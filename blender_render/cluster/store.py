"""Durable private job spool. A shared filesystem with working flock is required."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import time

from .common import ARTIFACTS, TERMINAL, atomic_json, job_id, json_bytes, read_json, validate_request
from .slurm import SubmissionUnknown


class NotFound(ValueError):
    pass


class Conflict(ValueError):
    pass


class Quota(ValueError):
    pass


class Store:
    def __init__(self, config, scheduler, engine_build):
        self.config, self.scheduler, self.engine_build = config, scheduler, engine_build
        self.root = config.spool
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_uid != os.getuid() or self.root.stat().st_mode & 0o077:
            raise ValueError('Cluster spool must be owned by the service account and mode 0700')

    @contextmanager
    def locked(self):
        with (self.root / '.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def directory(self, identifier):
        path = self.root / job_id(identifier)
        if path.is_symlink():
            raise NotFound('Job not found')
        return path

    def owned(self, owner, identifier):
        path = self.directory(identifier) / 'job.json'
        if not path.is_file():
            raise NotFound('Job not found')
        record = read_json(path, 64 * 1024)
        if record['owner'] != owner:
            raise NotFound('Job not found')
        return record

    def save(self, record):
        atomic_json(self.directory(record['job_id']) / 'job.json', record)

    def refresh(self, record):
        directory = self.directory(record['job_id'])
        if record['status'] in TERMINAL:
            return record
        if record['status'] == 'submitting':
            record['status'] = 'submission_unknown'
            self.save(record)
        result = directory / 'result.json'
        if result.is_file() and record['status'] != 'cancelling':
            data = read_json(result, 64 * 1024)
            if data['status'] in ('completed', 'failed'):
                record.update(status=data['status'], finished_at=data['finished_at'])
                self.save(record)
                return record
        if record.get('slurm_id'):
            try:
                state = self.scheduler.state(record['slurm_id'])
            except (OSError, subprocess.SubprocessError):
                return record  # A scheduler outage is not a job failure.
            failed = {'FAILED', 'TIMEOUT', 'OUT_OF_MEMORY', 'NODE_FAIL', 'BOOT_FAIL',
                      'DEADLINE', 'PREEMPTED', 'REVOKED', 'SPECIAL_EXIT'}
            if state == 'COMPLETED' and record['status'] != 'cancelling':
                # Shared filesystems may expose the worker receipt after Slurm's
                # accounting update. Allow metadata propagation before failing.
                record.setdefault('completion_seen_at', time.time())
                if time.time() - record['completion_seen_at'] < 60:
                    record['status'] = 'finalizing'
                    self.save(record)
                    return record
            if state in failed or state == 'CANCELLED' or state == 'COMPLETED':
                status = 'cancelled' if state == 'CANCELLED' or record['status'] == 'cancelling' else 'failed'
                # Slurm completion without the worker's verified result is not success.
                record.update(status=status, finished_at=time.time(), scheduler_state=state)
            elif state in {'RUNNING', 'COMPLETING'} and record['status'] != 'cancelling':
                record['status'] = 'running'
            self.save(record)
        return record

    def public(self, record):
        result = {key: record[key] for key in ('job_id', 'status', 'created_at', 'slurm_id', 'finished_at') if key in record}
        result['engine_build'] = record['engine_build']
        if record['status'] == 'completed':
            receipt = read_json(self.directory(record['job_id']) / 'result.json', 64 * 1024)
            result['artifacts'] = {name: {k: item[k] for k in ('bytes', 'sha256')}
                                   for name, item in receipt['artifacts'].items()}
        if record['status'] == 'submission_unknown':
            result['message'] = 'Submission outcome unknown. Contact the administrator; do not submit again.'
        if record['status'] == 'failed':
            result['message'] = 'Render failed. The administrator can inspect the private job log.'
        return result

    def submit(self, owner, document):
        document = validate_request(document, self.config.limits)
        identifier = hashlib.sha256((owner + '\0' + document['request_id']).encode()).hexdigest()[:32]
        digest = hashlib.sha256(json_bytes(document)).hexdigest()
        with self.locked():
            directory = self.directory(identifier)
            if directory.exists():
                record = self.owned(owner, identifier)
                if record['request_sha256'] != digest:
                    raise Conflict('Request ID was already used for different input')
                return self.public(self.refresh(record))
            records = [self.refresh(read_json(p, 64 * 1024)) for p in self.root.glob('*/job.json')]
            limits = self.config.limits
            active = [r for r in records if r['status'] not in TERMINAL]
            if (len(active) >= limits.max_active_total or
                    sum(r['owner'] == owner for r in active) >= limits.max_active_per_user or
                    sum(r['owner'] == owner and r['status'] != 'expired' for r in records) >= limits.max_retained_per_user):
                raise Quota('Job quota reached; wait for completion or delete retained jobs')
            used = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file() and not p.is_symlink())
            if used + len(json_bytes(document)) > limits.max_spool_bytes:
                raise Quota('Server storage quota reached')
            directory.mkdir(mode=0o700)
            record = {'job_id': identifier, 'owner': owner, 'request_sha256': digest,
                      'status': 'submitting', 'created_at': time.time(), 'engine_build': self.engine_build}
            self.save(record)  # Persist before sbatch: a crash must never cause duplicate work.
            atomic_json(directory / 'request.json', {'request': document,
                'limits': vars(limits), 'engine_build': self.engine_build,
                'execution': {'gpus': self.config.gpus}})
            try:
                record.update(slurm_id=self.scheduler.submit(directory, identifier), status='queued')
            except SubmissionUnknown:
                record['status'] = 'submission_unknown'
            except (OSError, subprocess.SubprocessError) as exc:
                atomic_json(directory / 'submission-error.json', {
                    'type': type(exc).__name__, 'message': str(exc)[-4000:],
                    'stdout': str(getattr(exc, 'stdout', '') or '')[-4000:],
                    'stderr': str(getattr(exc, 'stderr', '') or '')[-4000:]})
                record.update(status='failed', finished_at=time.time())
            self.save(record)
            return self.public(record)

    def status(self, owner, identifier):
        with self.locked():
            record = self.owned(owner, identifier)
            # A gateway crash during sbatch is ambiguous, never a license to resubmit.
            if record['status'] == 'submitting':
                record['status'] = 'submission_unknown'
                self.save(record)
            return self.public(self.refresh(record))

    def list_jobs(self, owner):
        with self.locked():
            records = [read_json(p, 64 * 1024) for p in self.root.glob('*/job.json')]
            own = sorted((r for r in records if r['owner'] == owner),
                         key=lambda r: r['created_at'], reverse=True)[:100]
            return [self.public(self.refresh(r)) for r in own]

    def open_artifact(self, owner, identifier, name):
        with self.locked():
            record = self.refresh(self.owned(owner, identifier))
            if record['status'] != 'completed' or name not in ARTIFACTS:
                raise NotFound('Artifact not found')
            directory = self.directory(identifier)
            receipt = read_json(directory / 'result.json', 64 * 1024)
            metadata = receipt['artifacts'].get(name)
            if not metadata:
                raise NotFound('Artifact not found')
            path = (directory / metadata['path']).resolve()
            if not path.is_relative_to(directory / 'out') or not path.is_file():
                raise NotFound('Artifact not found')
            handle = path.open('rb')
            if os.fstat(handle.fileno()).st_size != metadata['bytes']:
                handle.close()
                raise Conflict('Artifact changed after completion')
            return handle, metadata

    def cancel(self, owner, identifier):
        with self.locked():
            record = self.refresh(self.owned(owner, identifier))
            if record['status'] not in TERMINAL:
                if not record.get('slurm_id'):
                    raise Conflict('Unknown submission needs administrator reconciliation before cancellation')
                self.scheduler.cancel(record['slurm_id'])
                (self.directory(identifier) / 'cancel.requested').touch(mode=0o600)
                record['status'] = 'cancelling'
                self.save(record)
            return self.public(record)

    def expire(self, record):
        directory = self.directory(record['job_id'])
        for path in directory.iterdir():
            if path.name == 'job.json':
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        # Retain only an ownership/idempotency tombstone, never structure data.
        record['status'] = 'expired'
        self.save(record)

    def delete(self, owner, identifier):
        with self.locked():
            record = self.refresh(self.owned(owner, identifier))
            if record['status'] not in TERMINAL:
                raise Conflict('Cancel and wait for a terminal state before deletion')
            self.expire(record)
            return self.public(record)

    def sweep(self):
        with self.locked():
            for path in self.root.glob('*/job.json'):
                record = self.refresh(read_json(path, 64 * 1024))
                if (record['status'] in TERMINAL - {'expired'} and
                    time.time() - record['finished_at'] > self.config.limits.retention_seconds):
                    self.expire(record)

    def reconcile(self, identifier):
        with self.locked():
            record = read_json(self.directory(identifier) / 'job.json', 64 * 1024)
            if record['status'] not in {'submitting', 'submission_unknown'}:
                return self.public(self.refresh(record))
            found = self.scheduler.find_submission(identifier, record['created_at'])
            if found:
                record.update(slurm_id=found, status='queued')
                self.save(record)
            return self.public(self.refresh(record))
