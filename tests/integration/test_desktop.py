import json
import os
import socket
import subprocess
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest

from browser_fleet.config import FleetConfig, parse_proxies
from browser_fleet.render import render_worker

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_NETWORK_TESTS") != "1", reason="explicit Docker integration run required"
)


def test_live_desktop_viewer_and_profile_persistence():
    directory = Path(__file__).parent / "generated" / "desktop-automated"
    config = FleetConfig.from_dict({"name": "bf-desktop-automated", "admin_cidr": "203.0.113.7/32"})
    manifest = render_worker(
        config,
        parse_proxies("proxy.test:1080:browser-one:test-password"),
        1,
        directory,
        {"proxy.test": "172.30.0.250"},
    )
    compose = json.loads((directory / "compose.json").read_text())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    compose["services"]["viewer"]["ports"] = [f"127.0.0.1:{port}:8080"]
    compose["services"]["proxy"] = {
        "image": "browser-fleet-gateway:local",
        "entrypoint": ["python3", "/test/proxy.py"],
        "volumes": [f"{Path(__file__).parent.resolve() / 'proxy.py'}:/test/proxy.py:ro"],
        "networks": {"egress": {"ipv4_address": "172.30.0.250"}},
    }
    path = directory / "compose.json"
    path.write_text(json.dumps(compose))
    command = ["docker", "compose", "-f", str(path)]
    try:
        subprocess.run(
            [*command, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120"], check=True
        )
        viewer_path = manifest["browsers"][0]["viewer_path"]
        for headers, expected in [
            ({}, 200),
            ({"Origin": "https://evil.example"}, 403),
            ({"Host": "evil.example"}, 403),
        ]:
            client = HTTPConnection("127.0.0.1", port, timeout=10)
            client.request("GET", f"{viewer_path}/vnc.html", headers=headers)
            response = client.getresponse()
            assert response.status == expected
            response.read()
            client.close()
        client = HTTPConnection("127.0.0.1", port, timeout=10)
        client.request("GET", "/wrong-token/browser/0001/vnc.html")
        assert client.getresponse().status == 404
        client.close()

        # The remote browser must actually render, not just leave a healthy VNC server.
        probe = [
            *command,
            "exec",
            "-T",
            "desktop-0001",
            "sh",
            "-c",
            "pgrep -f 'type=renderer' >/dev/null && test $(readlink /proc/1/ns/user) != $(pgrep -f 'type=renderer' | head -1 | xargs -I{} readlink /proc/{}/ns/user)",
        ]
        for _ in range(30):
            if subprocess.run(probe, capture_output=True).returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("Browser renderer did not start in its sandbox")
        subprocess.run(
            [
                *command,
                "exec",
                "-T",
                "desktop-0001",
                "sh",
                "-c",
                "printf persisted > /home/browser/persistence-test",
            ],
            check=True,
        )
        subprocess.run(
            [*command, "up", "-d", "--no-build", "--force-recreate", "--wait", "desktop-0001"],
            check=True,
        )
        result = subprocess.run(
            [*command, "exec", "-T", "desktop-0001", "cat", "/home/browser/persistence-test"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout == "persisted"
        container = subprocess.run(
            [*command, "ps", "-q", "desktop-0001"], check=True, capture_output=True, text=True
        ).stdout.strip()
        policy = subprocess.run(
            ["docker", "inspect", "--format", "{{.HostConfig.RestartPolicy.Name}}", container],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert policy == "no", (
            "Docker autostart must not bypass gateway readiness after a daemon restart"
        )
    finally:
        subprocess.run([*command, "down", "--volumes", "--timeout", "3"], check=True)
