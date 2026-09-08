"""Private worker stage journal, independent of style JSON and molecular data."""
import json
import time
from pathlib import Path


def stage_path(manifest):
    return Path(manifest).with_name('operation-stages.json')


def record_stage(manifest, stage):
    if not manifest:
        return
    path = stage_path(manifest)
    history = json.loads(path.read_text()) if path.exists() else []
    if not history or history[-1]['stage'] != stage:
        history.append({'stage': stage, 'at': time.time()})
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(history))
        temporary.replace(path)


def read_stages(manifest):
    try:
        return json.loads(stage_path(manifest).read_text())
    except FileNotFoundError:
        return []
