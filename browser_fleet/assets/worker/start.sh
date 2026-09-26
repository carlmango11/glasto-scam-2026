#!/bin/bash
set -euo pipefail
cd /opt/browser-fleet
compose=(docker compose -f compose.json)
# Recreate namespaces and dependent desktops together after a host reboot.
"${compose[@]}" down --timeout 30
"${compose[@]}" up -d --wait --wait-timeout 180
while sleep 20; do
    for service in $("${compose[@]}" config --services | awk '/^net-/'); do
        container=$("${compose[@]}" ps -q "$service")
        health=$(docker inspect --format '{{.State.Health.Status}}' "$container" 2>/dev/null || true)
        desktop="desktop-${service#net-}"
        desktop_container=$("${compose[@]}" ps -q "$desktop")
        desktop_health=$(docker inspect --format '{{.State.Health.Status}}' "$desktop_container" 2>/dev/null || true)
        if [ "$health" != healthy ] || [ "$desktop_health" != healthy ]; then
            "${compose[@]}" stop "$desktop"
            "${compose[@]}" up -d --force-recreate --wait --wait-timeout 180 "$service" "$desktop"
        fi
    done
done
