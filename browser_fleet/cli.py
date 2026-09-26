import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from dataclasses import asdict
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from .aws import (
    aws_session,
    check_deployment,
    check_instance_capacity,
    outputs,
    refresh_workers,
    terraform,
    terraform_environment,
)
from .config import load_config, parse_proxies
from .render import ASSETS, render_worker, resolve_proxy_hosts, split_proxies, write_private


def state_directory(config_path: Path, name: str) -> Path:
    directory = config_path.resolve().parent / ".browser-fleet" / name
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory


def fingerprint(config, proxies) -> str:
    settings = asdict(config)
    for name in ("aws_profile", "admin_cidr", "proxy_file", "start_url"):
        settings.pop(name)
    payload = {"settings": settings, "proxies": [asdict(proxy) for proxy in proxies]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def root_owned_archive_member(member: tarfile.TarInfo) -> tarfile.TarInfo:
    # Host user IDs must not become owners of private files inside Linux containers.
    member.uid = member.gid = 0
    member.uname = member.gname = "root"
    return member


def render_bundles(config, proxies, directory: Path, addresses: dict) -> dict:
    groups = split_proxies(config, proxies)
    directory.mkdir(parents=True, exist_ok=True)
    for child in directory.iterdir():
        match = re.fullmatch(r"worker-(\d+)(?:\.tar\.gz)?", child.name)
        if match and int(match[1]) > config.instances:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    manifest = {"name": config.name, "browsers": []}
    for worker, group in enumerate(groups, 1):
        worker_dir = directory / f"worker-{worker:03d}"
        worker_manifest = render_worker(config, group, worker, worker_dir, addresses)
        manifest["browsers"].extend(worker_manifest["browsers"])
        archive = directory / f"worker-{worker:03d}.tar.gz"
        with tarfile.open(archive, "w:gz") as file:
            for path in sorted(worker_dir.iterdir()):
                file.add(path, arcname=path.name, filter=root_owned_archive_member)
        archive.chmod(0o600)
    write_private(directory / "manifest.json", json.dumps(manifest, indent=2))
    return manifest


def deploy(config, proxies, directory: Path, session, environment: dict, account: str, yes: bool):
    instance = session.client("ec2").describe_instance_types(InstanceTypes=[config.instance_type])[
        "InstanceTypes"
    ][0]
    check_instance_capacity(config, instance)
    saved_path = directory / "deployment.json"
    saved = json.loads(saved_path.read_text()) if saved_path.exists() else None
    digest = fingerprint(config, proxies)
    if saved:
        check_deployment(saved, account, config.region)
        if saved["fingerprint"] != digest:
            raise ValueError(
                "This fleet's layout/proxies/runtime settings are fixed to protect saved profiles. Use a new fleet name for a changed layout; admin_cidr, aws_profile and start_url may change."
            )
    addresses = saved["proxy_addresses"] if saved else resolve_proxy_hosts(proxies)
    render_bundles(config, proxies, directory / "bundles", addresses)
    infra = directory / "infra"
    shutil.copytree(
        ASSETS / "infra", infra, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".terraform")
    )
    key = directory / "id_ed25519"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    variables = {
        name: getattr(config, name)
        for name in ("name", "region", "admin_cidr", "instances", "instance_type", "disk_gb")
    }
    variables["ssh_public_key"] = key.with_suffix(".pub").read_text().strip()
    write_private(infra / "terraform.tfvars.json", json.dumps(variables, indent=2))
    deployment = {
        "account": account,
        "region": config.region,
        "fingerprint": digest,
        "proxy_addresses": addresses,
    }
    write_private(saved_path, json.dumps(deployment, indent=2))
    print(
        f"Deploying {config.total_browsers} browsers on {config.instances} {config.instance_type} instances in {config.region}; AWS account {account}."
    )
    print("Terraform will show the resources and ask before creating billable infrastructure.")
    terraform(infra, ["init", "-input=false"], environment)
    terraform(infra, ["apply", *(["-auto-approve"] if yes else [])], environment)
    deployed = outputs(infra, environment)
    s3 = session.client("s3")
    for worker in range(1, config.instances + 1):
        filename = f"worker-{worker:03d}.tar.gz"
        s3.upload_file(
            str(directory / "bundles" / filename),
            deployed["bucket"],
            filename,
            ExtraArgs={"ServerSideEncryption": "AES256"},
        )
    print("Bundles uploaded. New hosts install Docker and build desktop images on first boot.")
    print("Use 'browser-fleet status' to inspect hosts and 'browser-fleet open' to connect.")
    print("If a previous bootstrap failed, use 'browser-fleet repair --worker N'.")


def ssh_arguments(directory: Path, host: str) -> list[str]:
    return [
        "ssh",
        "-i",
        str(directory / "id_ed25519"),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={directory / 'known_hosts'}",
        "-o",
        "ConnectTimeout=10",
        f"ubuntu@{host}",
    ]


