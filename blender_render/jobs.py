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
    daily_jobs: int = 5
    daily_gpu_seconds: int = 1200
    max_job_seconds: int = 600
    startup_seconds: int = 180
    # L4 + four CPU cores + 8 GiB, rounded up with a safety margin. This is
    # deliberately a conservative reservation, not a claim of billed cost.
    usd_per_second: float = 0.0004
    expires_at: float = 1806451200.0  # 2027-03-31 00:00 UTC, conservative grant expiry
    ttl_seconds: int = 86400
    max_upload_bytes: int = 50 * 1024 * 1024
    max_atoms: int = 100000
    max_storage_bytes: int = 10 * 1024**3
    max_queue: int = 100

    def reserve(self) -> int:
        return math.ceil((self.max_job_seconds + self.startup_seconds) * self.usd_per_second * 1_000_000) if self.public else 0


class JobStore:
    def __init__(self, root: Path, policy: Policy | None = None, *, sync: Callable[[], None] | None = None, clock=time.time):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.inputs = self.root / "inputs"
        self.artifacts = self.root / "artifacts"
        self.inputs.mkdir(exist_ok=True)
        self.artifacts.mkdir(exist_ok=True)
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
        with self.transaction() as db:
            # Never replenish a lifetime budget when a deployment restarts.
            db.execute("INSERT OR IGNORE INTO settings VALUES ('budget_micro', ?)", (str(math.floor(self.policy.budget_usd * 1_000_000)),))
            db.execute("INSERT OR IGNORE INTO settings VALUES ('policy', ?)", (json.dumps(asdict(self.policy)),))
            db.execute("INSERT OR IGNORE INTO settings VALUES ('admission', 'open')")

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
            count, seconds = self.db.execute("SELECT COUNT(*),COALESCE(SUM(gpu_seconds + CASE WHEN state IN ('queued','running') THEN ? ELSE 0 END),0) FROM jobs WHERE owner=? AND submitted>=?", (self.policy.max_job_seconds, owner, day)).fetchone()
        return {"public": self.policy.public, "budget_remaining_usd": max(0, cap - spent - reserved) / 1_000_000 if self.policy.public else None,
                "estimated_spent_usd": spent / 1_000_000, "reserved_usd": reserved / 1_000_000,
                "renders_remaining_today": max(0, self.policy.daily_jobs - count) if self.policy.public else None,
                "gpu_seconds_remaining_today": max(0, self.policy.daily_gpu_seconds - seconds) if self.policy.public else None,
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
        """Charge a bounded sandbox attempt before downloading even malformed input."""
        with self.transaction() as db:
            day = math.floor(self.clock() / 86400) * 86400
            count = db.execute("SELECT COUNT(*) FROM operations WHERE owner=? AND created>=? AND note='conversion'", (owner, day)).fetchone()[0]
            if self.policy.public and count >= 10:
                raise JobError("Daily upload/conversion limit reached.", "upload_quota", 429)
            amount = 10000 if self.policy.public else 0
            self._admit(db, amount)
            db.execute("INSERT INTO operations(owner,created,amount,note) VALUES (?,?,?,'conversion')", (owner, self.clock(), amount))

    def add_input(self, owner: str, payload: dict, name: str = "structure.cif") -> dict:
        data = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        if len(data) > self.policy.max_upload_bytes:
            raise JobError("Structure exceeds the upload size limit.", "too_large", 413)
        now = self.clock()
        identifier = secrets.token_hex(16)
        path = self.inputs / (identifier + ".json")
        with self.transaction() as db:
            self._admit(db, 0)
            if self.policy.public:
                count = db.execute("SELECT COUNT(*) FROM inputs WHERE owner=? AND expires>?", (owner, now)).fetchone()[0]
                if count >= 10:
                    raise JobError("At most ten uploaded structures are retained per account.", "upload_quota", 429)
                size = db.execute("SELECT COALESCE(SUM(size),0) FROM inputs WHERE expires>?", (now,)).fetchone()[0]
                size += db.execute("SELECT COALESCE(SUM(json_extract(result,'$.bytes')),0) FROM jobs WHERE result IS NOT NULL").fetchone()[0]
                if size + len(data) > self.policy.max_storage_bytes:
                    raise JobError("Upload capacity is temporarily full.", "storage_full", 503)
            path.write_bytes(data)
            path.chmod(0o600)
            db.execute("INSERT INTO inputs VALUES (?,?,?,?,?,?,?)", (identifier, owner, now, now + self.policy.ttl_seconds, len(data), hashlib.sha256(data).hexdigest(), Path(name).name[:160]))
        return {"input_id": identifier, "expires_at": now + self.policy.ttl_seconds, "sha256": hashlib.sha256(data).hexdigest(), "summary": payload.get("structure_summary", {})}

    def input(self, owner: str, identifier: str) -> dict:
        if not isinstance(identifier, str):
            raise JobError("input_id must be a string")
        with self.lock:
            row = self.db.execute("SELECT * FROM inputs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
        if not row:
            raise JobError("Structure not found.", "not_found", 404)
        if row["expires"] <= self.clock():
            raise JobError("Structure has expired; upload it again.", "expired", 410)
        return json.loads((self.inputs / (row["id"] + ".json")).read_text())

    def submit(self, owner: str, input_id: str, options: dict, idempotency_key: str) -> dict:
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
                if db.execute("SELECT 1 FROM jobs WHERE owner=? AND state IN ('queued','running')", (owner,)).fetchone():
                    raise JobError("You already have an active render.", "active_job", 429)
                day = math.floor(now / 86400) * 86400
                count, seconds = db.execute("SELECT COUNT(*),COALESCE(SUM(gpu_seconds),0) FROM jobs WHERE owner=? AND submitted>=?", (owner, day)).fetchone()
                if count >= self.policy.daily_jobs or seconds + self.policy.max_job_seconds > self.policy.daily_gpu_seconds:
                    raise JobError("Your daily free render quota is used up.", "daily_quota", 429)
            db.execute("INSERT INTO jobs(id,owner,input_id,idempotency_key,request_hash,options,state,submitted,reserved) VALUES (?,?,?,?,?,?,'queued',?,?)", (identifier, owner, input_id, idempotency_key, request_hash, encoded, now, reservation))
            db.execute("UPDATE jobs SET session_id=?,submitted_by=? WHERE id=?", (self.session_id,owner,identifier))
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
                "expires_at": (row["finished"] + self.policy.ttl_seconds) if row["finished"] else None}

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
            if row["state"] == "expired":
                raise JobError("Render artifacts have expired.", "expired", 410)
            if row["state"] != "done":
                raise JobError("Render is not complete.", "not_ready", 409)
            if row["finished"] + self.policy.ttl_seconds <= self.clock():
                raise JobError("Render artifacts have expired.", "expired", 410)
            return json.loads(row["result"])

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
                db.execute("UPDATE jobs SET state='cancelled',cancel_requested=1,finished=? WHERE id=?", (self.clock(), identifier))
            elif row["state"] == "running":
                db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (identifier,))
        return self.get(owner, identifier)

    def finish(self, identifier: str, *, result: dict | None = None, error: str | None = None, seconds: float = 0):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            if not row or row["state"] != "running":
                return
            state = "cancelled" if row["cancel_requested"] else ("failed" if error else "done")
            seconds = max(0, min(float(seconds), self.policy.max_job_seconds))
            charged = min(row["reserved"], math.ceil((seconds + self.policy.startup_seconds) * self.policy.usd_per_second * 1_000_000)) if self.policy.public else 0
            db.execute("UPDATE jobs SET state=?,finished=?,charged=?,gpu_seconds=?,result=?,error=? WHERE id=?", (state, self.clock(), charged, seconds, json.dumps(result) if state == "done" else None, error, identifier))

    def recover_interrupted(self):
        with self.transaction() as db:
            # Never retry an unknown paid execution or refund its reservation.
            db.execute("UPDATE jobs SET state='failed',finished=?,charged=reserved,gpu_seconds=?,error='Service interrupted. Execution was not retried; submit a new request.' WHERE state='running'", (self.clock(), self.policy.max_job_seconds))

    def drain(self):
        with self.transaction() as db:
            db.execute("UPDATE settings SET value='draining' WHERE key='admission'")

    def resume(self):
        with self.transaction() as db:
            db.execute("UPDATE settings SET value='open' WHERE key='admission'")

    def cancel_pending(self):
        """Close queued work when operating funds no longer cover execution."""
        with self.transaction() as db:
            db.execute("UPDATE jobs SET state='cancelled',cancel_requested=1,finished=?,error='Service operating budget exhausted' WHERE state='queued'", (self.clock(),))

    def cleanup(self):
        now = self.clock()
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='oauth'").fetchone():
                db.execute("DELETE FROM oauth WHERE expires IS NOT NULL AND expires<=?", (now,))
            rows = db.execute("SELECT id FROM inputs WHERE expires<=? AND id NOT IN (SELECT input_id FROM jobs WHERE state IN ('queued','running'))", (now,)).fetchall()
            for row in rows:
                (self.inputs / (row["id"] + ".json")).unlink(missing_ok=True)
                db.execute("DELETE FROM inputs WHERE id=?", (row["id"],))
            rows = db.execute("SELECT id FROM jobs WHERE finished IS NOT NULL AND finished<=? AND state!='expired'", (now - self.policy.ttl_seconds,)).fetchall()
            for row in rows:
                shutil.rmtree(self.artifacts / row["id"], ignore_errors=True)
                db.execute("UPDATE jobs SET result=NULL,state='expired' WHERE id=?", (row["id"],))

    def close(self):
        with self.lock:
            self.db.close()
