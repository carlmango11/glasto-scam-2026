# Third-party components

`browser_fleet/assets/desktop/chrome-seccomp.json` is derived from
[Microsoft Playwright v1.55.0](https://github.com/microsoft/playwright/blob/v1.55.0/utils/docker/seccomp_profile.json),
licensed under Apache License 2.0. The local modification allows the `chroot`
system call inside Chrome's unprivileged user-namespace sandbox. The license is
included in `browser_fleet/assets/desktop/PLAYWRIGHT-LICENSE`.

Runtime images install or include sing-box, noVNC, TigerVNC, Xfce, nginx and
Google Chrome (or distro Chromium for local ARM tests). Their respective
upstream licenses and distribution terms apply.
