# Browser Fleet implementation plan

**Goal:** Configurable AWS hosts with persistent browser desktops and enforced per-browser proxy egress.
**Architecture:** Python CLI + Terraform + Docker Compose + sing-box/nftables + noVNC.
**Spec:** [design.md](design.md)

## Tasks

1. Write failing tests for proxy parsing, configuration validation, assignments,
   and redacted outputs. Implement the Python configuration and bundle renderer.
2. Build gateway and desktop images, worker bootstrap and viewer routing. Add
   controlled-network integration tests and run against local Docker.
3. Add Terraform infrastructure and CLI deployment/lifecycle commands. Test
   generated artifacts and command behavior without creating AWS resources.
4. Add a searchable dashboard with persistent desktop processes, keyboard
   navigation and clear loading/error states. Verify browser behavior.
5. Run the complete tests, Terraform validation, image/config checks and a fresh
   code review. Fix findings and document setup, costs, limits and verification.

## Review focus

- Passwords containing colons, shell metacharacters and literal backslashes.
- Insufficient/duplicate proxy rows and count changes that might reassign profiles.
- DNS through Docker's resolver and established connections after tunnel failure.
- Host reboot, gateway restart and desktop namespace replacement ordering.
- Accidental secret disclosure, public desktop exposure and AWS credential drift.

## Progress

- Workspace: new empty repository; implementing in place with no existing work to isolate.
- Authorization: implement requested architecture directly; no AWS deployment until credentials are selected.
- Decision: use a small noVNC switcher instead of Guacamole to avoid a database/authentication server for local SSH access.
- Completed: configuration/parser, isolated network and desktop images, worker supervisor,
  Terraform infrastructure, lifecycle CLI, dashboard, packaging and documentation.
- Reviews: network and deployment reviews completed. Fixed daemon restart ordering,
  viewer origin/token protection, host replacement behavior, ARM/memory validation,
  obsolete credential bundles, atomic state writes and cleanup after successful destroy.
- Verification: 48 unit tests passed; four opt-in Docker integration tests passed in
  the full integration run (desktop persistence/sandbox/viewer access, SOCKS5, HTTP
  CONNECT, HTTPS CONNECT). These cover proxied DNS, direct/metadata/Docker-DNS blocks,
  IPv6 attempts, failed TUN isolation and HTTPS certificate hostname rejection.
- Terraform format/validation and one mocked-provider test passed. Native desktop and
  gateway images built; the production amd64 Google Chrome image also built.
- Live UI: rendered and operated the remote Chromium desktop in the local dashboard;
  checked search empty state and browser renderer namespace isolation.
- Packaging: wheel includes runtime assets and provider lockfile, excludes provider cache;
  editable install and CLI entry point verified.
- Scale: generated and checked 200 distinct assignments in 10 bundles in 1.07 seconds,
  41.6 MiB process peak RSS on the development machine. This measures configuration
  generation only; it is not a browser capacity benchmark.
- Remaining external acceptance: no AWS resources deployed, no real provider credentials
  used, and no 200-browser runtime load test performed.
