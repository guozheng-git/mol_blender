import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from blender_render.cluster.common import Limits, atomic_json, validate_request
from blender_render.cluster.config import ServerConfig
from blender_render.cluster.slurm import Slurm, SubmissionUnknown
from blender_render.cluster.store import Conflict, NotFound, Quota, Store
from blender_render.spec import resolve_options


def payload():
    return {'cif_text': 'data_test\nloop_\n_atom_site.id\n_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\n1 0 0 0\n',
            'view': {'pymol_view': [1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., -30., 0., 0., 0., 10., 100., 0.]}}


def request(identifier='1' * 32, **options):
    return {'protocol': 1, 'request_id': identifier, 'payload': payload(),
            'style': resolve_options({'width': 32, 'height': 24, 'samples': 1, **options}, payload())['style_doc']}


@pytest.fixture
def config(tmp_path):
    image = tmp_path / 'renderer.sif'
    image.write_bytes(b'image')
    return ServerConfig(spool=tmp_path / 'jobs', image=image, tokens_file=tmp_path / 'tokens.json',
                        image_sha256=hashlib.sha256(b'image').hexdigest())


class Scheduler:
    def __init__(self):
        self.submissions = []
        self.state_value = 'PENDING'
        self.cancelled = []

    def submit(self, directory, identifier):
        self.submissions.append(identifier)
        return '123'

    def state(self, identifier):
        return self.state_value

    def cancel(self, identifier):
        self.cancelled.append(identifier)


@pytest.fixture
def store(config):
    return Store(config, Scheduler(), 'a' * 64)


def test_retry_is_idempotent_and_owner_is_authoritative(store):
    first = store.submit('alice', request())
    assert store.submit('alice', request()) == first
    assert len(store.scheduler.submissions) == 1
    with pytest.raises(NotFound):
        store.status('bob', first['job_id'])
    with pytest.raises(NotFound):
        store.cancel('bob', first['job_id'])
    with pytest.raises(Conflict):
        store.submit('alice', request(samples=2))
    assert store.submit('bob', request())['job_id'] != first['job_id']


def test_ambiguous_submission_and_restart_never_duplicate(store):
    def timeout(*args):
        store.scheduler.submissions.append('uncertain')
        raise SubmissionUnknown('timeout')
    store.scheduler.submit = timeout
    result = store.submit('alice', request())
    assert result['status'] == 'submission_unknown'
    assert store.submit('alice', request())['job_id'] == result['job_id']
    assert len(store.scheduler.submissions) == 1
    record = store.owned('alice', result['job_id'])
    record['status'] = 'submitting'
    store.save(record)
    assert store.status('alice', result['job_id'])['status'] == 'submission_unknown'


def test_cancel_waits_for_slurm_before_deleting(store):
    job = store.submit('alice', request())['job_id']
    assert store.cancel('alice', job)['status'] == 'cancelling'
    with pytest.raises(Conflict):
        store.delete('alice', job)
    store.scheduler.state_value = 'CANCELLED'
    assert store.status('alice', job)['status'] == 'cancelled'
    assert store.delete('alice', job)['status'] == 'expired'
    assert [p.name for p in store.directory(job).iterdir()] == ['job.json']
    assert store.submit('alice', request())['status'] == 'expired'
    assert len(store.scheduler.submissions) == 1


def test_quota_and_scheduler_failure_are_not_silent_success(store):
    for n in range(4):
        store.submit('alice', request(f'{n:032x}'))
    with pytest.raises(Quota):
        store.submit('alice', request('f' * 32))
    store.scheduler.state_value = 'COMPLETED'
    identifier = store.scheduler.submissions[0]
    assert store.status('alice', identifier)['status'] == 'finalizing'
    record = store.owned('alice', identifier)
    record['completion_seen_at'] -= 61
    store.save(record)
    assert store.status('alice', store.scheduler.submissions[0])['status'] == 'failed'


def test_scheduler_outage_preserves_pending_job(store):
    job = store.submit('alice', request())['job_id']
    def unavailable(*args):
        raise subprocess.TimeoutExpired('squeue', 30)
    store.scheduler.state = unavailable
    assert store.status('alice', job)['status'] == 'queued'


def test_rejected_submission_keeps_private_scheduler_diagnostics(store):
    def reject(*args):
        raise subprocess.CalledProcessError(1, ['sbatch'], stderr='allocation failure: Access/permission denied')
    store.scheduler.submit = reject
    result = store.submit('alice', request())
    assert result['status'] == 'failed' and 'slurm_id' not in result
    import json
    diagnostic = json.loads((store.directory(result['job_id']) / 'submission-error.json').read_text())
    assert 'Access/permission denied' in diagnostic['stderr']
    assert 'Access/permission denied' not in json.dumps(result)
    with pytest.raises(NotFound):
        store.open_artifact('alice', result['job_id'], 'submission-error.json')


