#!/bin/sh
set -eu
if command -v google-chrome-stable >/dev/null 2>&1; then
    browser=google-chrome-stable
else
    browser=chromium
fi
exec "$browser" --user-data-dir="$HOME/.config/fleet-chrome" --no-first-run \
    --no-default-browser-check --password-store=basic --start-maximized about:blank
