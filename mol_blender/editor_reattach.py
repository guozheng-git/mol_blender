# SPDX-License-Identifier: GPL-3.0-or-later
"""Trusted external bootstrap for replacing a bridge in its selected desktop.

Not an IPC operation. The user or local agent explicitly runs the generated
attachment script in that window. It never loads a file or launches a process.
"""
import json
from pathlib import Path
import sys

import bpy


def reattach(config_path):
    cfg = json.loads(Path(config_path).read_text())
    candidates = [bpy.app.driver_namespace.get('mol_blender_editor_bridge'),
                  bpy.app.driver_namespace.get('mol_blender_editor_reconnecting')]
    for name in ('mol_blender.editor_blender', '__main__'):
        candidates.append(getattr(sys.modules.get(name), '_ACTIVE', None))
    # Compatibility with bridges started by --python before they published a
    # stable handle. Only the selected bridge's bound load handler is accepted.
    candidates.extend(getattr(handler, '__self__', None) for handler in bpy.app.handlers.load_pre)
    old = next((value for value in candidates if value is not None
                and type(value).__name__ == 'Bridge'
                and getattr(value, 'cfg', {}).get('id') == cfg['id']), None)
    if old is None or old.scene != bpy.context.scene:
        raise ValueError('This is not the selected editor session. Run the script in its original window; nothing was changed.')
    # Validate the fresh capture before disconnecting the live bridge.
    import hashlib
    source = Path(config_path).parent / 'source' / 'mol_blender'
    for name, expected in cfg['source_files'].items():
        path = source / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Prepared reconnect source changed; prepare another script')
    bpy.app.driver_namespace['mol_blender_editor_reconnecting'] = old
    old.stop()
    for name in list(sys.modules):
        if name == 'mol_blender' or name.startswith('mol_blender.'):
            del sys.modules[name]
    sys.path.insert(0, str(source.parent))
    from mol_blender.editor_blender import start
    result = start(config_path)
    bpy.app.driver_namespace.pop('mol_blender_editor_reconnecting', None)
    return result


if __name__ == '__main__':
    reattach(CONFIG_PATH)
