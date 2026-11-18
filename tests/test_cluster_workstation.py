"""Workstation scheduling reuses the gateway's receipt and cancellation lifecycle."""
from dataclasses import replace
import json
import subprocess
import time
from types import SimpleNamespace

import pytest

from mol_blender.cluster import doctor
from mol_blender.cluster.config import Limits
from mol_blender.cluster.jsonio import atomic_json
from mol_blender.cluster.slurm import scheduler_for
from mol_blender.cluster.store import Quota, Store
from mol_blender.cluster.workstation import Workstation
from test_cluster_jobs import Scheduler, config, request


@pytest.fixture
def workstation(config):
    return replace(config, scheduler='workstation', gpus=1, gpu_index=2,
                   limits=Limits(max_active_total=1))


@pytest.mark.parametrize('settings,message', [
    ({'scheduler': 'other'}, 'scheduler'),
    ({'gpu_index': True}, 'gpu_index'),
    ({'gpu_index': -1}, 'gpu_index'),
    ({'limits': Limits()}, 'max_active_total'),
    ({'partition': 'gpu'}, 'Slurm resource selectors'),
    ({'gpu_request': 'gpus'}, 'Slurm resource selectors'),
    ({'systemctl': '/usr/%n/systemctl'}, 'percent'),
])
def test_invalid_workstation_policy_is_rejected(workstation, settings, message):
    with pytest.raises(ValueError, match=message):
        replace(workstation, **settings)


def test_slurm_keeps_ownership_of_gpu_allocation(config):
    with pytest.raises(ValueError, match='only supported by the workstation'):
        replace(config, gpu_index=1)


def test_changing_scheduler_cannot_orphan_active_slurm_jobs(config, workstation):
    store = Store(config, Scheduler(), 'b' * 64)
    result = store.submit('alice', request())
    # Legacy Slurm records have no scheduler field.
    record = store.owned('alice', result['job_id'])
    record.pop('scheduler')
    store.save(record)
    with pytest.raises(ValueError, match='separate spool'):
        Store(workstation, Workstation(workstation), 'b' * 64)


def test_worker_is_bounded_and_does_not_inherit_gateway_environment(workstation, monkeypatch):
    scheduler = scheduler_for(workstation)
    monkeypatch.setenv('APPTAINERENV_PRIVATE_TOKEN', 'do-not-inherit')
    monkeypatch.setenv('PYTHONPATH', '/untrusted')
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: calls.append((command, kwargs)) or
                        SimpleNamespace(stdout='', stderr=''))
    unit = scheduler.submit(workstation.spool, 'a' * 32)
    command, options = calls[0]
    assert command[0] == workstation.systemd_run and '--unit=' + unit in command
    assert '--collect' in command and '--property=Restart=no' in command
    assert '--property=RuntimeMaxSec=3900' in command and '--property=MemoryMax=16384M' in command
    assert '--property=KillMode=control-group' in command
    assert '/usr/bin/env' in command and '-i' in command
    assert 'APPTAINERENV_CUDA_VISIBLE_DEVICES=2' in command
    assert '--cleanenv' in command and '--containall' in command and '--nv' in command
    assert command[-1] == '/job' and 'mol_blender.cluster.worker' in command
    assert 'do-not-inherit' not in json.dumps(calls) and '/untrusted' not in json.dumps(calls)
    assert 'XDG_RUNTIME_DIR' in options['env']
    assert not any(tool in command for tool in (workstation.sbatch, workstation.srun))


class FakeManager:
    def __init__(self, lost_response=False):
        self.units = {}
        self.launches = 0
        self.lost_response = lost_response

    def run(self, command):
        if command[0].endswith('systemd-run'):
            self.launches += 1
            unit = next(item.split('=', 1)[1] for item in command if item.startswith('--unit='))
            self.units[unit] = 'active'
            if self.lost_response:
                raise subprocess.TimeoutExpired(command, 30)
            return SimpleNamespace(stdout='', stderr='')
        unit = command[3]
        if command[2] == 'stop':
            self.units.pop(unit, None)
            return SimpleNamespace(stdout='', stderr='')
        state = self.units.get(unit)
        if state is None:
            raise subprocess.CalledProcessError(1, command, output='LoadState=not-found\nActiveState=inactive\n')
        return SimpleNamespace(stdout=f'LoadState=loaded\nActiveState={state}\nResult=success\n')


