#!/bin/bash
set -euo pipefail
cd /opt/browser-fleet
docker compose -f compose.json build
cat > /etc/systemd/system/browser-fleet.service <<'EOF'
[Unit]
Description=Browser Fleet desktops and network gateways
After=docker.service network-online.target
Requires=docker.service
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=/opt/browser-fleet
ExecStart=/bin/bash /opt/browser-fleet/worker/start.sh
ExecStop=/usr/bin/docker compose -f /opt/browser-fleet/compose.json down --timeout 30
Restart=on-failure
RestartSec=30
TimeoutStartSec=0
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now browser-fleet.service
