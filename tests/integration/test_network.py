"""Run with RUN_NETWORK_TESTS=1 pytest tests/integration -v on a Docker host."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from browser_fleet.config import FleetConfig, parse_proxies
from browser_fleet.render import render_worker

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_NETWORK_TESTS") != "1", reason="explicit Docker integration run required"
)


@pytest.fixture(params=["socks5", "http", "https"])
def fleet(request):
    directory = Path(__file__).parent / "generated" / request.param
    config = FleetConfig.from_dict(
        {
            "name": f"bf-test-{request.param}",
            "admin_cidr": "203.0.113.1/32",
            "browsers_per_instance": 2,
            "proxy_protocol": request.param,
        }
    )
    proxies = parse_proxies(
        "proxy.test:1080:browser-one:test-password\nproxy.test:1080:browser-two:test-password"
    )
    render_worker(config, proxies, 1, directory, {"proxy.test": "172.30.0.250"})
    compose = json.loads((directory / "compose.json").read_text())
    compose["services"].pop("viewer")
    for number in (1, 2):
        desktop = compose["services"][f"desktop-{number:04d}"]
        desktop.pop("build")
        desktop["image"] = "browser-fleet-gateway:local"
        desktop["entrypoint"] = ["sleep", "infinity"]
        desktop["healthcheck"] = {"test": ["CMD", "true"], "interval": "1s"}
        desktop["security_opt"] = ["no-new-privileges:true"]
    compose["services"]["proxy"] = {
        "image": "browser-fleet-gateway:local",
        "entrypoint": ["python3", "/test/proxy.py"],
        "volumes": [f"{Path(__file__).parent.resolve() / 'proxy.py'}:/test/proxy.py:ro"],
        "networks": {"egress": {"ipv4_address": "172.30.0.250"}},
    }
    compose["services"]["observer"] = {
        "image": "browser-fleet-gateway:local",
        "entrypoint": ["sleep", "infinity"],
        "networks": {"egress": {"ipv4_address": "172.30.0.2"}},
    }
    if request.param == "https":
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(directory / "key.pem"),
                "-out",
                str(directory / "cert.pem"),
                "-days",
                "1",
                "-subj",
                "/CN=proxy.test",
                "-addext",
                "subjectAltName=DNS:proxy.test",
            ],
            check=True,
            capture_output=True,
        )
        compose["services"]["proxy"]["environment"] = {"TEST_TLS": "1"}
        compose["services"]["proxy"]["volumes"] += [
            "./cert.pem:/test/cert.pem:ro",
            "./key.pem:/test/key.pem:ro",
        ]
        for number in (1, 2):
            gateway = compose["services"][f"net-{number:04d}"]
            gateway["volumes"].append("./cert.pem:/test-ca.pem:ro")
            gateway["environment"] = {"SSL_CERT_FILE": "/test-ca.pem"}
    path = directory / "compose.json"
    path.write_text(json.dumps(compose))
    command = ["docker", "compose", "-f", str(path)]
    try:
        subprocess.run(
            [*command, "up", "-d", "--no-build", "--wait", "--wait-timeout", "60"], check=True
        )
        yield command
    finally:
        subprocess.run([*command, "down", "--volumes", "--timeout", "3"], check=True)


def execute(fleet, service, *arguments, check=True):
    return subprocess.run(
        [*fleet, "exec", "-T", service, *arguments],
        capture_output=True,
        text=True,
        check=check,
        timeout=30,
    )


def test_proxy_dns_and_fail_closed(fleet):
    # The direct endpoint is reachable from the observer: failure below must come from isolation.
    assert (
        "controlled destination"
        in execute(fleet, "observer", "curl", "-fsS", "http://172.30.0.250:8000").stdout
    )
    for number in (1, 2):
        service = f"desktop-{number:04d}"
        assert (
            "controlled destination"
            in execute(
                fleet, service, "curl", "-fsS", "--max-time", "8", "http://93.184.216.34"
            ).stdout
        )
        dns = execute(fleet, service, "dig", "+short", "+time=5", "+tries=1", "example.com")
        assert dns.stdout.strip() and "timed out" not in dns.stdout
        assert (
            "controlled destination"
            in execute(
                fleet, service, "curl", "-g", "-fsS", "--max-time", "8", "http://[2606:4700::1111]"
            ).stdout
        )
        assert (
            execute(
                fleet,
                service,
                "curl",
                "-fsS",
                "--max-time",
                "2",
                "http://172.30.0.250:8000",
                check=False,
            ).returncode
            != 0
        )
        assert (
            execute(
                fleet,
                service,
                "dig",
                "@127.0.0.11",
                "+time=1",
                "+tries=1",
                "example.com",
                check=False,
            ).returncode
            != 0
        )
        assert (
            execute(
                fleet,
                service,
                "dig",
                "@172.30.0.1",
                "+time=1",
                "+tries=1",
                "example.com",
                check=False,
            ).returncode
            != 0
        )
        assert (
            execute(
                fleet,
                service,
                "curl",
                "-fsS",
                "--max-time",
                "2",
                "http://169.254.169.254/latest/meta-data/",
                check=False,
            ).returncode
            != 0
        )
    events = json.loads(
        execute(fleet, "observer", "curl", "-fsS", "http://172.30.0.250:8000/events").stdout
    )
    for username in ("browser-one", "browser-two"):
        assert any(e["username"] == username and e["host"] == "93.184.216.34" for e in events)
        assert any(
            e["username"] == username and e["host"] == "1.1.1.1" and e["port"] == 443
            for e in events
        )
    execute(fleet, "net-0001", "ip", "link", "delete", "bf-tun")
    assert (
        execute(
            fleet,
            "desktop-0001",
            "curl",
            "-fsS",
            "--max-time",
            "2",
            "http://172.30.0.250:8000",
            check=False,
        ).returncode
        != 0
    )
    assert (
        execute(
            fleet,
            "desktop-0001",
            "curl",
            "-fsS",
            "--max-time",
            "2",
            "http://93.184.216.34",
            check=False,
        ).returncode
        != 0
    )
    assert (
        "controlled destination"
        in execute(
            fleet, "desktop-0002", "curl", "-fsS", "--max-time", "5", "http://93.184.216.34"
        ).stdout
    )
    if "https" in fleet[-1]:
        settings_path = Path(fleet[-1]).parent / "sessions/0002/sing-box.json"
        settings = json.loads(settings_path.read_text())
        settings["outbounds"][0]["tls"]["server_name"] = "wrong-host.test"
        settings_path.write_text(json.dumps(settings))
        subprocess.run(
            [
                *fleet,
                "up",
                "-d",
                "--no-build",
                "--force-recreate",
                "--wait",
                "net-0002",
                "desktop-0002",
            ],
            check=True,
        )
        assert (
            execute(
                fleet,
                "desktop-0002",
                "curl",
                "-fsS",
                "--max-time",
                "3",
                "http://93.184.216.34",
                check=False,
            ).returncode
            != 0
        )
    else:
        subprocess.run([*fleet, "stop", "--timeout", "2", "proxy"], check=True)
        assert (
            execute(
                fleet,
                "desktop-0002",
                "curl",
                "-fsS",
                "--max-time",
                "3",
                "http://93.184.216.34",
                check=False,
            ).returncode
            != 0
        )
