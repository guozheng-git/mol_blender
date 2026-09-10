"""Opt-in pytest audit guard: PYTHONPATH=tests pytest -p c1_boundary_guard.

No executed process/network boundaries, including attempts swallowed by code
under test. Does not affect the ordinary A/B process-based acceptance suite.
"""
import sys

import pytest

attempts = []


def guard(event, args):
    if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.exec', 'os.fork', 'os.forkpty',
                 'socket.connect', 'socket.bind', 'socket.getaddrinfo', 'socket.sendto'}:
        attempts.append(event)
        raise RuntimeError('C1 audit blocked real boundary: ' + event)


sys.addaudithook(guard)


def pytest_collection_modifyitems(items):
    process_tests = {
        'tests/test_developer.py::test_snapshot_tracks_python_and_assets',
        'tests/test_developer.py::test_save_restart_reuse_and_revision',
    }
    for item in items:
        if any(item.nodeid.endswith(name) or (item.path.name == 'test_developer.py' and item.name == name.split('::')[1]) for name in process_tests):
            item.add_marker(pytest.mark.skip(
                reason='Existing A/B child-process acceptance test; prohibited by C1 fake-only boundary'))


def pytest_sessionfinish(session, exitstatus):
    if attempts:
        session.exitstatus = 1
    reporter = session.config.pluginmanager.get_plugin('terminalreporter')
    reporter.write_line('C1 audit: real process/network boundary attempts = ' + str(attempts))
