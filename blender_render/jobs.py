"""Durable FIFO jobs, ownership, quotas, and conservative compute reservations.

Exactly one service process owns this database. On Modal the database lives on
its own Volume; ``sync`` commits the Volume before any paid work is dispatched.
Never mount this database in render workers or run overlapping deployments.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


class JobError(ValueError):
    def __init__(self, message: str, code: str = "invalid_request", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Policy:
    public: bool = False
    budget_usd: float = 450.0
    daily_jobs: int = 10
    quota_exempt_owners: tuple[str, ...] = ()
    max_job_seconds: int = 600
    startup_seconds: int = 180
    # L4 + four CPU cores + 8 GiB, rounded up with a safety margin. This is
    # deliberately a conservative reservation, not a claim of billed cost.
    usd_per_second: float = 0.0004
    expires_at: float = 1806451200.0  # 2027-03-31 00:00 UTC, conservative grant expiry
    ttl_seconds: int = 900
    input_ttl_seconds: int = 300
    max_upload_bytes: int = 50 * 1024 * 1024
    max_atoms: int = 100000
    max_storage_bytes: int = 10 * 1024**3
    max_queue: int = 100

    def reserve(self) -> int:
        return math.ceil((self.max_job_seconds + self.startup_seconds) * self.usd_per_second * 1_000_000) if self.public else 0


class JobStore:
    def __init__(self, root: Path, policy: Policy | None = None, *, data_root: Path | None = None, sync: Callable[[], None] | None = None, clock=time.time):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        transient = Path(data_root) if data_root else self.root
        self.inputs = transient / "inputs"
        self.artifacts = transient / "artifacts"
        self.inputs.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.policy = policy or Policy()
        self.session_id = os.environ.get("BLENDER_RENDER_SESSION_ID") or secrets.token_hex(12)
        self.clock = clock
        self.sync = sync or (lambda: None)
        self.lock = threading.RLock()
        self.healthy = True
        self.db = sqlite3.connect(str(self.root / "jobs.sqlite3"), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inputs (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL,
          expires REAL NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, input_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, options TEXT NOT NULL,
          state TEXT NOT NULL, submitted REAL NOT NULL, started REAL, finished REAL,
          reserved INTEGER NOT NULL, charged INTEGER NOT NULL DEFAULT 0,
          gpu_seconds REAL NOT NULL DEFAULT 0, remote_id TEXT,
          cancel_requested INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT,
          UNIQUE(owner, idempotency_key));
        CREATE INDEX IF NOT EXISTS job_queue ON jobs(state, submitted);
        CREATE TABLE IF NOT EXISTS operations (
          id INTEGER PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL,
          amount INTEGER NOT NULL, note TEXT NOT NULL);
        """)
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}
        for column in ('session_id','submitted_by'):
            if column not in columns:
                self.db.execute('ALTER TABLE jobs ADD COLUMN ' + column + ' TEXT')
        if 'rendered' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN rendered INTEGER NOT NULL DEFAULT 0')
            self.db.execute("UPDATE jobs SET rendered=1 WHERE state='done'")
        if 'client_request_hash' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN client_request_hash TEXT')
        if 'result_expires' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN result_expires REAL')
            # Preserve existing download deadlines; new jobs use the short
            # delivery window. Upgrades do not purge completed renders.
            self.db.execute("UPDATE jobs SET result_expires=finished+86400 WHERE result IS NOT NULL")
        input_columns = {row[1] for row in self.db.execute('PRAGMA table_info(inputs)')}
        for column in ('content_sha256', 'source_key'):
            if column not in input_columns:
                self.db.execute('ALTER TABLE inputs ADD COLUMN ' + column + ' TEXT')
        with self.transaction() as db:
            # Never replenish a lifetime budget when a deployment restarts.
            db.execute("INSERT OR IGNORE INTO settings VALUES ('budget_micro', ?)", (str(math.floor(self.policy.budget_usd * 1_000_000)),))
            db.execute("INSERT OR IGNORE INTO settings VALUES ('policy', ?)", (json.dumps(asdict(self.policy)),))
            db.execute("INSERT OR IGNORE INTO settings VALUES ('admission', 'open')")
            for row in db.execute("SELECT id FROM inputs WHERE content_sha256 IS NULL").fetchall():
                path = self.input_path(row['id'])
                if path.is_file():
                    digest = self._content_digest(json.loads(path.read_text()))
                    db.execute("UPDATE inputs SET content_sha256=? WHERE id=?", (digest, row['id']))

    @contextmanager
    def transaction(self):
        with self.lock:
            if not self.healthy:
                raise JobError("Persistent state is unavailable; compute admission is closed.", "state_unavailable", 503)
            changes = self.db.total_changes
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
            try:
                if self.db.total_changes != changes:
                    self.sync()
            except BaseException:
                self.healthy = False
                raise

    def _usage(self, db) -> tuple[int, int, int]:
        cap = min(int(db.execute("SELECT value FROM settings WHERE key='budget_micro'").fetchone()[0]), math.floor(self.policy.budget_usd * 1_000_000))
        spent, reserved = db.execute("SELECT COALESCE(SUM(charged),0), COALESCE(SUM(CASE WHEN state IN ('queued','running') THEN reserved ELSE 0 END),0) FROM jobs").fetchone()
        spent += db.execute("SELECT COALESCE(SUM(amount),0) FROM operations").fetchone()[0]
        return cap, int(spent), int(reserved)

    def _admit(self, db, amount: int, *, dispatch: bool = False):
        if not dispatch and db.execute("SELECT value FROM settings WHERE key='admission'").fetchone()[0] != "open":
            raise JobError("Service is draining. Please retry later or use your own compute.", "admission_closed", 503)
        if self.policy.public:
            if self.clock() >= self.policy.expires_at:
                raise JobError("The free compute grant has expired. Connect your own compute.", "credits_expired", 402)
            cap, spent, reserved = self._usage(db)
            if spent + reserved + amount > cap:
                raise JobError("Free compute credits are exhausted. Connect your own compute.", "budget_exhausted", 402)

    def quota(self, owner: str) -> dict:
        now = self.clock()
        day = math.floor(now / 86400) * 86400
        with self.lock:
            cap, spent, reserved = self._usage(self.db)
            count = self.db.execute("SELECT COUNT(*) FROM jobs WHERE owner=? AND submitted>=? AND (rendered=1 OR state IN ('queued','running'))", (owner, day)).fetchone()[0]
            retained, next_expiry = self.db.execute("SELECT COUNT(*),MIN(expires) FROM inputs WHERE owner=? AND expires>?", (owner, now)).fetchone()
        limited = self.policy.public and owner not in self.policy.quota_exempt_owners
        return {"public": self.policy.public, "budget_remaining_usd": max(0, cap - spent - reserved) / 1_000_000 if self.policy.public else None,
                "estimated_spent_usd": spent / 1_000_000, "reserved_usd": reserved / 1_000_000,
                "renders_remaining_today": max(0, self.policy.daily_jobs - count) if limited else None,
                "daily_render_limit": self.policy.daily_jobs if limited else None,
                "renders_completed_or_reserved_today": count,
                "testing_quota_exempt": owner in self.policy.quota_exempt_owners,
                "gpu_seconds_remaining_today": None,
                "pending_uploads": retained,
                "retained_structure_limit": None,
                "upload_slots_remaining": None,
                "conversions_remaining_today": None,
                "next_structure_expiry_at": next_expiry,
                "daily_quota_resets_at": day + 86400 if self.policy.public else None,
                "credits_expire_at": self.policy.expires_at if self.policy.public else None,
                "accounting": "conservative estimates; Modal workspace cap is the final billing limit"}

    def charge_operation(self, owner: str, amount_usd: float, note: str):
        if not math.isfinite(amount_usd) or amount_usd < 0:
            raise ValueError("Charges must be nonnegative and finite")
        amount = math.ceil(amount_usd * 1_000_000) if self.policy.public else 0
        with self.transaction() as db:
            self._admit(db, amount)
            db.execute("INSERT INTO operations(owner,created,amount,note) VALUES (?,?,?,?)", (owner, self.clock(), amount, note))

    def reserve_conversion(self, owner: str):
        """Reserve sandbox cost without imposing an upload-count quota."""
        with self.transaction() as db:
            amount = 10000 if self.policy.public else 0
            self._admit(db, amount)
            db.execute("INSERT INTO operations(owner,created,amount,note) VALUES (?,?,?,'conversion')", (owner, self.clock(), amount))

    def input_path(self, identifier):
        path = self.inputs / (identifier + '.json')
        legacy = self.root / 'inputs' / (identifier + '.json')
        return path if path.exists() or not legacy.exists() else legacy

    def artifact_root(self, identifier):
        path = self.artifacts / identifier
        legacy = self.root / 'artifacts' / identifier
        return path if path.exists() or not legacy.exists() else legacy
    @staticmethod
    def _content_digest(payload: dict) -> str:
        # Export timestamps and run IDs do not change the structure or pose.
        # Every other field, including camera, colors and DSS, remains hashed.
        content = {k: v for k, v in payload.items() if k not in {'generated_at_utc', 'run_id'}}
        return hashlib.sha256(json.dumps(content, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def _input_info(self, row, *, reused: bool = False) -> dict:
        path = self.input_path(row['id'])
        payload = json.loads(path.read_text()) if path.is_file() else {}
        return {"input_id": row['id'], "name": row['name'], "created_at": row['created'],
                "expires_at": row['expires'], "sha256": row['sha256'], "size_bytes": row['size'],
                "summary": payload.get('structure_summary', {}), "reused": reused}

    def find_source(self, owner: str, source_key: str) -> dict | None:
        """Only server-computed file digests can bypass another conversion."""
        with self.lock:
            row = self.db.execute("SELECT * FROM inputs WHERE owner=? AND source_key=? AND expires>? ORDER BY created,id LIMIT 1", (owner, source_key, self.clock())).fetchone()
            return self._input_info(row, reused=True) if row else None

    def list_inputs(self, owner: str) -> dict:
        with self.lock:
            rows = self.db.execute("SELECT * FROM inputs WHERE owner=? ORDER BY created,id", (owner,)).fetchall()
            items = []
            for row in rows:
                item = self._input_info(row)
                item.pop('reused')
                item['reusable'] = row['expires'] > self.clock()
                item['jobs'] = [dict(j) for j in self.db.execute("SELECT id AS job_id,state AS status FROM jobs WHERE input_id=? AND owner=? ORDER BY submitted DESC", (row['id'], owner))]
                item['in_use'] = any(j['status'] in {'queued', 'running'} for j in item['jobs'])
                items.append(item)
            return {"structures": items, "quota": self.quota(owner)}

    def delete_input(self, owner: str, identifier: str) -> dict:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM inputs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                # The response does not disclose another user's input IDs.
                return {"input_id": identifier, "deleted": False}
            if db.execute("SELECT 1 FROM jobs WHERE input_id=? AND state IN ('queued','running')", (identifier,)).fetchone():
                raise JobError("This structure is used by an active render. Wait for completion or cancel that job first.", "input_in_use", 409)
            self.input_path(identifier).unlink(missing_ok=True)
            db.execute("DELETE FROM inputs WHERE id=? AND owner=?", (identifier, owner))
        return {"input_id": identifier, "deleted": True}

    def add_input(self, owner: str, payload: dict, name: str = "structure.cif", *, source_key: str | None = None) -> dict:
        data = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        if len(data) > self.policy.max_upload_bytes:
            raise JobError("Structure exceeds the upload size limit.", "too_large", 413)
        now = self.clock()
        identifier = secrets.token_hex(16)
        path = self.inputs / (identifier + ".json")
        digest = self._content_digest(payload)
        with self.transaction() as db:
            existing = db.execute("SELECT * FROM inputs WHERE owner=? AND content_sha256=? AND expires>? ORDER BY created,id LIMIT 1", (owner, digest, now)).fetchone()
            if existing:
                if source_key:
                    db.execute("UPDATE inputs SET source_key=? WHERE id=?", (source_key, existing['id']))
                return self._input_info(existing, reused=True)
            self._admit(db, 0)
            if self.policy.public:
                size = db.execute("SELECT COALESCE(SUM(size),0) FROM inputs WHERE expires>?", (now,)).fetchone()[0]
                size += db.execute("SELECT COALESCE(SUM(json_extract(result,'$.bytes')),0) FROM jobs WHERE result IS NOT NULL").fetchone()[0]
                if size + len(data) > self.policy.max_storage_bytes:
                    raise JobError("Upload capacity is temporarily full.", "storage_full", 503)
            path.write_bytes(data)
            path.chmod(0o600)
            db.execute("INSERT INTO inputs(id,owner,created,expires,size,sha256,name,content_sha256,source_key) VALUES (?,?,?,?,?,?,?,?,?)", (identifier, owner, now, now + self.policy.input_ttl_seconds, len(data), hashlib.sha256(data).hexdigest(), Path(name).name[:160], digest, source_key))
            return self._input_info(db.execute("SELECT * FROM inputs WHERE id=?", (identifier,)).fetchone())

    def input(self, owner: str, identifier: str) -> dict:
        if not isinstance(identifier, str):
            raise JobError("input_id must be a string")
        with self.lock:
            row = self.db.execute("SELECT * FROM inputs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                raise JobError("Temporary structure is unavailable; upload it again.", "not_found", 404)
            active = self.db.execute("SELECT 1 FROM jobs WHERE input_id=? AND state IN ('queued','running')", (identifier,)).fetchone()
            if row["expires"] <= self.clock() and not active:
                raise JobError("Temporary structure has expired; upload it again.", "expired", 410)
            path = self.input_path(row['id'])
            if not path.is_file():
                raise JobError("Temporary structure was lost on restart; upload it again.", "expired", 410)
            return json.loads(path.read_text())

    def submission_replay(self, owner, input_id, key, client_hash):
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise JobError("Provide an idempotency key of 1 to 128 characters.")
        if not isinstance(input_id, str):
            raise JobError("input_id must be a string")
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE owner=? AND idempotency_key=?", (owner, key)).fetchone()
            if row and row['client_request_hash']:
                if row['input_id'] != input_id or row['client_request_hash'] != client_hash:
                    raise JobError("Idempotency key was already used for a different request.", "idempotency_conflict", 409)
                return self._public_job(row)
            return None

    def submit(self, owner: str, input_id: str, options: dict, idempotency_key: str, *, client_hash: str | None = None) -> dict:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
            raise JobError("Provide an idempotency key of 1 to 128 characters.")
        encoded = json.dumps(options, sort_keys=True, allow_nan=False, separators=(",", ":"))
        request_hash = hashlib.sha256((input_id + encoded).encode()).hexdigest()
        now = self.clock()
        identifier = secrets.token_hex(16)
        with self.transaction() as db:
            existing = db.execute("SELECT * FROM jobs WHERE owner=? AND idempotency_key=?", (owner, idempotency_key)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise JobError("Idempotency key was already used for a different request.", "idempotency_conflict", 409)
                return self._public_job(existing)
            self.input(owner, input_id)
            reservation = self.policy.reserve()
            self._admit(db, reservation)
            if db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= self.policy.max_queue:
                raise JobError("Render queue is full; try again later.", "queue_full", 429)
            if self.policy.public:
                day = math.floor(now / 86400) * 86400
                count = db.execute("SELECT COUNT(*) FROM jobs WHERE owner=? AND submitted>=? AND (rendered=1 OR state IN ('queued','running'))", (owner, day)).fetchone()[0]
                if owner not in self.policy.quota_exempt_owners and count >= self.policy.daily_jobs:
                    raise JobError("Your daily free render quota is used up.", "daily_quota", 429)
            db.execute("INSERT INTO jobs(id,owner,input_id,idempotency_key,request_hash,options,state,submitted,reserved) VALUES (?,?,?,?,?,?,'queued',?,?)", (identifier, owner, input_id, idempotency_key, request_hash, encoded, now, reservation))
            db.execute("UPDATE jobs SET session_id=?,submitted_by=?,client_request_hash=? WHERE id=?", (self.session_id,owner,client_hash,identifier))
        return self.get(owner, identifier)

    def _public_job(self, row) -> dict:
        position = 0
        if row["state"] == "queued":
            position = self.db.execute("SELECT COUNT(*) FROM jobs WHERE state='queued' AND (submitted<? OR (submitted=? AND rowid <= (SELECT rowid FROM jobs WHERE id=?)))", (row["submitted"], row["submitted"], row["id"])).fetchone()[0]
        return {"job_id": row["id"], "status": row["state"], "queue_position": position,
                "session_id": row["session_id"], "submitted_by": row["submitted_by"],
                "submitted_at": row["submitted"], "started_at": row["started"], "finished_at": row["finished"],
                "cancel_requested": bool(row["cancel_requested"]), "error": row["error"],
                "estimated_cost_usd": row["charged"] / 1_000_000,
                "expires_at": row['result_expires']}

    def get(self, owner: str, identifier: str) -> dict:
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                raise JobError("Job not found.", "not_found", 404)
            return self._public_job(row)

    def result(self, owner: str, identifier: str) -> dict:
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                raise JobError("Job not found.", "not_found", 404)
            if row["state"] in {"expired", "delivered", "deleted"}:
                raise JobError("Render files have expired or been deleted after delivery or a deletion request.", "expired", 410)
            if row["state"] != "done":
                raise JobError("Render is not complete.", "not_ready", 409)
            if row['result_expires'] is not None and row['result_expires'] <= self.clock():
                raise JobError("Render artifacts have expired.", "expired", 410)
            data = json.loads(row["result"])
            if 'manifest' not in data:
                manifest = self.artifact_root(identifier) / 'manifest.json'
                data['manifest'] = json.loads(manifest.read_text()) if manifest.is_file() else {}
            return data

    def delete_result(self, owner: str, identifier: str) -> dict:
        """Remove a completed result without refunding render or cost usage."""
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                raise JobError("Job not found.", "not_found", 404)
            if row['state'] in {'queued', 'running'}:
                raise JobError("Cancel the active render before deleting its files.", "job_in_use", 409)
            # Check both locations so a pre-privacy release's files are also
            # removed. Job IDs come from the owner-checked database record.
            roots = {self.artifacts / row['id'], self.root / 'artifacts' / row['id']}
            files = byte_count = 0
            for root in roots:
                if root.exists():
                    for path in root.rglob('*'):
                        if path.is_file():
                            files += 1
                            byte_count += path.stat().st_size
                    shutil.rmtree(root)
            deleted = bool(files or row['result'])
            # Older results embedded full provenance in SQLite. Clear it and
            # render options; secure_delete also removes their freed cells.
            db.execute("UPDATE jobs SET result=NULL,options='{}',state='deleted',result_expires=NULL WHERE id=?", (identifier,))
        return {'job_id': identifier, 'deleted': deleted, 'files_deleted': files, 'bytes_deleted': byte_count}

    def claim(self) -> dict | None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY submitted,rowid LIMIT 1").fetchone()
            if not row:
                return None
            # A durable admission reservation already exists. Expiration or an
            # operator drain also prevents starting previously queued work.
            try:
                self._admit(db, 0, dispatch=True)
            except JobError:
                return None
            db.execute("UPDATE jobs SET state='running',started=? WHERE id=?", (self.clock(), row["id"]))
            return dict(row)

    def set_remote_id(self, identifier: str, remote_id: str):
        with self.transaction() as db:
            db.execute("UPDATE jobs SET remote_id=? WHERE id=?", (remote_id, identifier))

    def cancelled(self, identifier: str) -> bool:
        with self.lock:
            row = self.db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (identifier,)).fetchone()
            return bool(row and row[0])

    def cancel(self, owner: str, identifier: str) -> dict:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                raise JobError("Job not found.", "not_found", 404)
            if row["state"] == "queued":
                db.execute("UPDATE jobs SET state='cancelled',cancel_requested=1,finished=?,options='{}' WHERE id=?", (self.clock(), identifier))
            elif row["state"] == "running":
                db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (identifier,))
            self._release_finished_inputs(db)
        return self.get(owner, identifier)

    def finish(self, identifier: str, *, result: dict | None = None, error: str | None = None, seconds: float = 0):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            if not row or row["state"] != "running":
                return
            state = "cancelled" if row["cancel_requested"] else ("failed" if error else "done")
            seconds = max(0, min(float(seconds), self.policy.max_job_seconds))
            charged = min(row["reserved"], math.ceil((seconds + self.policy.startup_seconds) * self.policy.usd_per_second * 1_000_000)) if self.policy.public else 0
            record = {k: v for k, v in (result or {}).items() if k != 'manifest'}
            db.execute("UPDATE jobs SET state=?,finished=?,charged=?,gpu_seconds=?,result=?,error=?,rendered=?,result_expires=?,options='{}' WHERE id=?", (state, self.clock(), charged, seconds, json.dumps(record) if state == "done" else None, error, int(state == 'done'), self.clock()+self.policy.ttl_seconds if state=='done' else None, identifier))
            self._release_finished_inputs(db)

    def _release_finished_inputs(self, db):
        rows = db.execute("SELECT id FROM inputs WHERE id IN (SELECT input_id FROM jobs) AND id NOT IN (SELECT input_id FROM jobs WHERE state IN ('queued','running'))").fetchall()
        for row in rows:
            self.input_path(row['id']).unlink(missing_ok=True)
            db.execute("DELETE FROM inputs WHERE id=?", (row['id'],))

    def artifact_delivered(self, owner: str, identifier: str, name: str):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row or row['state'] != 'done' or not row['result']:
                return
            result = json.loads(row['result'])
            artifact = next((a for a in result['artifacts'] if a['name'] == name), None)
            if not artifact:
                return
            (self.artifact_root(identifier) / name).unlink(missing_ok=True)
            result['artifacts'] = [a for a in result['artifacts'] if a['name'] != name]
            result['bytes'] = sum(a['size'] for a in result['artifacts'])
            if result['artifacts']:
                db.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(result), identifier))
            else:
                shutil.rmtree(self.artifact_root(identifier), ignore_errors=True)
                db.execute("UPDATE jobs SET result=NULL,state='delivered' WHERE id=?", (identifier,))

    def recover_interrupted(self):
        with self.transaction() as db:
            # Never retry an unknown paid execution or refund its reservation.
            db.execute("UPDATE jobs SET state='failed',finished=?,charged=reserved,gpu_seconds=?,options='{}',error='Service interrupted. Execution was not retried; submit a new request.' WHERE state='running'", (self.clock(), self.policy.max_job_seconds))
            for row in db.execute("SELECT id,input_id FROM jobs WHERE state='queued'").fetchall():
                if not self.input_path(row['input_id']).is_file():
                    db.execute("UPDATE jobs SET state='failed',finished=?,options='{}',error='Temporary input lost on restart; upload again.' WHERE id=?", (self.clock(), row['id']))
            self._release_finished_inputs(db)

    def drain(self):
        with self.transaction() as db:
            db.execute("UPDATE settings SET value='draining' WHERE key='admission'")

    def resume(self):
        with self.transaction() as db:
            db.execute("UPDATE settings SET value='open' WHERE key='admission'")

    def cancel_pending(self):
        """Close queued work when operating funds no longer cover execution."""
        with self.transaction() as db:
            db.execute("UPDATE jobs SET state='cancelled',cancel_requested=1,finished=?,options='{}',error='Service operating budget exhausted' WHERE state='queued'", (self.clock(),))
            self._release_finished_inputs(db)

    def cleanup(self):
        now = self.clock()
        with self.transaction() as db:
            self._release_finished_inputs(db)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='oauth'").fetchone():
                db.execute("DELETE FROM oauth WHERE expires IS NOT NULL AND expires<=?", (now,))
            rows = db.execute("SELECT id FROM inputs WHERE expires<=? AND id NOT IN (SELECT input_id FROM jobs WHERE state IN ('queued','running'))", (now,)).fetchall()
            for row in rows:
                self.input_path(row['id']).unlink(missing_ok=True)
                db.execute("DELETE FROM inputs WHERE id=?", (row["id"],))
            rows = db.execute("SELECT id FROM jobs WHERE result_expires IS NOT NULL AND result_expires<=? AND state NOT IN ('expired','delivered')", (now,)).fetchall()
            for row in rows:
                shutil.rmtree(self.artifact_root(row['id']), ignore_errors=True)
                db.execute("UPDATE jobs SET result=NULL,options='{}',state='expired' WHERE id=?", (row["id"],))

    def close(self):
        with self.lock:
            self.db.close()
