"""Bounded data-free process-local monotonic measurements."""
import json
import math
import time
from contextlib import contextmanager
from pathlib import Path

PHASES = {'conversion', 'startup', 'scene_build',
          'rendering', 'artifact_preparation', 'delivery'}
HOPS = {'controller', 'worker', 'client', 'converter'}
STATUSES = {'executed', 'skipped', 'cached', 'unavailable', 'interrupted'}


def sanitize(records):
    result = []
    if not isinstance(records, list):
        return result
    for row in records[-256:]:
        if not isinstance(row, dict):
            continue
        duration, attempt = row.get('duration_ms'), row.get('attempt')
        if (any(not isinstance(row.get(key), str) for key in ('phase', 'hop', 'status'))
                or row.get('phase') not in PHASES or row.get('hop') not in HOPS or row.get('status') not in STATUSES
                or type(duration) not in (int, float) or not math.isfinite(duration) or not 0 <= duration <= 1e12
                or type(attempt) is not int or not 1 <= attempt <= 1000000):
            continue
        result.append({key: row[key] for key in ('phase', 'hop', 'status', 'duration_ms', 'attempt')})
    return result


def record(phase, hop, seconds=0, status='executed', attempt=1):
    return sanitize([dict(phase=phase, hop=hop, duration_ms=round(seconds*1000, 3), status=status, attempt=attempt)])[0]


@contextmanager
def measure(sink, phase, hop, attempt=1):
    start = time.monotonic()
    status = 'executed'
    try:
        yield
    except BaseException:
        status = 'interrupted'
        raise
    finally:
        sink(record(phase, hop, time.monotonic()-start, status, attempt))


def timing_path(manifest):
    return Path(manifest).with_name('operation-timings.json')


def write_timings(manifest, records):
    timing_path(manifest).write_text(json.dumps(sanitize(records)))


def read_timings(manifest):
    try:
        return sanitize(json.loads(timing_path(manifest).read_text()))
    except FileNotFoundError:
        return []
