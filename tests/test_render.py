import json

import pytest

from browser_fleet.config import FleetConfig, parse_proxies
from browser_fleet.render import render_worker, split_proxies


def config(**changes):
    return FleetConfig.from_dict({"name": "test", "admin_cidr": "203.0.113.1/32", **changes})


def test_assignments_are_contiguous_and_require_enough_proxies():
    proxies = parse_proxies("\n".join(f"proxy.example:1080:u:p{i}" for i in range(4)))
    groups = split_proxies(config(instances=2, browsers_per_instance=2), proxies)
    assert [[p.password for p in group] for group in groups] == [["p0", "p1"], ["p2", "p3"]]
    with pytest.raises(ValueError, match="4"):
        split_proxies(config(instances=2, browsers_per_instance=2), proxies[:3])


def test_extra_proxies_are_not_assigned():
    proxies = parse_proxies("\n".join(f"proxy.example:1080:u:p{i}" for i in range(7)))
    groups = split_proxies(config(instances=2, browsers_per_instance=2), proxies)
    assert [[p.password for p in group] for group in groups] == [["p0", "p1"], ["p2", "p3"]]


def test_bundle_has_separate_namespaces_and_no_secrets_in_compose(tmp_path):
    proxies = parse_proxies("proxy.example:1080:u:top$secret\nproxy.example:1080:u:second")
    render_worker(
        config(browsers_per_instance=2), proxies, 1, tmp_path, {"proxy.example": "198.51.100.10"}
    )
    compose = json.loads((tmp_path / "compose.json").read_text())
    assert compose["services"]["desktop-0001"]["network_mode"] == "service:net-0001"
    assert compose["services"]["desktop-0002"]["network_mode"] == "service:net-0002"
    assert compose["services"]["desktop-0001"]["cap_drop"] == ["ALL"]
    assert compose["services"]["viewer"]["ports"] == ["127.0.0.1:8080:8080"]
    assert "top$secret" not in (tmp_path / "compose.json").read_text()
    settings = json.loads((tmp_path / "sessions/0001/sing-box.json").read_text())
    assert settings["outbounds"][0]["password"] == "top$secret"
    assert settings["dns"]["servers"][0]["detour"] == "upstream"
    assert settings["outbounds"][0]["server"] == "198.51.100.10"
    assert (tmp_path / "sessions/0001/sing-box.json").stat().st_mode & 0o777 == 0o600
    assert "secret" not in (tmp_path / "manifest.json").read_text()


def test_https_verifies_original_proxy_hostname(tmp_path):
    render_worker(
        config(proxy_protocol="https"),
        parse_proxies("proxy.example:443:u:p"),
        1,
        tmp_path,
        {"proxy.example": "198.51.100.10"},
    )
    outbound = json.loads((tmp_path / "sessions/0001/sing-box.json").read_text())["outbounds"][0]
    assert outbound["tls"] == {"enabled": True, "server_name": "proxy.example"}
