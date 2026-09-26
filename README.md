# Browser Fleet

Run human-operated Chrome desktops across AWS EC2, with one proxy per browser.
A local dashboard lets you search and switch between desktops. Each has its own
persistent profile, Linux desktop and isolated network namespace.

## Quick start

Requirements on your Mac/Linux operator machine: Python 3.11+, Terraform 1.7+,
OpenSSH (`ssh` and `ssh-keygen`), and AWS credentials. Docker is required locally
only for integration tests; the deployment installs it on EC2.
The credential setup example below uses the AWS CLI.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# Use your normal AWS credential setup. SSO profiles also work.
aws configure --profile browser-fleet

cp fleet.example.json fleet.json
# Edit fleet.json: set admin_cidr to YOUR_PUBLIC_IPV4/32 and choose sizing.
# Create proxies.txt with one host:port:username:password entry per browser.

browser-fleet validate
browser-fleet deploy
browser-fleet status
browser-fleet open
```

`deploy` shows a Terraform plan and asks for confirmation before creating AWS
resources. `--yes` skips that prompt. First boot downloads packages and builds
images; provisioning completing does not mean the desktops are ready yet.
Use `status` to see container health. Keep `open` running while using the dashboard.
Ctrl+C closes local connections; it does not stop the remote browsers.

The sample configuration specifies **10 instances × 20 browsers = 200 browsers**.
The instance type is an initial sizing choice, not a measured capacity guarantee.
Start with one host and your real pages, then choose the production fleet size.
Browser memory and CPU limits are configurable. The CLI rejects ARM instances
and hosts too small to fit the configured memory limits plus host/gateway overhead.

## Configuration

See [fleet.example.json](fleet.example.json). All commands accept
`--config /path/to/fleet.json`. Paths inside the configuration are relative to it.

| Setting | Purpose |
|---|---|
| `aws_profile` | AWS profile, including SSO. Omit to use the default AWS credential chain. |
| `region` | AWS region, default `eu-west-2`. |
| `admin_cidr` | Operator's public IP/CIDR allowed to SSH; `/32` recommended, `/24` is the broadest accepted. |
| `instances` | Number of EC2 hosts. |
| `browsers_per_instance` | Number of independent desktops on each host. |
| `instance_type` | An x86_64 EC2 type; fleet uses Ubuntu 22.04 and Google Chrome. |
| `disk_gb` | Encrypted gp3 root disk size per host, including all profiles. |
| `proxy_file` | File with exactly `instances × browsers_per_instance` proxy lines. |
| `proxy_protocol` | `socks5` (default), `https`, or `http`; one protocol for the fleet. |
| `browser_memory_mb` / `browser_cpus` | Per-desktop limits. Each also reserves up to 512 MB shared memory within its memory limit. |
| `screen_width` / `screen_height` | Initial desktop size; the viewer can resize it. |

The AWS identity needs permission to manage the resources declared in
`browser_fleet/assets/infra`: EC2/VPC/security groups/key pairs, IAM roles and
instance profiles (including PassRole), and S3 buckets/objects. It also needs STS
GetCallerIdentity and EC2 DescribeInstanceTypes/DescribeInstances. Organizational
policies and regional EC2/vCPU quotas still apply. Credentials are resolved locally
and passed to Terraform through its environment, never saved in this project.

### Proxy files

```text
proxy.example.com:12321:my-user:my-password_country-gb_session-one
proxy.example.com:12321:my-user:my-password_country-gb_session-two
```

Lines are assigned in order: host 1 gets the first N, host 2 gets the next N.
Blank lines are ignored. Duplicate entries and missing/extra entries are errors.
Passwords may contain colons and shell characters; no shell interpolation is used.
Backslashes are literal: don't add Markdown `\_` escaping unless a backslash is
actually part of your password. Usernames cannot contain a colon in this format.
Use hostnames or IPv4 literals; bracketed IPv6 proxy endpoints aren't supported.

`https` means **TLS to the proxy itself**, with certificate/hostname verification.
Some vendors call a plaintext HTTP CONNECT endpoint an “HTTPS proxy” because it
can carry HTTPS websites. Use `http` for that endpoint, or use `socks5`.
SOCKS5 does not encrypt its own authentication/transport; the website's HTTPS
remains end-to-end encrypted. Use a TLS proxy endpoint when you need encrypted
proxy authentication. There is no protocol autodetection or insecure TLS fallback.

An upstream hostname is resolved on the operator machine at deployment and its
IPv4 address is pinned. This is infrastructure DNS, separate from browser DNS.
To change a pinned endpoint, create a new fleet; there is no automatic endpoint
rotation. Different proxy credentials do not guarantee different exit IPs:
the provider controls exit assignment and session expiry.

## Network isolation

```text
Your browser → localhost dashboard → SSH tunnels → private nginx/noVNC viewers

Per EC2 host:
  Desktop 1 → namespace 1 → sing-box TUN → assigned proxy 1 → internet
  Desktop 2 → namespace 2 → sing-box TUN → assigned proxy 2 → internet
  …
