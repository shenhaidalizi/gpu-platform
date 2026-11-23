from types import SimpleNamespace

from gpu_platform.api import host_status


def test_disk_distinguishes_reserved_space_and_unmounted_directory(monkeypatch):
    monkeypatch.setattr(host_status.os.path, "ismount", lambda path: path == "/d")
    fs = SimpleNamespace(f_blocks=100, f_bfree=22, f_bavail=18, f_frsize=4096)
    monkeypatch.setattr(host_status.os, "statvfs", lambda path: fs)
    assert host_status.disk_usage("/e") == {"mount": "/e", "error": "未挂载"}
    disk = host_status.disk_usage("/d")
    assert disk["used"] == 78 * 4096 and disk["reserved"] == 4 * 4096
    assert disk["total"] == disk["used"] + disk["available"] + disk["reserved"]


def test_disk_summary_matches_current_mount(tmp_path):
    import json
    import os
    mount = tmp_path / "d"
    mount.mkdir()
    from unittest.mock import patch
    with patch.object(host_status, "disk_usage", return_value={"mount": str(mount)}):
        summary = {"device": os.stat(mount).st_dev, "users": [{"name": "a", "bytes": 4096}]}
        snapshot = tmp_path / "d.json"
        snapshot.write_text(json.dumps(summary))
        assert host_status.disk_with_owners(str(mount), tmp_path)["ownership"] == summary
        snapshot.write_text(json.dumps({**summary, "device": -1}))
        assert host_status.disk_with_owners(str(mount), tmp_path)["ownership"] is None


def test_scan_uses_disk_blocks_and_counts_linked_files_once(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    import runpy
    scan = runpy.run_path(str(Path(__file__).resolve().parents[1] / "deploy/disk_owners.py"))["scan"]
    monkeypatch.setattr(os.path, "ismount", lambda mount: True)
    f = tmp_path / "sparse"
    with f.open("wb") as stream:
        stream.seek(16 * 1024**2)
        stream.write(b"x")
    os.link(f, tmp_path / "link")
    result = scan(tmp_path)
    assert result["partial"] is False
    assert len(result["users"]) == 1
    assert result["users"][0]["bytes"] == (tmp_path.stat().st_blocks + f.stat().st_blocks) * 512