def test_lost_launch_response_recovers_across_gateway_restart_without_duplicate(workstation, monkeypatch):
    manager = FakeManager(lost_response=True)
    first = Workstation(workstation)
    monkeypatch.setattr(first, 'run', manager.run)
    store = Store(workstation, first, 'b' * 64)
    accepted = store.submit('alice', request())
    assert accepted['status'] == 'submission_unknown'
    assert 'slurm_id' not in accepted and manager.launches == 1

    restarted = Workstation(workstation)
    monkeypatch.setattr(restarted, 'run', manager.run)
    store = Store(workstation, restarted, 'b' * 64)
    recovered = store.reconcile(accepted['job_id'])
    assert recovered['status'] == 'running' and recovered['worker_id'] in manager.units
    assert 'slurm_id' not in recovered
    assert store.submit('alice', request())['worker_id'] == recovered['worker_id']
    with pytest.raises(Quota):
        store.submit('alice', request('2' * 32))
    assert manager.launches == 1
    assert store.cancel('alice', accepted['job_id'])['status'] == 'cancelling'
    assert store.status('alice', accepted['job_id'])['status'] == 'cancelled'
    assert not manager.units


def test_result_survives_unit_collection_and_next_render_is_distinct(workstation, monkeypatch):
    manager = FakeManager()
    scheduler = Workstation(workstation)
    monkeypatch.setattr(scheduler, 'run', manager.run)
    store = Store(workstation, scheduler, 'b' * 64)
    first = store.submit('alice', request())
    atomic_json(store.directory(first['job_id']) / 'result.json',
                {'status': 'completed', 'finished_at': time.time(), 'artifacts': {}})
    manager.units.clear()
    assert store.status('alice', first['job_id'])['status'] == 'completed'
    second = store.submit('alice', request('2' * 32))
    assert second['worker_id'] != first['worker_id'] and manager.launches == 2


def test_missing_unit_without_worker_result_is_never_success(workstation, monkeypatch):
    scheduler = Workstation(workstation)
    manager = FakeManager()
    monkeypatch.setattr(scheduler, 'run', manager.run)
    store = Store(workstation, scheduler, 'b' * 64)
    result = store.submit('alice', request())
    manager.units.clear()
    assert store.status('alice', result['job_id'])['status'] == 'finalizing'
    record = store.owned('alice', result['job_id'])
    record['completion_seen_at'] = time.time() - 61
    store.save(record)
    assert store.status('alice', result['job_id'])['status'] == 'failed'


def test_cannot_inspect_or_cancel_another_spools_unit(workstation):
    scheduler = Workstation(workstation)
    other = Workstation(replace(workstation, spool=workstation.spool.parent / 'another'))
    for identifier in ('ssh.service', '1;stop', other.unit('a' * 32)):
        with pytest.raises(ValueError, match='worker ID'):
            scheduler.cancel(identifier)


def test_manager_outage_preserves_accepted_job(workstation, monkeypatch):
    scheduler = Workstation(workstation)
    manager = FakeManager()
    monkeypatch.setattr(scheduler, 'run', manager.run)
    store = Store(workstation, scheduler, 'b' * 64)
    result = store.submit('alice', request())
    def offline(command):
        raise subprocess.CalledProcessError(1, command, stderr='Failed to connect to bus')
    monkeypatch.setattr(scheduler, 'run', offline)
    assert store.status('alice', result['job_id'])['status'] == 'queued'
    assert manager.launches == 1


def test_doctor_checks_workstation_without_slurm_or_starting_a_unit(workstation, monkeypatch):
    workstation.spool.mkdir()
    monkeypatch.setattr(doctor.os, 'access', lambda path, mode: path in
                        [getattr(workstation, name) for name in workstation.executables])
    monkeypatch.setattr('mol_blender.cluster.workstation.platform.system', lambda: 'Linux')
    scheduler = Workstation(workstation)
    calls = []
    def run(command):
        calls.append(command)
        if command[0] == workstation.systemd_run:
            assert command == [workstation.systemd_run, '--version']
            output = 'systemd 259\n'
        elif command[0] == workstation.systemctl:
            output = 'Version=259\n'
        elif command[-1] == '--version':
            output = 'apptainer version 1.5.0'
        else:
            assert command[-1] == '--check'
            output = json.dumps({'engine_build': 'b' * 64, 'platform': 'Linux', 'python': '3.11.15'})
        return SimpleNamespace(stdout=output, stderr='')
    monkeypatch.setattr(scheduler, 'run', run)
    report = doctor.run(workstation, scheduler, 'b' * 64)
    assert report['scheduler'] == 'workstation' and report['workstation_check']['available']
    assert 'slurm_dry_run' not in report and report['render_verified'] is False
    assert not any(command[0] in (workstation.sbatch, workstation.srun) for command in calls)
