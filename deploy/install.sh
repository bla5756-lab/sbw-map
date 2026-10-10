#!/usr/bin/env bash
# Install (or refresh) the user-level systemd unit and start the server on :8100.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.config/systemd/user"
cp "$HERE/southbay-water.service" "$HOME/.config/systemd/user/southbay-water.service"
systemctl --user daemon-reload
systemctl --user enable --now southbay-water.service
systemctl --user --no-pager --lines=5 status southbay-water.service || true
echo "Reachable at: http://$(hostname -I | awk '{print $1}'):8100  and on the tailnet at http://$(tailscale ip -4 2>/dev/null || echo '<tailnet-ip>'):8100"
