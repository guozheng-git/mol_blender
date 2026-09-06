import asyncio
import copy
import json
from pathlib import Path
import pytest
from blender_render import core
from blender_render.budget import OperatingBudget
from blender_render.jobs import JobStore, Policy
from blender_render.service import prepare_options
from blender_render.api import BoundedBody
from blender_render.hpc import validate_profile


def test_rough_changes_only_performance():
    original = core.load_and_validate_style_json(core._default_preset_style('dried_paint_green'))
    rough = core._apply_rough_style_overrides(copy.deepcopy(original))
    for field in ('materials','styles','camera','lights','world','geometry'):
        assert rough[field] == original[field], field


def test_packaged_preset_and_runtime_assets_exist():
    for name in core.PRESET_STYLE_FILENAMES:
        core.load_and_validate_style_json(core._default_preset_style(name))
    root = Path(core.__file__).parent
    for name in ['runtime.lock.json','runtime-requirements.txt','runtime-sources.json','style.schema.json','profile_ipd.json']:
        assert (root/name).is_file()
    assert 'molecularnodes==4.5.10' in (root/'runtime-requirements.txt').read_text().lower()


def test_operating_budget_is_reserved_and_cannot_refill_on_restart(tmp_path):
    now = [1780000000.]
    store = JobStore(tmp_path, Policy(public=True), clock=lambda:now[0])
    budget = OperatingBudget(store, cap_usd=.1)
    assert budget.reserve_hour() and budget.reserve_hour()
    now[0] += 3601
    assert budget.reserve_hour()
    store.close()
    store = JobStore(tmp_path, Policy(public=True), clock=lambda:now[0])
    budget = OperatingBudget(store, cap_usd=530)
    now[0] += 3601
    assert not budget.reserve_hour()
    store.close()


def test_chunked_mcp_upload_cannot_bypass_size_limit():
    async def run():
        called, responses = [], []
        async def app(*args): called.append(True)
        parts = iter([{'type':'http.request','body':b'x'*700000,'more_body':True},
                      {'type':'http.request','body':b'x'*700000,'more_body':False}])
        async def receive(): return next(parts)
        async def send(message): responses.append(message)
        await BoundedBody(app, 50*1024**2)({'type':'http','method':'POST','path':'/mcp'},receive,send)
        assert not called
        assert responses[0]['status'] == 413
    asyncio.run(run())


def test_budget_failure_still_stops_compute_when_persistence_is_unavailable(tmp_path):
    store = JobStore(tmp_path)
    budget = OperatingBudget(store)
    stopped = []
    class Service:
        def stop(self): stopped.append('worker')
    service = Service()
    service.store = store
    store.healthy = False
    budget.stop_unfunded(service, lambda: stopped.append('deployment'))
    assert stopped == ['worker', 'deployment']
    store.close()


def test_expired_grant_does_not_use_prepaid_operating_hour(tmp_path):
    now = [100.]
    store = JobStore(tmp_path, Policy(expires_at=100000), clock=lambda:now[0])
    budget = OperatingBudget(store)
    assert budget.reserve_hour()
    now[0] = 100001
    assert not budget.reserve_hour()
    store.close()


def test_profile_rejects_ssh_option_injection():
    profile = json.loads(Path(core.__file__).with_name('profile_ipd.json').read_text())
    validate_profile(profile)
    profile['host'] = '-oProxyCommand=unexpected'
    with pytest.raises(ValueError, match='Invalid SSH host'): validate_profile(profile)


def test_encrypted_oauth_survives_restart_and_expires(tmp_path):
    from blender_render.auth import SQLiteCredentials
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
    async def run():
        store = JobStore(tmp_path)
        wrapped = FernetEncryptionWrapper(SQLiteCredentials(store), source_material='k'*48, salt='test')
        await wrapped.put('user', {'token':'sensitive-test-token'}, collection='oauth')
        assert 'sensitive-test-token' not in (tmp_path/'jobs.sqlite3').read_bytes().decode(errors='ignore')
        store.close()
        store = JobStore(tmp_path)
        wrapped = FernetEncryptionWrapper(SQLiteCredentials(store), source_material='k'*48, salt='test')
        assert (await wrapped.get('user', collection='oauth'))['token'] == 'sensitive-test-token'
        await wrapped.delete('user', collection='oauth')
        assert await wrapped.get('user', collection='oauth') is None
        store.close()
    asyncio.run(run())