@pytest.mark.parametrize('field,value', [('input_path', '/etc/passwd'), ('pse', 'pickle'), ('script', 'code')])
def test_request_rejects_paths_sessions_and_scripts(field, value):
    doc = request()
    doc['payload'][field] = value
    with pytest.raises(ValueError, match='exported structure'):
        validate_request(doc, Limits())


@pytest.mark.parametrize('identifier', ['/job', '../../outside', 'relative/path'])
def test_export_run_id_cannot_redirect_worker_temporary_files(identifier):
    doc = request()
    doc['payload']['run_id'] = identifier
    with pytest.raises(ValueError, match='never a path'):
        validate_request(doc, Limits())


def test_view_cannot_reference_server_files():
    doc = request()
    doc['payload']['view']['state_coordinates_file'] = '/private/states.json'
    with pytest.raises(ValueError, match='camera values only'):
        validate_request(doc, Limits())


def test_output_bounds_and_destinations():
    doc = request()
    doc['style']['output'].update(filename='/tmp/arbitrary.png', blend_filename='../bad.blend')
    normalized = validate_request(doc, Limits())
    assert normalized['style']['output']['filename'] == 'render.png'
    with pytest.raises(ValueError, match='pixel/sample'):
        validate_request(request(width=8192, height=8192), Limits())
    with pytest.raises(ValueError, match='pixel/sample'):
        validate_request(request(samples=513), Limits())


@pytest.mark.parametrize('gpus', [0, 1])
def test_slurm_uses_admin_arguments_and_private_single_job_bind(config, monkeypatch, gpus):
    from dataclasses import replace
    config = replace(config, gpus=gpus)
    scheduler = Slurm(config)
    config.spool.mkdir()
    directory = config.spool / ('a' * 32)
    directory.mkdir()
    seen = []
    monkeypatch.setattr(scheduler, 'run', lambda cmd: seen.append(cmd) or SimpleNamespace(stdout='123;cluster\n'))
    assert scheduler.submit(directory, 'a' * 32) == '123'
    assert '--export=NIL' in seen[0]
    assert ('--gres=gpu:1' in seen[0]) == bool(gpus)
    script = (directory / 'job.sh').read_text()
    assert '--cleanenv --containall --no-home' in script
    assert '--no-mount hostfs,bind-paths,cwd' in script
    assert f'{directory}:/job' in script
    assert str(config.tokens_file) not in script
    assert 'uv sync' not in script and 'pip install' not in script
    assert '/opt/studio-runtime/render/bin/python -I -m blender_render.cluster.worker /job' in script


def test_slurm_accounting_fallback_and_ambiguous_output(config, monkeypatch):
    scheduler = Slurm(config)
    answers = iter(['', '123|OUT_OF_MEMORY|\n'])
    monkeypatch.setattr(scheduler, 'run', lambda cmd: SimpleNamespace(stdout=next(answers)))
    assert scheduler.state('123') == 'OUT_OF_MEMORY'
    config.spool.mkdir()
    monkeypatch.setattr(scheduler, 'run', lambda cmd: SimpleNamespace(stdout='unrecognized'))
    with pytest.raises(SubmissionUnknown):
        scheduler.submit(config.spool, 'a' * 32)
    with pytest.raises(ValueError):
        scheduler.cancel('123; rm anything')


def test_slurm_removed_queue_entry_still_uses_accounting(config, monkeypatch):
    scheduler = Slurm(config)
    def run(command):
        if command[0] == config.squeue:
            raise subprocess.CalledProcessError(1, command, stderr='Invalid job id')
        return SimpleNamespace(stdout='123|CANCELLED by 1000|\n')
    monkeypatch.setattr(scheduler, 'run', run)
    assert scheduler.state('123') == 'CANCELLED'


def test_scheduler_polling_is_cached_and_outages_back_off(config, monkeypatch):
    scheduler = Slurm(config)
    calls = []
    def run(command):
        calls.append(command)
        return SimpleNamespace(stdout='PENDING\n')
    monkeypatch.setattr(scheduler, 'run', run)
    assert scheduler.state('123') == scheduler.state('123') == 'PENDING'
    assert len(calls) == 1
    def fail(command):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 30)
    monkeypatch.setattr(scheduler, 'run', fail)
    for identifier in ('124', '125', '126'):
        with pytest.raises(subprocess.TimeoutExpired):
            scheduler.state(identifier)
    assert len(calls) == 2


