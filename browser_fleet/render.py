import json
import os
import secrets
import shutil
import socket
import tempfile
from pathlib import Path

from .config import FleetConfig, Proxy

ASSETS = Path(__file__).parent / "assets"
NETWORK = "172.30.0.0/24"
VIEWER_IP = "172.30.0.2"


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def split_proxies(config: FleetConfig, proxies: list[Proxy]) -> list[list[Proxy]]:
    if len(proxies) < config.total_browsers:
        raise ValueError(f"Expected at least {config.total_browsers} proxies, found {len(proxies)}")
    count = config.browsers_per_instance
    return [proxies[i : i + count] for i in range(0, config.total_browsers, count)]


def resolve_proxy_hosts(proxies: list[Proxy]) -> dict[str, str]:
    addresses = {}
    for host in dict.fromkeys(p.host for p in proxies):
        records = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        addresses[host] = records[0][4][0]
    return addresses


def sing_box_config(proxy: Proxy, protocol: str, address: str) -> dict:
    upstream = {
        "type": "socks" if protocol == "socks5" else "http",
        "tag": "upstream",
        "server": address,
        "server_port": proxy.port,
        "username": proxy.username,
        "password": proxy.password,
        "bind_interface": "eth0",
    }
    if protocol == "socks5":
        upstream.update(version="5", network="tcp")
    if protocol == "https":
        upstream["tls"] = {"enabled": True, "server_name": proxy.host}
    return {
        "log": {"level": "warn", "timestamp": True},
        "dns": {
            "servers": [
                {
                    "type": "https",
                    "tag": "proxied-dns",
                    "server": "1.1.1.1",
                    "server_port": 443,
                    "path": "/dns-query",
                    "detour": "upstream",
                }
            ],
            "final": "proxied-dns",
            "strategy": "ipv4_only",
        },
        "inbounds": [
            {
                "type": "tun",
                "tag": "browser",
                "interface_name": "bf-tun",
                "address": ["198.18.0.1/30", "fdfe:dcba:9876::1/126"],
                "auto_route": True,
                "strict_route": True,
                "stack": "gvisor",
                "route_exclude_address": [NETWORK, f"{address}/32"],
            }
        ],
        "outbounds": [upstream],
        "route": {
            "default_interface": "eth0",
            "final": "upstream",
            "rules": [
                {"port": 53, "action": "hijack-dns"},
                {"network": "udp", "action": "reject"},
                {"ip_is_private": True, "action": "reject"},
            ],
        },
    }


def firewall_rules(proxy: Proxy, address: str) -> str:
    return f"""table inet browser_fleet {{
  chain input {{
    type filter hook input priority -150; policy drop;
    iifname "lo" accept
    ct state established,related accept
    iifname "bf-tun" accept
    ip saddr {VIEWER_IP} tcp dport 6080 accept
  }}
  chain forward {{
    type filter hook forward priority -150; policy drop;
  }}
  chain output {{
    type filter hook output priority -150; policy drop;
    ip daddr 127.0.0.11 drop
    oifname "lo" accept
    oifname "bf-tun" accept
    meta skuid 0 ip daddr {address} tcp dport {proxy.port} oifname "eth0" accept
    ip daddr {VIEWER_IP} tcp sport 6080 ct state established oifname "eth0" accept
  }}
}}
"""


