#!/bin/sh
# Package the NavaRoute Telegram bot for AWS Lambda.
#
# Run from anywhere (paths resolve from this script's location):
#   tools/telegram_bot/package_lambda.sh
#
# Builds tools/telegram_bot/dist/navaroute-bot.zip containing bot.py
# and lambda_handler.py at the zip top level (the deployment layout
# the handler import expects). Prints the zip size and sha256.
# The dist/ directory is gitignored; the zip is never committed.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST="$SCRIPT_DIR/dist"
ZIP="$DIST/navaroute-bot.zip"

mkdir -p "$DIST"
rm -f "$ZIP"

python3 - "$SCRIPT_DIR" "$ZIP" <<'EOF'
import hashlib
import sys
import zipfile
from pathlib import Path

src_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    for name in ("bot.py", "lambda_handler.py"):
        zf.write(src_dir / name, arcname=name)

data = out.read_bytes()
print(f"wrote {out}")
print(f"size: {len(data)} bytes")
print(f"sha256: {hashlib.sha256(data).hexdigest()}")
EOF