def test_reuses_linux_uv_environment_at_its_original_path(config, tmp_path):
    from dataclasses import replace
    runtime = tmp_path / 'shared uv runtime'
    config = replace(config, runtime_home=runtime, gpus=1)
    command = Slurm(config).container_command(config.spool)
    assert f'{runtime}:{runtime}:ro' in command
    assert str(runtime / 'render/bin/python') in command
    assert 'BLENDER_RENDER_HOME=' + str(runtime / 'blender-render') in command
    assert '--nv' in command
    script = Slurm(config).script(config.spool)
    assert '--cpu-bind=cores' in script and '/usr/bin/srun' in script
    assert 'APPTAINERENV_CUDA_VISIBLE_DEVICES' in script
    assert 'uv sync' not in script and 'pip install' not in script


def test_reconcile_attaches_the_original_slurm_job_without_resubmitting(store):
    result = store.submit('alice', request())
    record = store.owned('alice', result['job_id'])
    record.pop('slurm_id')
    record['status'] = 'submission_unknown'
    store.save(record)
    store.scheduler.find_submission = lambda identifier, created: '123'
    assert store.reconcile(result['job_id'])['slurm_id'] == '123'
    assert len(store.scheduler.submissions) == 1


def test_worker_uses_shared_renderer_and_refuses_repeat(config, monkeypatch):
    import tempfile
    import os
    from blender_render import local, engine
    from blender_render.cluster import worker
    root = config.spool
    root.mkdir()
    build = engine.generate_manifest(Path(worker.__file__).resolve().parents[1])['engine_build']
    envelope = {'request': request(), 'limits': vars(config.limits), 'engine_build': build}
    atomic_json(root / 'request.json', envelope)
    calls = []
    previous = tempfile.gettempdir(), os.environ.get('TMPDIR')
    def render(data, **kwargs):
        assert data == payload()
        scratch = Path(tempfile.gettempdir())
        assert scratch.is_relative_to(root)
        assert os.environ['TMPDIR'] == str(scratch)
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            assert Path(handle.name).is_relative_to(scratch)
            handle.write(b'private temporary input')
        calls.append(kwargs)
        path = kwargs['output']
        path.parent.mkdir()
        path.write_bytes(b'png')
        return {'outputs': {'render.png': str(path)}}
    monkeypatch.setattr(local, 'render_payload', render)
    assert worker.run(root) == 0
    assert worker.run(root) == 0
    assert len(calls) == 1
    assert not (root / 'request.json').exists()
    assert not list(root.glob('scratch-*'))
    assert (tempfile.gettempdir(), os.environ.get('TMPDIR')) == previous


def test_worker_rejects_changed_environment_before_render(config, monkeypatch):
    from blender_render import local
    from blender_render.cluster import worker
    config.spool.mkdir()
    atomic_json(config.spool / 'request.json', {'request': request(), 'limits': vars(config.limits), 'engine_build': 'wrong'})
    monkeypatch.setattr(local, 'render_payload', lambda *a, **kw: pytest.fail('must not render'))
    assert worker.run(config.spool) == 1
    assert 'builds differ' in (config.spool / 'worker-error.log').read_text()


@pytest.mark.parametrize('settings, expected', [
    ({'gpus': 1}, ['--gres=gpu:1']),
    ({'gpus': 1, 'gpu_type': 'a100'}, ['--gres=gpu:a100:1']),
    ({'gpus': 1, 'gpu_request': 'gpus'}, ['--gpus=1']),
    ({'gpus': 1, 'gpu_request': 'gpus', 'gpu_type': 'h100'}, ['--gpus=h100:1']),
    ({'gpus': 0}, []),
])
def test_site_gpu_request_syntax(config, settings, expected):
    from dataclasses import replace
    scheduler = Slurm(replace(config, **settings))
    options = scheduler.batch_options(config.spool, 'a' * 32)
    assert [o for o in options if o.startswith(('--gres', '--gpus'))] == expected


def test_constraint_applies_to_cpu_and_gpu_jobs(config):
    from dataclasses import replace
    for gpus in (0, 1):
        options = Slurm(replace(config, gpus=gpus, constraint='a100|h100')).batch_options(config.spool, 'x')
        assert '--constraint=a100|h100' in options


@pytest.mark.parametrize('settings, message', [
    ({'gpu_request': 'gpu'}, 'gpu_request'),
    ({'gpus': 1, 'gpu_type': 'a100:2'}, 'gpu_type'),
    ({'gpus': 1, 'gpu_type': 'a100 --wrap=x'}, 'gpu_type'),
    ({'gpu_type': 'a100'}, 'requires gpus'),
    ({'constraint': 'a100 h100'}, 'constraint'),
    ({'constraint': '[a100*2]'}, 'constraint'),
    ({'constraint': 'a100|'}, 'constraint'),
    ({'constraint': 'a' * 201}, 'constraint'),
])
def test_site_gpu_settings_are_bounded(config, settings, message):
    from dataclasses import asdict
    document = {**asdict(config), **settings}
    with pytest.raises(ValueError, match=message):
        ServerConfig(**document)


