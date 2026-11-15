# ArvanCloud mirror relay

Keeps the ArvanCloud mirror (bucket `repoget`, region `ir-central1`)
fresh from GitHub, independently of GitHub Actions. It runs on two
hosts for redundancy during blackouts: the Muse VM (every 15 minutes)
and the AWS EC2 server (systemd timer, every 15 minutes).

## What it does

Two stages, both idempotent:

- **Stage A — upstream → feed-data (GitHub).** Copies the current
  base64 feed files (the `FEEDS` list from
  `scripts/build_mirror_page.py`) from the public navaroute repo into
  the private `mani-moh/feed-data` repo, and deletes `*_base64.txt`
  files there that are no longer in the list. Needs GitHub write
  access (a token or `gh`). Runs even without S3 credentials, so
  content keeps flowing via the Actions deploy while the S3 keys are
  not yet configured.
- **Stage B — feed-data → ArvanCloud S3.** Uploads the feed files
  plus the generated `index.html` to the bucket, using
  **newest-wins**: a file is uploaded only when its repo version is
  strictly newer than the mirror's copy (Git commit time of that
  file in feed-data, falling back to the upstream commit time, vs
  the S3 object's `LastModified`) and the content differs (local
  MD5 vs the S3 ETag). A mirror copy that is newer or equal is never
  overwritten — this matters during a blackout, when the mirror can
  hold fresh Blackout Kit content the repo does not have yet.
  **Mirror-only files are never deleted** (the Blackout Kit special
  feeds live only in the mirror). The single exception is a
  one-time, explicit cleanup of the known retired feed names from
  the feeds rename (PR #45) — `reachable`, `active`, `top100`,
  `lite500`, `verified`, `tier1_*`, `iran_*` — deleted only when
  the repo no longer contains them, and nothing else. Needs the S3
  key pair. Without it, runs in `--check` mode: verifies
  reachability and reports what WOULD change.

Two robustness notes: the GitHub contents API returns an empty
`content` field for files larger than ~1 MB, so those are fetched
from their `download_url` instead (this bit the first live run: the
two largest feeds landed as 0 bytes in S3 before the fix). And a
feed listed in `FEEDS` but present in neither feed-data nor
upstream (a Blackout Kit special feed not published yet) is
skipped, not an error — the page builder only links files that
exist, so the mirror page stays consistent.

## Credentials

Read from (in order) `--config`, `$ARVAN_RELAY_CONFIG`,
`~/.config/arvan-relay/env`, and
`~/workspace/your_files/arvancloud-credentials.txt`. Keys:

```
ARVAN_ACCESS_KEY=...
ARVAN_SECRET_KEY=...
# optional: GITHUB_TOKEN=... (feed-data contents read+write)
```

Values are never printed or logged. The relay never writes secrets
to the status file, stdout, or the repo.

## Run it

```bash
# dry run (no writes to feed-data or S3)
python3 tools/relay/relay.py --check

# full run, status to a file
python3 tools/relay/relay.py --status /tmp/relay-status.json

# S3 only (skip the feed-data push)
python3 tools/relay/relay.py --no-stage-a --status /tmp/relay-status.json
```

Exit code is 0 when the run completed; 1 when an endpoint was
unreachable or a stage errored.

## VM (Muse)

A cron runs the relay every 15 minutes (job `arvan-relay-15min`):
`~/workspace/arvan-relay/.venv/bin/python
~/workspace/projects/navaroute-git/tools/relay/relay.py
--status ~/workspace/arvan-relay/status.json`.
Status: `cat ~/workspace/arvan-relay/status.json`.

## AWS EC2 server

One SSH command (server must be running; needs the `.pem`):

```bash
ssh -i ~/.ssh/whitedns-key.pem ubuntu@16.58.75.182 \
  "curl -fsSL https://raw.githubusercontent.com/mani-moh/navaroute/master/tools/relay/install-aws.sh | sudo bash"
```

This installs the relay as a systemd service + timer (every 15
minutes, starts on boot). Then fill in
`/home/ubuntu/.config/arvan-relay/env` on the server with the S3 keys
and run `sudo systemctl start arvan-relay.service` once. Status:
`cat /var/log/arvan-relay/status.json`.
