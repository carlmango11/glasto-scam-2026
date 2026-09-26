import json

import pytest

from browser_fleet.config import FleetConfig, load_config, parse_proxies


def test_proxy_password_keeps_colons_and_literal_characters():
    proxy = parse_proxies("proxy.example:12321:user:p:a$ss\\_word\n")[0]
    assert proxy.password == "p:a$ss\\_word"
    assert proxy.host == "proxy.example"
    assert proxy.port == 12321
    assert "p:a" not in repr(proxy)


def test_blank_lines_are_ignored_and_order_is_preserved():
    proxies = parse_proxies("\nfirst.example:80:u:a\n\nsecond.example:443:v:b\r\n")
    assert [p.host for p in proxies] == ["first.example", "second.example"]


@pytest.mark.parametrize(
    "row",
    [
        "a:80:u",
        "a:0:u:p",
        "a:65536:u:p",
        "a:x:u:p",
        ":80:u:p",
        "a:80::p",
        "a:80:u:",
        "a/evil:80:u:p",
        "a:80:u:p\x00",
    ],
)
def test_bad_proxy_errors_never_echo_secrets(row):
    with pytest.raises(ValueError) as error:
        parse_proxies(row)
    assert row not in str(error.value)
    assert "line 1" in str(error.value)


def test_duplicate_proxy_is_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        parse_proxies("a:80:u:secret\na:80:u:secret")


def test_config_paths_are_relative_to_config_file(tmp_path):
    path = tmp_path / "fleet.json"
    path.write_text(
        json.dumps(
            {
                "name": "demo",
                "instances": 2,
                "browsers_per_instance": 3,
                "admin_cidr": "203.0.113.7/32",
                "proxy_file": "proxies.txt",
            }
        )
    )
    config = load_config(path)
    assert config.proxy_file == tmp_path / "proxies.txt"
    assert config.total_browsers == 6


@pytest.mark.parametrize(
    "changes",
    [
        {"instances": 0},
        {"instances": True},
        {"browsers_per_instance": 201},
        {"admin_cidr": "0.0.0.0/0"},
        {"name": "../bad"},
        {"proxy_protocol": "invalid"},
        {"browser_memory_mb": 128},
    ],
)
def test_invalid_config_is_rejected(changes):
    values = {"name": "demo", "admin_cidr": "203.0.113.7/32", **changes}
    with pytest.raises(ValueError):
        FleetConfig.from_dict(values)


def test_config_rejects_credential_fields():
    with pytest.raises(ValueError, match="Unknown"):
        FleetConfig.from_dict({"name": "demo", "aws_secret_access_key": "secret"})


@pytest.mark.parametrize(
    "changes",
    [
        {"name": 7},
        {"region": None},
        {"instance_type": []},
        {"proxy_protocol": []},
        {"aws_profile": 7},
    ],
)
def test_invalid_string_types_are_user_errors(changes):
    with pytest.raises(ValueError):
        FleetConfig.from_dict({"name": "demo", "admin_cidr": "203.0.113.7/32", **changes})
