"""Security checks must include inactive lock variants and fail closed."""
import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

AUDIT = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/audit_dependencies.py'))


def fixture_locks(root):
    (root / 'uv.lock').write_text('''
[[package]]
name = "example_pkg"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
[[package]]
name = "example-pkg"
version = "2.0"
source = { registry = "https://pypi.org/simple" }
[[package]]
name = "local-project"
version = "1.0"
source = { editable = "." }
''')
    (root / 'pyproject.toml').write_text('[build-system]\nrequires = ["builder==3.0"]\n')
    for filename in AUDIT['RUNTIME_LOCKS']:
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('runtime-only==4.0 \\\n    --hash=sha256:abcd\n')


def test_all_lock_variants_build_tools_and_runtimes_are_audited(tmp_path):
    fixture_locks(tmp_path)
    assert AUDIT['locked_packages'](tmp_path) == {
        'builder': ['3.0'], 'example-pkg': ['1.0', '2.0'], 'runtime-only': ['4.0'],
    }


@pytest.mark.parametrize('unverified', [False, True])
def test_unavailable_advisory_data_cannot_pass(tmp_path, monkeypatch, unverified):
    fixture_locks(tmp_path)
    main = AUDIT['main']
    monkeypatch.setitem(main.__globals__, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['audit_dependencies.py'])

    def unavailable(command, **kwargs):
        if unverified:
            Path(command[command.index('--output') + 1]).write_text(json.dumps({
                'dependencies': [{'name': 'example-pkg', 'version': '1.0',
                                  'skip_reason': 'Advisory lookup failed'}],
            }))
        return subprocess.CompletedProcess(command, 0 if unverified else 2)

    monkeypatch.setattr(subprocess, 'run', unavailable)
    if unverified:
        assert main() == 1
    else:
        with pytest.raises(SystemExit):
            main()
