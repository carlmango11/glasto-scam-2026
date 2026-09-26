# Browser Fleet design

Build 200 human-operated Chrome desktops distributed across a configurable number
of AWS EC2 instances. Each browser has a persistent home and one proxy supplied as
host:port:username:password. Fleet size is instances × browsers_per_instance.

## Architecture

Python CLI renders Docker Compose bundles and provisions AWS using Terraform.
Credentials use the standard AWS chain (profiles/SSO/environment), never configuration
fields. Terraform creates a VPC, SSH-only security group, EC2 hosts, encrypted disks,
and a private encrypted artifact bucket. Each host can read only its own bundle.
Secrets go in mode-0600 bundle files uploaded by the CLI, never Terraform state,
user-data, command-line arguments, the dashboard, or logs.

Each non-root desktop shares a network namespace with one sing-box gateway. The
gateway sets an nftables default-deny policy before opening its TUN interface.
Only gateway-root TCP connections to the selected upstream IP/port may leave eth0.
Browser traffic may leave only through the TUN. DNS is intercepted and forwarded
as DoH through the same proxy. Other UDP is rejected consistently for both proxy
types; this deliberately does not promise full WebRTC support. IPv6 has no direct
escape. Docker's embedded resolver is blocked before DNAT. No general established
egress rule may let established browser traffic escape when the TUN disappears.
Only established desktop responses to the worker's private viewer are exempt.

SOCKS5 is the default. HTTPS means TLS to the CONNECT proxy with certificate
verification; plain HTTP CONNECT is a separately named option for providers that
use the term HTTPS to mean tunnelling HTTPS websites over plaintext CONNECT.
The CLI resolves upstream hostnames before bundle creation. Browser destination
DNS never uses the host resolver. The selected proxy IP is pinned until redeploy.

Each worker has an nginx viewer gateway bound to host loopback. The CLI opens
SSH tunnels and serves a local dashboard. noVNC handles desktop input/display.
Sessions remain alive when viewers disconnect; stop/start preserves disk data,
not running processes. Destruction removes the fleet and its profiles.

## Constraints and verification

Linux desktop containers share a host kernel, not individual guest OS kernels.
Chrome retains its sandbox. Desktops have no capabilities or privilege escalation.
Only gateways get NET_ADMIN, NET_RAW (for binding proxy sockets to eth0), and
/dev/net/tun. Workers are trusted infrastructure;
host compromise is outside browser-level isolation. SSH access uses a user-supplied
CIDR and per-fleet key. Local forwarding binds 127.0.0.1 only.

Test parsing, secret handling, assignments, generated configuration, CLI commands,
and Terraform validity locally. Run Linux-container integration tests against a
controlled SOCKS/CONNECT proxy and destination: permitted traffic, DNS, direct
TCP/UDP, Docker resolver, cross-session access, and gateway failure. AWS end-to-end
deployment requires the user's selected credentials and is reported separately.
