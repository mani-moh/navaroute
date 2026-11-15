#!/usr/bin/env bash
# Install the ArvanCloud mirror relay on an Ubuntu EC2 server.
#
# Run once, as a single SSH command (the server must be running):
#   ssh -i ~/.ssh/whitedns-key.pem ubuntu@16.58.75.182 \
#     "curl -fsSL https://raw.githubusercontent.com/mani-moh/navaroute/master/tools/relay/install-aws.sh | sudo bash"
#
# What it does (idempotent -- safe to re-run):
#   * installs python3 + venv, creates /opt/arvan-relay with a boto3 venv
#   * downloads relay.py and build_mirror_page.py from the repo (master)
#   * creates /home/ubuntu/.config/arvan-relay/env (mode 600) -- fill it
#     with ARVAN_ACCESS_KEY and ARVAN_SECRET_KEY (and optional
#     GITHUB_TOKEN) after install; an existing file is never overwritten
#   * installs a systemd service + timer that runs the relay every
#     15 minutes, enabled so it starts on boot
#
# The relay only makes outbound HTTPS calls (GitHub + ArvanCloud S3)
# and writes a status file; it touches nothing else on the server.
# It never prints credentials. Requires passwordless sudo (standard
# on EC2 Ubuntu).
set -euo pipefail

RELAY_DIR="/opt/arvan-relay"
CONFIG_DIR="/home/ubuntu/.config/arvan-relay"
CONFIG_FILE="$CONFIG_DIR/env"
STATUS_DIR="/var/log/arvan-relay"
RAW_BASE="https://raw.githubusercontent.com/mani-moh/navaroute/master"

echo "== ArvanCloud relay installer =="

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv curl ca-certificates

mkdir -p "$RELAY_DIR" "$CONFIG_DIR" "$STATUS_DIR"
chown ubuntu:ubuntu "$CONFIG_DIR" "$STATUS_DIR"

if [ ! -x "$RELAY_DIR/.venv/bin/python" ]; then
    python3 -m venv "$RELAY_DIR/.venv"
fi
"$RELAY_DIR/.venv/bin/pip" install -q --upgrade pip
"$RELAY_DIR/.venv/bin/pip" install -q boto3

curl -fsSL "$RAW_BASE/tools/relay/relay.py" -o "$RELAY_DIR/relay.py"
curl -fsSL "$RAW_BASE/scripts/build_mirror_page.py" -o "$RELAY_DIR/build_mirror_page.py"
chmod 644 "$RELAY_DIR/relay.py" "$RELAY_DIR/build_mirror_page.py"

if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" <<'EOF'
# ArvanCloud mirror relay credentials. Fill in the two S3 keys
# (same values as ~/workspace/your_files/arvancloud-credentials.txt).
# Optional: GITHUB_TOKEN (a fine-grained PAT for mani-moh/feed-data,
# contents read+write) lets the relay push feed updates to feed-data;
# without it the relay only reads the public upstream repo.
ARVAN_ACCESS_KEY=
ARVAN_SECRET_KEY=
# GITHUB_TOKEN=
EOF
    chmod 600 "$CONFIG_FILE"
    chown ubuntu:ubuntu "$CONFIG_FILE"
    echo "Created $CONFIG_FILE (empty) -- fill in the S3 keys."
else
    echo "Keeping existing $CONFIG_FILE (not overwritten)."
fi

cat > /etc/systemd/system/arvan-relay.service <<'EOF'
[Unit]
Description=ArvanCloud mirror relay (feed-data -> S3)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ubuntu
ExecStart=/opt/arvan-relay/.venv/bin/python /opt/arvan-relay/relay.py --config /home/ubuntu/.config/arvan-relay/env --status /var/log/arvan-relay/status.json
EOF

cat > /etc/systemd/system/arvan-relay.timer <<'EOF'
[Unit]
Description=Run the ArvanCloud mirror relay every 15 minutes

[Timer]
OnCalendar=*:0/15
Persistent=true
Unit=arvan-relay.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now arvan-relay.timer
systemctl start arvan-relay.service || true

echo ""
echo "Installed. The relay runs every 15 minutes (systemd timer)."
echo "  status:  systemctl status arvan-relay.timer"
echo "  last run: cat /var/log/arvan-relay/status.json"
echo "  logs:    journalctl -u arvan-relay.service --no-pager -n 30"
echo "Next: fill in $CONFIG_FILE with the S3 keys, then:"
echo "  sudo systemctl start arvan-relay.service"
