#!/bin/sh
set -eu
umask 077
# Replace the table in one transaction; never expose a firewall-free restart window.
if nft list table inet browser_fleet >/dev/null 2>&1; then
    printf 'delete table inet browser_fleet\n' > /tmp/firewall.nft
else
    : > /tmp/firewall.nft
fi
cat /etc/browser-fleet/firewall.nft >> /tmp/firewall.nft
nft -f /tmp/firewall.nft
sing-box check -c /etc/sing-box/config.json
exec sing-box run -c /etc/sing-box/config.json
