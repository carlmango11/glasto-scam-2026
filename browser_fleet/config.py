import json
import re
from dataclasses import dataclass, field, fields
from ipaddress import IPv4Network
from pathlib import Path


@dataclass(frozen=True)
class Proxy:
    host: str
    port: int
    username: str = field(repr=False)
    password: str = field(repr=False)


def parse_proxies(text: str, limit: int | None = None) -> list[Proxy]:
    proxies = []
    seen = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split(":", 3)
        if (
            len(parts) != 4
            or not all(parts)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*", parts[0])
            or not parts[1].isascii()
            or not parts[1].isdigit()
            or not 1 <= int(parts[1]) <= 65535
            or any(ord(c) < 32 or ord(c) == 127 for c in line)
        ):
            raise ValueError(
                f"Invalid proxy at line {line_number}; expected host:port:username:password"
            )
        proxy = Proxy(parts[0].lower(), int(parts[1]), parts[2], parts[3])
        if proxy in seen:
            raise ValueError(f"Duplicate proxy at line {line_number}")
        seen.add(proxy)
        proxies.append(proxy)
        if len(proxies) == limit:
            break
    if not proxies:
        raise ValueError("Proxy file is empty")
    return proxies


@dataclass(frozen=True)
class FleetConfig:
    name: str
    admin_cidr: str
    region: str = "eu-west-2"
    aws_profile: str | None = None
    instances: int = 1
    browsers_per_instance: int = 1
    instance_type: str = "m7i.2xlarge"
    disk_gb: int = 100
    proxy_file: Path = Path("proxies.txt")
    proxy_protocol: str = "socks5"
    browser_memory_mb: int = 2048
    browser_cpus: float = 1.0
    screen_width: int = 1440
    screen_height: int = 900

    @property
    def total_browsers(self) -> int:
        return self.instances * self.browsers_per_instance

    @classmethod
    def from_dict(cls, values: dict, base: Path = Path(".")) -> "FleetConfig":
        unknown = values.keys() - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(
                "Unknown configuration fields; see fleet.example.json (credentials use AWS profiles)"
            )
        try:
            config = cls(
                **{
                    **values,
                    "proxy_file": (base / values.get("proxy_file", "proxies.txt")).resolve(),
                }
            )
        except TypeError as error:
            raise ValueError("Configuration requires name and admin_cidr") from error
        for name in ("name", "region", "instance_type", "proxy_protocol", "admin_cidr"):
            if not isinstance(getattr(config, name), str):
                raise ValueError(f"{name} must be a string")
        if config.aws_profile is not None and (
            not isinstance(config.aws_profile, str) or not config.aws_profile
        ):
            raise ValueError("aws_profile must be a nonempty string or null")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", config.name):
            raise ValueError(
                "name must contain 1–31 lowercase letters, digits or hyphens, starting with a letter"
            )
        limits = {
            "instances": (1, 100),
            "browsers_per_instance": (1, 200),
            "disk_gb": (30, 16384),
            "browser_memory_mb": (512, 65536),
            "screen_width": (800, 3840),
            "screen_height": (600, 2160),
        }
        for name, (minimum, maximum) in limits.items():
            value = getattr(config, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
        if type(config.browser_cpus) not in (int, float) or not 0.25 <= config.browser_cpus <= 32:
            raise ValueError("browser_cpus must be between 0.25 and 32")
        if config.proxy_protocol not in {"socks5", "https", "http"}:
            raise ValueError("proxy_protocol must be socks5, https or http")
        try:
            network = IPv4Network(config.admin_cidr)
        except (ValueError, TypeError) as error:
            raise ValueError(
                "admin_cidr must be an IPv4 network, normally your public IP/32"
            ) from error
        if network.prefixlen < 24:
            raise ValueError("admin_cidr must be /24 or narrower; use your public IP/32")
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", config.region):
            raise ValueError("Invalid AWS region")
        if not re.fullmatch(r"[a-z0-9]+\.[a-z0-9]+", config.instance_type):
            raise ValueError("Invalid EC2 instance_type")
        return config


def load_config(path: Path) -> FleetConfig:
    values = json.loads(path.read_text())
    if not isinstance(values, dict):
        raise ValueError("Configuration must be a JSON object")
    return FleetConfig.from_dict(values, path.resolve().parent)