def main():
    parser = argparse.ArgumentParser(
        description="AWS browser desktops with one isolated proxy per browser"
    )
    parser.add_argument(
        "command",
        choices=[
            "validate",
            "render",
            "deploy",
            "status",
            "open",
            "stop",
            "start",
            "destroy",
            "repair",
        ],
    )
    parser.add_argument("--config", type=Path, default=Path("fleet.json"))
    parser.add_argument(
        "--yes", action="store_true", help="Skip deployment/stop/destroy confirmation"
    )
    parser.add_argument("--worker", type=int, help="Worker number for repair")
    parser.add_argument("--port", type=int, default=8787, help="Local dashboard port")
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not launch the local web browser"
    )
    arguments = parser.parse_args()
    os.umask(0o077)
    try:
        run(arguments)
    except KeyboardInterrupt:
        if arguments.command in {"open", "status", "validate", "render"}:
            message = "Stopped local command. Remote desktops are unchanged."
        else:
            message = "Interrupted. AWS resources may already have changed. Keep the saved state, inspect status/Terraform, and rerun the command to reconcile."
        print(f"\n{message}", file=sys.stderr)
        return 130
    except (
        ValueError,
        OSError,
        BotoCoreError,
        ClientError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        if isinstance(error, subprocess.CalledProcessError):
            message = f"{Path(error.cmd[0]).name} exited with status {error.returncode}; inspect its output above"
        elif isinstance(error, ClientError):
            message = f"AWS {error.operation_name}: {error.response['Error']['Code']}"
        else:
            message = str(error)
        print(f"Error: {message}", file=sys.stderr)
        return 1
    return 0


def run(arguments):
    config = load_config(arguments.config)
    if arguments.command in {"validate", "render", "deploy"}:
        proxies = parse_proxies(config.proxy_file.read_text(), limit=config.total_browsers)
        split_proxies(config, proxies)
        if arguments.command == "validate":
            print(
                f"Valid: {config.instances} instance(s), {config.browsers_per_instance} browser(s) each, {config.total_browsers} proxies ({config.proxy_protocol})."
            )
            return
    directory = state_directory(arguments.config, config.name)
    if arguments.command == "render":
        render_bundles(config, proxies, directory / "preview", resolve_proxy_hosts(proxies))
        print(
            f"Rendered private preview bundles in {directory / 'preview'}; no AWS resources created."
        )
        return
    session = aws_session(config)
    account = session.client("sts").get_caller_identity()["Account"]
    credentials = session.get_credentials().get_frozen_credentials()
    environment = terraform_environment(credentials, config.region)
    if arguments.command == "deploy":
        with (directory / "lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            deploy(config, proxies, directory, session, environment, account, arguments.yes)
        return
    saved_path = directory / "deployment.json"
    if not saved_path.exists():
        raise ValueError("No saved deployment. Run deploy first.")
    check_deployment(json.loads(saved_path.read_text()), account, config.region)
    infra = directory / "infra"
    if arguments.command == "destroy":
        print(
            "Destroy removes all fleet desktops, saved browser profiles, disks, proxy bundles, and generated local state."
        )
        with (directory / "lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            terraform(
                infra, ["destroy", *(["-auto-approve"] if arguments.yes else [])], environment
            )
        shutil.rmtree(directory)
        print(
            "Fleet destroyed and generated local state removed. Original config and proxy files remain."
        )
        return
    deployed = outputs(infra, environment)
    if not deployed.get("workers"):
        raise ValueError("No deployed workers. Run deploy to create the fleet.")
    workers = refresh_workers(session, deployed["workers"])
    if arguments.command == "status":
        for worker in workers:
            print(
                f"Worker {worker['index']:03d}  {worker['id']}  {worker['state']}  {worker['public_ip'] or '-'}"
            )
            if worker["state"] == "running" and worker["public_ip"]:
                subprocess.run(
                    [
                        *ssh_arguments(directory, worker["public_ip"]),
                        "sudo systemctl is-active browser-fleet; sudo docker compose -f /opt/browser-fleet/compose.json ps --format table",
                    ],
                    timeout=20,
                )
    elif arguments.command in {"stop", "start"}:
        if arguments.command == "stop" and not arguments.yes:
            if (
                input("Stop all desktops? Open sessions end; profiles remain on disk. Type stop: ")
                != "stop"
            ):
                return
        method = getattr(session.client("ec2"), f"{arguments.command}_instances")
        method(InstanceIds=[worker["id"] for worker in workers])
        print(
            f"Requested {arguments.command} for {len(workers)} workers. Use status to check progress."
        )
    elif arguments.command == "repair":
        worker = next((w for w in workers if w["index"] == arguments.worker), None)
        if not worker or not worker["public_ip"]:
            raise ValueError("repair requires --worker N for a running worker")
        subprocess.run(
            [
                *ssh_arguments(directory, worker["public_ip"]),
                "sudo cloud-init status --wait; sudo bash -c 'set -eu; systemctl stop browser-fleet || true; python3 /opt/browser-fleet/fetch.py; tar -xzf /opt/browser-fleet/bundle.tar.gz -C /opt/browser-fleet; bash /opt/browser-fleet/worker/install.sh'",
            ],
            check=True,
        )
    elif arguments.command == "open":
        from .dashboard import serve

        manifest = json.loads((directory / "bundles/manifest.json").read_text())
        serve(directory, workers, manifest, arguments.port, not arguments.no_browser)