def test_existing_configs_keep_plain_gres(config):
    assert (config.gpu_request, config.gpu_type, config.constraint) == ('gres', '', '')


def test_job_script_records_compute_node_apptainer_version(config):
    script = Slurm(config).script(config.spool)
    assert '/usr/bin/apptainer --version > apptainer.version 2>&1 || true\n' in script
    assert script.index('apptainer.version') < script.index('exec ')


def test_dry_run_uses_real_submission_options_without_queueing(config, monkeypatch, tmp_path):
    from dataclasses import replace
    config = replace(config, gpus=1, gpu_request='gpus', gpu_type='l40s', partition='gpu', constraint='ib')
    scheduler = Slurm(config)
    seen = []
    monkeypatch.setattr(scheduler, 'run', lambda cmd: seen.append(cmd) or SimpleNamespace(
        stdout='', stderr='sbatch: Job 77 to start at 2026-11-03T10:00:00 using 8 processors on nodes g1 in partition gpu\n'))
    result = scheduler.dry_run(tmp_path)
    assert seen[0][:2] == [config.sbatch, '--test-only'] and '--parsable' not in seen[0]
    assert seen[0][2:-1] == scheduler.batch_options(tmp_path, 'doctor')
    assert result['accepted'] and 'partition gpu' in result['message']
    assert {'--gpus=l40s:1', '--partition=gpu', '--constraint=ib'} <= set(result['options'])
    assert (tmp_path / 'job.sh').is_file()


def test_dry_run_reports_slurm_rejection(config, monkeypatch, tmp_path):
    scheduler = Slurm(config)
    def reject(command):
        raise subprocess.CalledProcessError(1, command, stderr='sbatch: error: Invalid generic resource (gres) specification\n')
    monkeypatch.setattr(scheduler, 'run', reject)
    with pytest.raises(ValueError, match='Invalid generic resource'):
        scheduler.dry_run(tmp_path)


@pytest.mark.parametrize('output, product, version', [
    ('apptainer version 1.5.5\n', 'apptainer', '1.5.5'),
    ('apptainer version 1.3.4-1.el8\n', 'apptainer', '1.3.4'),
    ('apptainer version v1.1.0+12-gabc\n', 'apptainer', '1.1.0'),
    ('apptainer version 1.2\n', 'apptainer', '1.2.0'),
])
def test_supported_apptainer_versions(output, product, version):
    from blender_render.cluster.doctor import apptainer_version
    runtime, warnings = apptainer_version(output)
    assert (runtime['product'], runtime['version'], warnings) == (product, version, [])


def test_old_apptainer_is_refused_and_singularity_warns():
    from blender_render.cluster.doctor import apptainer_version
    with pytest.raises(ValueError, match='1.0.3 is too old; 1.1 or newer'):
        apptainer_version('apptainer version 1.0.3')
    for output in ('singularity-ce version 4.1.2', 'singularity version 3.8.7'):
        runtime, warnings = apptainer_version(output)
        assert runtime['product'].startswith('singularity') and 'untested' in warnings[0]
    with pytest.raises(ValueError, match='Could not determine'):
        apptainer_version('command not found')


def test_doctor_checks_runtime_container_and_dry_run_in_order(config, monkeypatch):
    import json
    from blender_render.cluster import doctor
    monkeypatch.setattr(doctor.os, 'access', lambda path, mode: True)
    calls = []

    class Fake(Slurm):
        def run(self, command):
            calls.append(command)
            if command[-1] == '--version':
                return SimpleNamespace(stdout='apptainer version 1.4.5\n', stderr='')
            return SimpleNamespace(stdout=json.dumps({'engine_build': 'b' * 64, 'platform': 'Linux',
                                                      'machine': 'x86_64', 'python': '3.11.15'}), stderr='')

        def dry_run(self, directory):
            calls.append('dry-run')
            return {'accepted': True, 'message': 'sbatch: Job 1 to start', 'options': []}

    report = doctor.run(config, Fake(config), 'b' * 64)
    assert calls[0] == [config.apptainer, '--version'] and calls[1][-1] == '--check' and calls[2] == 'dry-run'
    assert report['apptainer']['version'] == '1.4.5' and report['slurm_dry_run']['accepted']
    assert report['render_verified'] is False and report['warnings'] == []


def test_doctor_stops_before_container_check_on_old_apptainer(config, monkeypatch):
    from blender_render.cluster import doctor
    monkeypatch.setattr(doctor.os, 'access', lambda path, mode: True)
    calls = []

    class Fake(Slurm):
        def run(self, command):
            calls.append(command)
            return SimpleNamespace(stdout='apptainer version 1.0.0\n', stderr='')

    with pytest.raises(ValueError, match='too old'):
        doctor.run(config, Fake(config), 'b' * 64)
    assert len(calls) == 1
