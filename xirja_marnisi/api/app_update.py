"""Authenticated, same-server APK delivery. No public File attachment."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import frappe
from xirja_marnisi.api.security import require_pos_access

PACKAGE = 'com.example.xirja_frontend'
MAX_APK_BYTES = 200 * 1024 * 1024
DOWNLOAD_PATH = '/api/method/xirja_marnisi.api.app_update.download'


def _authorize():
    # Network reachability is managed by the infrastructure team. Preserve the
    # existing POS session/role boundary without a separate application IP list.
    require_pos_access(staff_allowed=True)


def _release(root: Path):
    manifest = root / 'release.json'
    if not manifest.is_file():
        return None
    data = json.loads(manifest.read_text())
    filename = data.get('filename', '')
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith('.apk'):
        raise ValueError('Invalid APK filename')
    apk = (root / filename).resolve()
    if apk.parent != root.resolve() or not apk.is_file():
        raise ValueError('Release APK is missing')
    if (data.get('package_name') != PACKAGE or type(data.get('version_code')) is not int
            or data['version_code'] < 1 or not isinstance(data.get('version_name'), str)
            or not data['version_name'].strip()
            or type(data.get('size_bytes')) is not int
            or not 0 < data['size_bytes'] <= MAX_APK_BYTES
            or apk.stat().st_size != data['size_bytes']
            or not re.fullmatch(r'[a-f0-9]{64}', data.get('sha256', ''))):
        raise ValueError('Invalid APK release metadata')
    # Polling checks metadata/size; publication and download verify the checksum.
    return data, apk


def _root():
    return Path(frappe.get_site_path('private', 'pos-updates'))


@frappe.whitelist()
def check():
    _authorize()
    release = _release(_root())
    if release is None:
        return {'available': False}
    data, _ = release
    return {'available': True, **{key: data[key] for key in
            ('package_name', 'version_code', 'version_name', 'size_bytes', 'sha256')},
            'download_path': DOWNLOAD_PATH,
            'notes': str(data.get('notes') or '')[:2000]}


@frappe.whitelist()
def download(sha256: str = ''):
    _authorize()
    release = _release(_root())
    if release is None or sha256 != release[0]['sha256']:
        frappe.throw('Release changed or is unavailable. Check for updates again.')
    data, apk = release
    content = apk.read_bytes()
    if hashlib.sha256(content).hexdigest() != data['sha256']:
        frappe.throw('Release checksum failed. Contact support.')
    frappe.local.response.update({'type': 'download', 'filename': apk.name,
        'filecontent': content, 'content_type': 'application/vnd.android.package-archive',
        'display_content_as': 'attachment'})