```

Each desktop is a non-root container with no Linux capabilities and no privilege
escalation. Chrome keeps its user-namespace sandbox. The gateway alone has the
network capabilities and TUN device. Its nftables policy is installed before a
desktop starts, and firewall replacement is atomic.

- Browser TCP goes through its assigned SOCKS5/CONNECT proxy.
- DNS, including ordinary UDP DNS, is intercepted and resolved using
  **Cloudflare DoH through that same proxy**. Docker's embedded resolver is blocked.
- Other UDP, including direct QUIC and WebRTC UDP, is blocked. HTTP normally falls
  back to TCP; video calls and other UDP-dependent features may not work.
- IPv6 has no direct internet fallback; destination DNS currently requests IPv4.
- Direct access to the host, other desktops and EC2 metadata is blocked.
- If a gateway fails or its TUN disappears, the browser loses connectivity.
- The only direct desktop exception is replies to its worker's private viewer.

These are full Linux desktop **containers sharing a host kernel**, not one VM per
browser. EC2/host administrators and the proxy gateway are trusted. This protects
against routing bypasses by ordinary browser/desktop processes; it does not claim
protection against a host-kernel exploit or a compromised host administrator.

Desktop ports bind to EC2 loopback only. SSH forwards bind to operator loopback
only. The viewer also checks Host/Origin and requires an unguessable URL token to
protect against unrelated web pages connecting to local WebSockets. Don't share
viewer URLs or forward them publicly. Local processes on the operator machine
are trusted. The initial SSH host key uses OpenSSH trust-on-first-use, then pins
it in the fleet's `known_hosts` file.

## Lifecycle and saved profiles

```bash
browser-fleet status
browser-fleet stop             # Ends processes; encrypted disks/profiles remain.
browser-fleet start            # Starts hosts; reopen the dashboard after readiness.
browser-fleet open --port 8788  # Alternate local port.
browser-fleet repair --worker 1
browser-fleet destroy          # Permanently deletes the fleet, disks, and profiles.
```

Each desktop's home is a named Docker volume on its host's root disk. It survives
container recreation and EC2 stop/start, but **not instance termination or destroy**.
There is no automatic backup. Snapshot the EBS volumes before destructive operations.
Live tabs/processes don't survive stop/start; browser-saved data does.

The host supervisor starts desktops only after firewall readiness and recreates
a gateway/desktop pair together when unhealthy. Desktops deliberately have no
Docker daemon restart policy: starting them independently could bypass readiness
during a host reboot. Closing a browser window leaves the desktop available; use
the desktop's application menu to reopen Chrome.

Layout, proxy assignments, instance size, and runtime settings are fixed after
deployment to avoid silently attaching a saved profile to a different proxy.
Use another fleet name for changes. `admin_cidr` and the local `aws_profile` can
change; deploy again to update the SSH allowlist. The AWS account and region must
match the saved deployment. Routine AMI/user-data refreshes do not replace hosts.

`repair` waits for cloud-init, downloads the last uploaded runtime bundle, rebuilds
images and restarts that host's desktops without deleting their home volumes.
After a source update, `deploy` uploads new bundles; use `repair` on each host to
apply them. Rebuilding installs current stable Chrome. For a failed initial
bootstrap, inspect `/var/log/cloud-init-output.log` over SSH. Repair requires the
initial package/bootstrap setup to have reached creation of `fetch.py`.

Keep `.browser-fleet/<name>/`: it holds Terraform state, the SSH key, viewer tokens,
private bundles, and account binding. It is ignored by Git and owner-only on disk.
Don't delete it while resources exist. Proxy secrets are in private local bundles
and encrypted, non-public S3 objects, **not Terraform state or EC2 user-data**.
Worker IAM roles can read only their own bundle. The operator still needs to
protect local files and AWS access.
After a successful `destroy`, the generated local state and secret bundles are
removed too. Your original configuration and proxy input file remain.

## Cost and scaling

This provisions On-Demand EC2 instances, gp3 EBS volumes and public IPv4 addresses,
plus a small S3 bucket. AWS data-transfer and proxy-provider charges also apply.
There is no NAT Gateway, load balancer, EKS cluster or autoscaling. Stopping EC2
does not stop EBS storage charges. `destroy` removes the provisioned resources.
Check regional pricing and quotas before deploying 200 browsers. Session density
depends heavily on the pages and number of active viewers; no 200-browser load
test has been performed.

## Verification

```bash
pip install -e '.[dev]'
pytest -q
ruff check browser_fleet tests
terraform -chdir=browser_fleet/assets/infra init -backend=false
terraform -chdir=browser_fleet/assets/infra validate
terraform -chdir=browser_fleet/assets/infra test

docker build -t browser-fleet-gateway:local browser_fleet/assets/gateway
docker build -t browser-fleet-desktop:local browser_fleet/assets/desktop
RUN_NETWORK_TESTS=1 pytest tests/integration -v
```

Terraform tests use a mocked AWS provider and create no resources. Network tests
use dummy credentials and controlled SOCKS5/CONNECT endpoints. They make real DoH
requests to Cloudflare for `example.com`; they do not use your proxy file or AWS
credentials. Integration tests reserve Docker subnet `172.30.0.0/24` and must run
sequentially without another test fleet using that subnet.

Local ARM testing uses distro Chromium; AWS amd64 images install Google Chrome.
Local tests and Terraform validation do not substitute for an AWS acceptance run
with your actual provider. Validate exit IPs, DNS behavior and the sites you use on
a small fleet before opening all 200 desktops.

The seccomp profile derives from Playwright v1.55.0, with `chroot` added for Chrome's
user-namespace sandbox when the desktop has all outer capabilities dropped. See
[THIRD_PARTY.md](THIRD_PARTY.md).
