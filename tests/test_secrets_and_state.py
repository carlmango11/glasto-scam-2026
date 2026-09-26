import json
import tarfile

from browser_fleet.cli import fingerprint, render_bundles
from browser_fleet.config import FleetConfig, parse_proxies
from browser_fleet.render import render_worker, write_private


def test_password_never_enters_manifest_or_container_environment(tmp_path):
    config = FleetConfig.from_dict({"name": "test", "admin_cidr": "203.0.113.1/32"})
    proxies = parse_proxies("proxy.example:1080:private-user:private-password")
    manifest = render_bundles(config, proxies, tmp_path, {"proxy.example": "198.51.100.1"})
    assert "private-user" not in json.dumps(manifest)
    assert "private-password" not in json.dumps(manifest)
    with tarfile.open(tmp_path / "worker-001.tar.gz") as archive:
        secrets = archive.extractfile("sessions/0001/sing-box.json").read().decode()
        assert "private-password" in secrets
        assert archive.getmember("sessions/0001/sing-box.json").mode == 0o600
        compose = archive.extractfile("compose.json").read().decode()
        assert "private-password" not in compose
        assert "private-user" not in compose


def test_rerender_preserves_viewer_access_token(tmp_path):
    config = FleetConfig.from_dict({"name": "test", "admin_cidr": "203.0.113.1/32"})
    proxies = parse_proxies("proxy.example:1080:u:p")
    first = render_worker(config, proxies, 1, tmp_path, {"proxy.example": "198.51.100.1"})
    second = render_worker(config, proxies, 1, tmp_path, {"proxy.example": "198.51.100.1"})
    assert first["browsers"][0]["viewer_path"] == second["browsers"][0]["viewer_path"]


def test_assignment_changes_are_detected_but_operator_profile_can_change():
    config = {"name": "test", "admin_cidr": "203.0.113.1/32"}
    proxies = parse_proxies("proxy.example:1080:u:p")
    before = fingerprint(FleetConfig.from_dict(config), proxies)
    after = fingerprint(
        FleetConfig.from_dict(
            {**config, "aws_profile": "another-login", "admin_cidr": "203.0.113.2/32"}
        ),
        proxies,
    )
    changed_proxy = fingerprint(
        FleetConfig.from_dict(config), parse_proxies("proxy.example:1080:u:different")
    )
    assert before == after
    assert before != changed_proxy


def test_smaller_rerender_does_not_ship_removed_proxy_secrets(tmp_path):
    config = {"name": "test", "admin_cidr": "203.0.113.1/32"}
    render_bundles(
        FleetConfig.from_dict({**config, "browsers_per_instance": 2}),
        parse_proxies("proxy.example:1080:u:keep\nproxy.example:1080:u:removed"),
        tmp_path,
        {"proxy.example": "198.51.100.1"},
    )
    render_bundles(
        FleetConfig.from_dict(config),
        parse_proxies("proxy.example:1080:u:keep"),
        tmp_path,
        {"proxy.example": "198.51.100.1"},
    )
    with tarfile.open(tmp_path / "worker-001.tar.gz") as archive:
        assert "sessions/0002/sing-box.json" not in archive.getnames()


def test_failed_atomic_write_preserves_previous_state(tmp_path, monkeypatch):
    import os

    import pytest

    path = tmp_path / "deployment.json"
    path.write_text('{"account":"original"}')

    def fail_sync(_):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, "fsync", fail_sync)
    with pytest.raises(OSError):
        write_private(path, '{"account":"new"}')
    assert json.loads(path.read_text())["account"] == "original"


def test_reduced_worker_count_removes_old_archives_and_secrets(tmp_path):
    config = {"name": "test", "admin_cidr": "203.0.113.1/32"}
    proxies = parse_proxies("proxy.example:1080:u:keep\nproxy.example:1080:u:removed")
    addresses = {"proxy.example": "198.51.100.1"}
    render_bundles(FleetConfig.from_dict({**config, "instances": 2}), proxies, tmp_path, addresses)
    render_bundles(FleetConfig.from_dict(config), proxies[:1], tmp_path, addresses)
    assert not (tmp_path / "worker-002").exists()
    assert not (tmp_path / "worker-002.tar.gz").exists()
