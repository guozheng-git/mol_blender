"""Reject resource exhaustion and corrupt bundles before writing restored files."""
import base64
import gzip
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       'plugins/structure-studio/skills/structure-studio/scripts'))
import artifact_files as files


@pytest.fixture
def bundle(tmp_path):
    source = tmp_path / 'session.pse'
    source.write_bytes(b'x' * 4096)
    paths = files.bundle({'pse': str(source)}, str(tmp_path / 'saved'))['parts']
    return Path(paths[0])


def rewrite(path, change):
    doc = json.loads(path.read_text())
    change(doc)
    path.write_text(json.dumps(doc))


@pytest.mark.parametrize('field,value', [
    ('bytes', 10), ('bytes', -1), ('bytes', True), ('bytes', files.MAX_FILE_BYTES + 1),
    ('parts', 10**12), ('part', False), ('sha256', 'bad'), ('data', '###'),
])
def test_rejects_bad_entry_before_writing(bundle, tmp_path, field, value):
    rewrite(bundle, lambda doc: doc['entries'][0].update({field: value}))
    out = tmp_path / 'restored'
    with pytest.raises(ValueError):
        files.restore([str(bundle)], str(out))
    assert not out.exists()


@pytest.mark.parametrize('suffix', [b'junk', gzip.compress(b'another member')])
def test_gzip_trailing_data_rejected(suffix):
    with pytest.raises(ValueError, match='concatenated'):
        files._decode(base64.b64encode(gzip.compress(b'valid') + suffix))


def test_truncated_and_bomb_streams():
    with pytest.raises(ValueError, match='Truncated'):
        files._decode(base64.b64encode(gzip.compress(b'data')[:-2]))
    with pytest.raises(ValueError, match='size limit'):
        files._decode(base64.b64encode(gzip.compress(b'x' * 1_000_000)), 16)


def test_aggregate_and_input_limits(bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(files, 'MAX_TOTAL_BYTES', 16)
    with pytest.raises(ValueError, match='total'):
        files.restore([str(bundle)], str(tmp_path / 'out'))
    monkeypatch.setattr(files, 'MAX_PART_BYTES', 16)
    with pytest.raises(ValueError, match='size limit'):
        files.restore([str(bundle)], str(tmp_path / 'out'))


def test_duplicate_document_and_entry_rejected(bundle, tmp_path):
    with pytest.raises(ValueError, match='duplicate'):
        files.restore([str(bundle)] * 2, str(tmp_path / 'out'))
    rewrite(bundle, lambda doc: doc['entries'].append(doc['entries'][0].copy()))
    with pytest.raises(ValueError, match='duplicate'):
        files.restore([str(bundle)], str(tmp_path / 'out'))


def test_legacy_unpack_is_bounded_and_never_overwrites(tmp_path, monkeypatch):
    source = tmp_path / 'data.txt'
    source.write_bytes(base64.b64encode(gzip.compress(b'test')))
    output = tmp_path / 'out.pse'
    output.write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        files.unpack([str(source)], str(output))
    assert output.read_bytes() == b'keep'
    monkeypatch.setattr(files, 'MAX_ENCODED_BYTES', 2)
    with pytest.raises(ValueError, match='size limit'):
        files.unpack([str(source)], str(tmp_path / 'new'))
