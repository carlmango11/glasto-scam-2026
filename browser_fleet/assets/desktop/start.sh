#!/bin/bash
set -euo pipefail
export DISPLAY=:1
export XDG_RUNTIME_DIR=/tmp/runtime-browser
mkdir -p "$XDG_RUNTIME_DIR" "$HOME/.config/autostart"
chmod 700 "$XDG_RUNTIME_DIR"
# Only the previous container's X server and browser used this private home.
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1 "$HOME/.config/fleet-chrome/SingletonLock" \
    "$HOME/.config/fleet-chrome/SingletonSocket" "$HOME/.config/fleet-chrome/SingletonCookie"
cat > "$HOME/.config/autostart/browser.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Chrome
Exec=/usr/local/bin/launch-browser.sh
EOF
Xtigervnc :1 -geometry "${SCREEN_WIDTH:-1440}x${SCREEN_HEIGHT:-900}" -depth 24 \
    -SecurityTypes None -localhost yes -rfbport 5900 -AlwaysShared -ac -nolisten tcp &
vnc_pid=$!
trap 'kill $(jobs -pr) 2>/dev/null || true; wait || true' EXIT
for attempt in $(seq 1 50); do
    if xdpyinfo >/dev/null 2>&1; then break; fi
    kill -0 "$vnc_pid"
    sleep 0.1
done
xdpyinfo >/dev/null
dbus-run-session -- xfce4-session &
websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 &
wait -n
