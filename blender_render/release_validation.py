"""Explicit publication gate; preparatory artifacts cannot count as executed tests."""
import json

REQUIRED = ('installed_wheel','clean_mac_runtime','pymol_export','visual_mac','visual_hpc','visual_modal',
    'hpc_fifo_restart','job_reliability','public_security','sandbox_isolation','budget_exhaustion',
    'automatic_shutdown','github_oauth','codex_plugin','chatgpt_plugin','blend_download','container','licenses','history_audit')

def require_release_ready(path):
    if path is None or not path.is_file():
        raise ValueError('Public deployment requires a release_validation report; use restricted staging to complete account tests')
    report=json.loads(path.read_text())
    missing=[name for name in REQUIRED if report.get('checks',{}).get(name,{}).get('status') != 'passed']
    if missing:
        raise ValueError('Release checks remain incomplete: '+', '.join(missing))
    return report
