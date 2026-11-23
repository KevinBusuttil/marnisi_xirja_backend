import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from xirja_marnisi.api import app_update as updates


def release(tmp_path):
    content = b'APK fixture'
    (tmp_path / 'pos.apk').write_bytes(content)
    data = dict(filename='pos.apk', package_name=updates.PACKAGE, version_code=15,
                version_name='1.1.3', size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
    (tmp_path / 'release.json').write_text(json.dumps(data))
    return data


def test_authorization_applies_to_both_endpoints(monkeypatch):
    def deny():
        raise PermissionError('unauthorized')
    monkeypatch.setattr(updates, '_authorize', deny)
    with pytest.raises(PermissionError):
        updates.check()
    with pytest.raises(PermissionError):
        updates.download()


def test_manifest_missing_and_valid(tmp_path):
    assert updates._release(tmp_path) is None
    data = release(tmp_path)
    assert updates._release(tmp_path)[0] == data


@pytest.mark.parametrize('key,value', [('filename', '../pos.apk'), ('package_name', 'other'),
    ('version_code', 0), ('version_code', True), ('size_bytes', 999), ('sha256', 'bad')])
def test_bad_manifest_is_rejected(tmp_path, key, value):
    data = release(tmp_path); data[key] = value
    (tmp_path / 'release.json').write_text(json.dumps(data))
    with pytest.raises(ValueError):
        updates._release(tmp_path)


def test_check_does_not_expose_storage_paths(monkeypatch, tmp_path):
    release(tmp_path)
    monkeypatch.setattr(updates, '_authorize', lambda: None)
    monkeypatch.setattr(updates, '_root', lambda: tmp_path)
    result = updates.check()
    assert result['download_path'] == updates.DOWNLOAD_PATH
    assert 'filename' not in result


def test_download_validates_revision_and_corrupt_bytes(monkeypatch, tmp_path):
    data = release(tmp_path)
    monkeypatch.setattr(updates, '_authorize', lambda: None)
    monkeypatch.setattr(updates, '_root', lambda: tmp_path)
    def throw(message, *args):
        raise ValueError(message)
    monkeypatch.setattr(updates.frappe, 'throw', throw)
    monkeypatch.setattr(updates.frappe, 'local', SimpleNamespace(response={}), raising=False)
    with pytest.raises(ValueError, match='Release changed'):
        updates.download('old')
    updates.download(data['sha256'])
    assert updates.frappe.local.response['filecontent'] == b'APK fixture'
    (tmp_path / 'pos.apk').write_bytes(b'bad fixture')
    with pytest.raises(ValueError, match='checksum'):
        updates.download(data['sha256'])


def test_authorize_requires_pos_access_without_ip_configuration(monkeypatch):
    calls = []
    monkeypatch.setattr(updates, 'require_pos_access', lambda **kw: calls.append(kw))
    # No request IP or site network configuration is necessary.
    updates._authorize()
    assert calls == [{'staff_allowed': True}]


def test_symlink_outside_private_release_directory_is_rejected(tmp_path):
    data = release(tmp_path)
    outside = tmp_path.parent / 'outside.apk'
    outside.write_bytes(b'APK fixture')
    (tmp_path / 'pos.apk').unlink()
    (tmp_path / 'pos.apk').symlink_to(outside)
    with pytest.raises(ValueError):
        updates._release(tmp_path)
