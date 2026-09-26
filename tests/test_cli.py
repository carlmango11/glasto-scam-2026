import json
import subprocess
import sys

import pytest

from browser_fleet.aws import check_deployment, check_instance_capacity, terraform_environment
from browser_fleet.config import FleetConfig


def test_render_only_resolves_and_bundles_required_proxies(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from browser_fleet import cli

    path = tmp_path / "fleet.json"
    path.write_text('{"name":"demo","admin_cidr":"203.0.113.7/32"}')
    (tmp_path / "proxies.txt").write_text("used.example:1080:u:first\nunused.example:1080:u:second")
    resolved = []
    bundled = []

    def resolve(proxies):
        resolved.extend(proxies)
        return {proxy.host: "198.51.100.1" for proxy in proxies}

    def render(config, proxies, directory, addresses):
        bundled.extend(proxies)

    monkeypatch.setattr(cli, "resolve_proxy_hosts", resolve)
    monkeypatch.setattr(cli, "render_bundles", render)
    cli.run(SimpleNamespace(command="render", config=path))
    assert [proxy.host for proxy in resolved] == ["used.example"]
    assert bundled == resolved


def test_terraform_uses_exact_resolved_credentials_without_ambient_profiles():
    class Credentials:
        access_key = "test-key"
        secret_key = "test-secret"
        token = "test-token"

    env = terraform_environment(
        Credentials(),
        "eu-west-2",
        {"PATH": "/bin", "AWS_PROFILE": "wrong", "AWS_WEB_IDENTITY_TOKEN_FILE": "/wrong"},
    )
    assert env["AWS_ACCESS_KEY_ID"] == "test-key"
    assert env["AWS_SESSION_TOKEN"] == "test-token"
    assert "AWS_PROFILE" not in env
    assert "AWS_WEB_IDENTITY_TOKEN_FILE" not in env
    assert env["PATH"] == "/bin"


def test_deployment_rejects_different_aws_account():
    import pytest

    with pytest.raises(ValueError, match="account"):
        check_deployment({"account": "123", "region": "eu-west-2"}, "456", "eu-west-2")


def test_validate_does_not_need_aws_or_expose_passwords(tmp_path):
    (tmp_path / "proxies.txt").write_text("example.com:1080:user:secret-value")
    path = tmp_path / "fleet.json"
    path.write_text(json.dumps({"name": "demo", "admin_cidr": "203.0.113.7/32"}))
    result = subprocess.run(
        [sys.executable, "-m", "browser_fleet", "validate", "--config", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "1 browser" in result.stdout
    assert "secret-value" not in result.stdout + result.stderr


def test_config_failure_is_actionable_without_traceback(tmp_path):
    path = tmp_path / "fleet.json"
    path.write_text('{"name":"demo","admin_cidr":"0.0.0.0/0"}')
    result = subprocess.run(
        [sys.executable, "-m", "browser_fleet", "validate", "--config", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "admin_cidr" in result.stderr
    assert "Traceback" not in result.stderr


def test_runtime_assets_are_packaged():
    from browser_fleet.render import ASSETS

    for name in (
        "infra/main.tf",
        "gateway/Dockerfile",
        "desktop/chrome-seccomp.json",
        "web/index.html",
    ):
        assert (ASSETS / name).is_file(), name


def test_arm_instances_fail_before_provisioning():
    import pytest

    config = FleetConfig.from_dict({"name": "demo", "admin_cidr": "203.0.113.7/32"})
    with pytest.raises(ValueError, match="x86_64"):
        check_instance_capacity(config, {"ProcessorInfo": {"SupportedArchitectures": ["arm64"]}})


def test_insufficient_instance_memory_is_rejected():
    import pytest

    config = FleetConfig.from_dict(
        {"name": "demo", "admin_cidr": "203.0.113.7/32", "browsers_per_instance": 20}
    )
    with pytest.raises(ValueError, match="memory"):
        check_instance_capacity(
            config,
            {
                "ProcessorInfo": {"SupportedArchitectures": ["x86_64"]},
                "MemoryInfo": {"SizeInMiB": 16384},
            },
        )


@pytest.mark.parametrize("failure", [False, True])
def test_destroy_preserves_recovery_state_until_aws_removal_succeeds(
    tmp_path, monkeypatch, failure
):
    from types import SimpleNamespace

    from browser_fleet import cli

    path = tmp_path / "fleet.json"
    path.write_text('{"name":"demo","admin_cidr":"203.0.113.7/32"}')
    directory = cli.state_directory(path, "demo")
    (directory / "deployment.json").write_text('{"account":"123","region":"eu-west-2"}')
    credentials = SimpleNamespace(access_key="dummy", secret_key="dummy", token=None)
    session = SimpleNamespace(
        client=lambda _: SimpleNamespace(get_caller_identity=lambda: {"Account": "123"}),
        get_credentials=lambda: SimpleNamespace(get_frozen_credentials=lambda: credentials),
    )
    monkeypatch.setattr(cli, "aws_session", lambda _: session)

    def terraform(infra, args, environment):
        assert infra == directory / "infra"
        assert args == ["destroy", "-auto-approve"]
        if failure:
            raise subprocess.CalledProcessError(1, ["terraform", "destroy"])

    monkeypatch.setattr(cli, "terraform", terraform)
    arguments = SimpleNamespace(command="destroy", config=path, yes=True)
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            cli.run(arguments)
        assert (directory / "deployment.json").exists()
    else:
        cli.run(arguments)
        assert not directory.exists()
    assert path.exists()


@pytest.mark.parametrize(
    "command,expected", [("open", "unchanged"), ("deploy", "may already have changed")]
)
def test_interrupt_message_reflects_possible_remote_changes(monkeypatch, capsys, command, expected):
    from browser_fleet import cli

    def interrupted(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(sys, "argv", ["browser-fleet", command])
    monkeypatch.setattr(cli, "run", interrupted)
    assert cli.main() == 130
    assert expected in capsys.readouterr().err
