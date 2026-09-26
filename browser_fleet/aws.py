import json
import os
import subprocess
from pathlib import Path

import boto3

from .config import FleetConfig


def terraform_environment(credentials, region: str, base: dict | None = None) -> dict:
    environment = dict(os.environ if base is None else base)
    for key in list(environment):
        if key.startswith("AWS_"):
            del environment[key]
    environment.update(
        AWS_ACCESS_KEY_ID=credentials.access_key,
        AWS_SECRET_ACCESS_KEY=credentials.secret_key,
        AWS_DEFAULT_REGION=region,
        AWS_EC2_METADATA_DISABLED="true",
    )
    if credentials.token:
        environment["AWS_SESSION_TOKEN"] = credentials.token
    return environment


def aws_session(config: FleetConfig):
    return boto3.Session(profile_name=config.aws_profile, region_name=config.region)


def check_instance_capacity(config: FleetConfig, instance: dict) -> None:
    if "x86_64" not in instance["ProcessorInfo"]["SupportedArchitectures"]:
        raise ValueError(
            "This fleet requires x86_64 EC2 instances for Google Chrome; ARM/Graviton is not supported"
        )
    required_memory = 2048 + config.browsers_per_instance * (config.browser_memory_mb + 256)
    if instance["MemoryInfo"]["SizeInMiB"] < required_memory:
        raise ValueError(
            f"Instance memory is too small for the configured limits: need at least {required_memory} MiB per host. Choose a larger instance or reduce browsers_per_instance/browser_memory_mb."
        )


def check_deployment(saved: dict, account: str, region: str) -> None:
    if saved["account"] != account or saved["region"] != region:
        raise ValueError(
            "AWS account/region differs from the saved deployment; use its original credentials and region"
        )


def terraform(directory: Path, arguments: list[str], environment: dict, capture=False):
    return subprocess.run(
        ["terraform", f"-chdir={directory}", *arguments],
        env=environment,
        check=True,
        capture_output=capture,
        text=True,
    )


def outputs(directory: Path, environment: dict) -> dict:
    values = json.loads(terraform(directory, ["output", "-json"], environment, capture=True).stdout)
    return {name: value["value"] for name, value in values.items()}


def refresh_workers(session, workers: list[dict]) -> list[dict]:
    response = session.client("ec2").describe_instances(InstanceIds=[w["id"] for w in workers])
    instances = {i["InstanceId"]: i for r in response["Reservations"] for i in r["Instances"]}
    return [
        {
            **worker,
            "public_ip": instances[worker["id"]].get("PublicIpAddress"),
            "state": instances[worker["id"]]["State"]["Name"],
        }
        for worker in workers
    ]