def render_worker(
    config: FleetConfig,
    proxies: list[Proxy],
    worker: int,
    destination: Path,
    addresses: dict[str, str],
) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    destination.chmod(0o700)
    token_path = destination / "viewer-token"
    if not token_path.exists():
        write_private(token_path, secrets.token_urlsafe(32))
    viewer_token = token_path.read_text().strip()
    for child in destination.iterdir():
        if child != token_path:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    for name in ("gateway", "desktop", "worker"):
        source = ASSETS / name
        if source.exists():
            shutil.copytree(source, destination / name, dirs_exist_ok=True)
    services = {}
    volumes = {}
    sessions = []
    locations = []
    for offset, proxy in enumerate(proxies):
        browser_id = f"{(worker - 1) * config.browsers_per_instance + offset + 1:04d}"
        session_dir = destination / "sessions" / browser_id
        address = addresses[proxy.host]
        write_private(
            session_dir / "sing-box.json",
            json.dumps(sing_box_config(proxy, config.proxy_protocol, address), indent=2),
        )
        write_private(session_dir / "firewall.nft", firewall_rules(proxy, address))
        (session_dir / "resolv.conf").write_text(
            "nameserver 198.18.0.2\noptions timeout:2 attempts:2\n"
        )
        (session_dir / "resolv.conf").chmod(0o644)
        network_service = f"net-{browser_id}"
        services[network_service] = {
            "build": "./gateway",
            "image": "browser-fleet-gateway:local",
            "restart": "unless-stopped",
            "cap_drop": ["ALL"],
            "cap_add": ["NET_ADMIN", "NET_RAW"],
            "devices": ["/dev/net/tun:/dev/net/tun"],
            "security_opt": ["no-new-privileges:true"],
            "networks": {"egress": {"ipv4_address": f"172.30.0.{offset + 10}"}},
            "volumes": [
                f"./sessions/{browser_id}/sing-box.json:/etc/sing-box/config.json:ro",
                f"./sessions/{browser_id}/firewall.nft:/etc/browser-fleet/firewall.nft:ro",
            ],
            "healthcheck": {
                "test": [
                    "CMD-SHELL",
                    "ip link show bf-tun >/dev/null && nft list table inet browser_fleet >/dev/null",
                ],
                "interval": "5s",
                "timeout": "3s",
                "retries": 12,
            },
            "mem_limit": "256m",
            "cpus": 0.5,
        }
        volumes[f"home-{browser_id}"] = {}
        services[f"desktop-{browser_id}"] = {
            "build": "./desktop",
            "image": "browser-fleet-desktop:local",
            "restart": "no",
            "network_mode": f"service:{network_service}",
            "depends_on": {network_service: {"condition": "service_healthy", "restart": True}},
            "user": "1000:1000",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true", "seccomp=./desktop/chrome-seccomp.json"],
            "shm_size": "512m",
            "mem_limit": f"{config.browser_memory_mb}m",
            "cpus": config.browser_cpus,
            "pids_limit": 512,
            "healthcheck": {
                "test": ["CMD", "curl", "-fsS", "http://127.0.0.1:6080/vnc.html"],
                "interval": "15s",
                "timeout": "5s",
                "retries": 6,
            },
            "environment": {
                "START_URL": config.start_url.replace("$", "$$"),
                "SCREEN_WIDTH": str(config.screen_width),
                "SCREEN_HEIGHT": str(config.screen_height),
                "BROWSER_ID": browser_id,
            },
            "volumes": [
                f"home-{browser_id}:/home/browser",
                f"./sessions/{browser_id}/resolv.conf:/etc/resolv.conf:ro",
            ],
        }
        locations.append(f"""location /{viewer_token}/browser/{browser_id}/ {{
  proxy_pass http://172.30.0.{offset + 10}:6080/;
  proxy_http_version 1.1;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection $connection_upgrade;
  proxy_read_timeout 86400;
  proxy_buffering off;
}}""")
        sessions.append(
            {
                "id": browser_id,
                "worker": worker,
                "proxy_host": proxy.host,
                "proxy_port": proxy.port,
                "protocol": config.proxy_protocol,
                "viewer_path": f"/{viewer_token}/browser/{browser_id}",
            }
        )
    services["viewer"] = {
        "image": "nginx:1.28-alpine",
        "restart": "unless-stopped",
        "ports": ["127.0.0.1:8080:8080"],
        "networks": {"egress": {"ipv4_address": VIEWER_IP}},
        "volumes": ["./nginx.conf:/etc/nginx/conf.d/default.conf:ro"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": "128m",
    }
    compose = {
        "name": f"{config.name}-worker-{worker:03d}",
        "services": services,
        "volumes": volumes,
        "networks": {"egress": {"ipam": {"config": [{"subnet": NETWORK}]}}},
    }
    (destination / "compose.json").write_text(json.dumps(compose, indent=2))
    (destination / "nginx.conf").write_text(
        'map $http_upgrade $connection_upgrade { default upgrade; "" close; }\n'
        'map $http_origin $allowed_origin { default 0; "" 1; ~^http://(127\\.0\\.0\\.1|localhost)(:[0-9]+)?$ 1; }\n'
        "server { listen 8080; server_tokens off; access_log off;\n"
        "if ($host !~ ^(127\\.0\\.0\\.1|localhost)$) { return 403; }\n"
        "if ($allowed_origin = 0) { return 403; }\n"
        "add_header Referrer-Policy no-referrer always;\n"
        'location = /health { return 200 "viewer ready\\n"; }\n'
        + "\n".join(locations)
        + "\nlocation / { return 404; }\n}\n"
    )
    manifest = {"worker": worker, "browsers": sessions}
    write_private(destination / "manifest.json", json.dumps(manifest, indent=2))
    return manifest
