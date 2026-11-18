# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender's main-thread request loop, owned by the optional local supervisor."""
import json
import os
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

package = Path(__file__).resolve().parent
sys.path[:0] = [str(package), str(package.parent)]
from mol_blender.blender_runtime import SnapshotRuntime
from mol_blender.warm_protocol import receive, send
from mol_blender import blender_custom_render as renderer

cfg = json.loads(Path(os.environ['MOL_BLENDER_DEV_CONFIG']).read_text())
stream = socket.socket(fileno=int(os.environ['MOL_BLENDER_WARM_FD']))
parent_pid = os.getppid()


def watch_parent():
    # A killed supervisor must not leave an orphan render holding the GPU.
    # bpy stays on the main thread; this watcher only observes process ownership.
    while True:
        time.sleep(1)
        if os.getppid() != parent_pid:
            os._exit(1)


threading.Thread(target=watch_parent, daemon=True).start()
runtime = None
try:
    runtime = SnapshotRuntime(cfg)
    send(stream, {'ready': True, 'pid': os.getpid(), 'source_sha256': cfg['identity']['sha256']})
    count = 0
    while True:
        command = receive(stream)
        if command['action'] == 'stop':
            break
        if command['action'] != 'render':
            raise ValueError('Unsupported Blender worker action')
        count += 1
        evidence = {'id': cfg['worker_id'], 'pid': os.getpid(), 'request_index': count,
                    'reused_process': count > 1, 'fresh_process': count == 1}
        started = time.monotonic()
        args = renderer._parse_argv(['blender', '--', *command['arguments']])
        runtime.render(args, warm_worker=evidence)
        send(stream, {**evidence, 'runtime_seconds': time.monotonic() - started})
except Exception as exc:
    traceback.print_exc()
    try:
        send(stream, {'error': str(exc)})
    except (OSError, ValueError):
        pass
finally:
    if runtime is not None:
        runtime.close()
    stream.close()
