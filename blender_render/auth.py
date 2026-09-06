"""GitHub OAuth with encrypted credentials in the same single-writer database."""
import json
import asyncio
from key_value.aio.stores.base import BaseStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from fastmcp.server.auth.providers.github import GitHubProvider


class SQLiteCredentials(BaseStore):
    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS oauth (collection TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, expires REAL, PRIMARY KEY(collection,key))")
        super().__init__(stable_api=True)

    async def _get_managed_entry(self, *, key, collection):
        with self.store.lock:
            row = self.store.db.execute("SELECT value,expires FROM oauth WHERE collection=? AND key=?", (collection,key)).fetchone()
        if not row or row[1] is not None and row[1] <= self.store.clock():
            return None
        return self._serialization_adapter.load_json(json_str=row[0])

    async def _put_managed_entry(self, *, key, collection, managed_entry):
        value = self._serialization_adapter.dump_json(entry=managed_entry, key=key, collection=collection)
        expires = managed_entry.expires_at.timestamp() if managed_entry.expires_at else None
        def put():
            with self.store.transaction() as db:
                db.execute("INSERT OR REPLACE INTO oauth VALUES (?,?,?,?)", (collection,key,value,expires))
        await asyncio.to_thread(put)

    async def _delete_managed_entry(self, *, key, collection):
        def delete():
            with self.store.transaction() as db:
                return db.execute("DELETE FROM oauth WHERE collection=? AND key=?", (collection,key)).rowcount > 0
        return await asyncio.to_thread(delete)


def github_provider(store, *, client_id, client_secret, base_url, encryption_key, jwt_key, allowed_users=None):
    if min(len(encryption_key), len(jwt_key)) < 32:
        raise ValueError("Persistent credential encryption and JWT signing keys are required")
    encrypted = FernetEncryptionWrapper(SQLiteCredentials(store), source_material=encryption_key, salt="blender-render-oauth-v1")
    class RestrictedGitHub(GitHubProvider):
        async def verify_token(self, token):
            access = await super().verify_token(token)
            if access and allowed_users is not None and str(access.claims.get('sub')) not in allowed_users:
                return None
            return access
    return RestrictedGitHub(client_id=client_id, client_secret=client_secret, base_url=base_url,
        required_scopes=["read:user"], client_storage=encrypted, jwt_signing_key=jwt_key,
        require_authorization_consent=True)
