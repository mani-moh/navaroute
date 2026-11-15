# Blackout Kit

During an internet blackout in Iran, Ryan still needs fresh ways to
connect. The Blackout Kit publishes **special feeds** on the
Iranian-hosted ArvanCloud mirror (bucket `repoget`, region
`ir-central1`) that carry new connection methods, resolver lists,
installer files and how-to notes — encrypted, so the content is not
detectable in transit or storage.

## How it fits together

```
Telegram mining (public channels/groups + Saved Messages)
        + web search (new methods)
        → dedupe (content hash, never publish twice)
        → encrypt (AES-256-GCM, BK1 envelope)
        → feed-data repo (kit-1..4_base64.txt)
        → relay / GitHub Actions sync → ArvanCloud mirror
        → mirror page (neutral labels) + Telegram bot /key (owner-only)
```

The publish path writes files to the private `mani-moh/feed-data`
repo; the existing relay/sync pushes them to ArvanCloud. The kit
never talks to S3 itself and never deletes mirror-only files.

## Special feeds

| File | Mirror label | Contents (encrypted) |
|---|---|---|
| `kit-1_base64.txt` | extra · 1 | resolver lists (IP:port, one per line) |
| `kit-2_base64.txt` | extra · 2 | connection entries (one per line) |
| `kit-3_base64.txt` | extra · 3 | app/installer download links (one per line) |
| `kit-4_base64.txt` | extra · 4 | method notes (short plain-language how-tos) |

Labels on the mirror page are deliberately neutral — the page
enforces a forbidden-word list by test (`tests/test_mirror_page.py`).
The feed files are base64 envelopes, never plain text.

**Newest-wins.** A feed file is only rewritten when the new build is
newer than the one already published (each file carries its build
time in its plaintext header) and its findings differ. An older build
never overwrites a newer file, and this kit never deletes any file
from the mirror.

## Encryption

- **Algorithm:** AES-256-GCM, implemented in pure stdlib Python
  (`tools/blackout_kit/aesgcm.py`) so the kit works on a bare Python
  3.12 install with no third-party packages. Validated against the
  `cryptography` package (200 random cases + NIST vectors) and
  NIST GCM test case 3 in the test suite.
- **Envelope:** `BK1 || nonce (12 random bytes) || ciphertext || tag (16)`,
  base64-wrapped at 76 columns. The feed filename is bound as
  additional authenticated data, so a blob cannot be replayed into a
  different feed file.
- **Key:** one 32-byte key, generated once on the VM:
  `python3 -m tools.blackout_kit.cli genkey`
  It is stored at `~/.config/blackout-kit/key` (mode 600). It is
  **never** printed, never logged, never committed to any repo, and
  never sent in chat except through the owner-only bot command below.

### Decrypting a downloaded feed

The standalone helper needs nothing but Python 3:

```bash
python3 decrypt_helper.py --key-file key.txt kit-2_base64.txt > entries.txt
```

`tools/blackout_kit/decrypt_helper.py` is generated from the kit's
AES core (`python3 -m tools.blackout_kit.make_decrypt_helper`) so it
can never drift from the kit's crypto; the test suite asserts the
checked-in copy matches a fresh build.

## Key distribution (Telegram bot)

`/key` in @awsmanibot sends the key — **owner-only** (Telegram user
ID `204376459`). Anyone else gets "This action is owner-only", and
the key value never appears in the bot's logs. The command is
deliberately not listed in `/help`, so casual chatters never see it.
The bot reads the key from the `BLACKOUT_KEY` environment variable
(base64, same form as the key file).

Caveat: a Telegram bot chat is not end-to-end encrypted. The key is
sent only to Ryan, in a private chat with the bot, and only on his
explicit `/key` request. If that trade-off ever stops being
acceptable, rotate the key (generate a new one, re-encrypt and
republish the feeds) instead of using `/key`.

## Telegram mining — scope rule

The miner (`tools/blackout_kit/telegram_mine.py`) reads **only**:

- public channels/groups, by @username or t.me link, and
- Ryan's own Saved Messages (`me`).

It **never** reads private 1:1 chats. This is enforced in code:
sources are validated by `assert_allowed_source` (a phone number, a
numeric user id, or anything that is not a public channel/group is
refused), and the module contains no dialog-listing call at all
(guarded by a test that fails if one is added). Only concrete,
actionable items are extracted from messages — links, entries,
APK links, IP:port — plain prose is never republished.

## 24/7 searching during blackouts

The search loop is **gated on the blackout monitor's verdict**:

- The monitor (`check_blackout.py --json`, the same script the hourly
  auto-start cron uses) prints `BLACKOUT` / `DEGRADED` / `OK` /
  `UNKNOWN`. Only `BLACKOUT` or `DEGRADED` escalates.
- `OK` and `UNKNOWN` never trigger a search; an unparseable monitor
  result is treated as `UNKNOWN`.
- Each round runs web search (DuckDuckGo HTML, stdlib only,
  best-effort — a provider outage never crashes the loop) plus
  Telegram mining, dedupes by content hash against the published
  registry (`~/.config/blackout-kit/published.json`), and publishes
  only genuinely new findings.
- **Backoff:** after each round that finds nothing new, the wait
  before the next round doubles (15 min → 2 h cap), so a long
  blackout does not burn requests on empty searches.

Run one round (and optionally publish):

```bash
python3 -m tools.blackout_kit.cli search --publish
python3 -m tools.blackout_kit.cli check   # just the monitor verdict
```

To wire it into the monitor's schedule, run the `search --publish`
command from the same hourly slot as the blackout check (e.g. right
after `auto_start.py` in the hourly cron). The loop itself decides
whether the verdict warrants searching.

## What Ryan must do

Two steps need Ryan; neither can be done from this VM.

### 1. Telegram login (phone number + code)

The miner logs in as Ryan's personal Telegram account (a user
client, not a bot). On the VM:

```bash
pip install telethon
python3 -m tools.blackout_kit.login --api-id <id> --api-hash <hash>
```

- The API id/hash come from https://my.telegram.org/apps (create an
  app once; they identify the app, not Ryan).
- The script asks for his **phone number**, then for the **login code**
  Telegram sends him (and his two-step password, if he has one).
  Both are typed at the terminal prompt only — never in chat, never
  stored, never logged.
- After login, the session file is encrypted with the Blackout Kit
  key and stored at `~/.config/blackout-kit/telegram.session.enc`
  (mode 600); the plaintext session is deleted. The miner decrypts
  it to a temporary file only while it runs.
- Until this is done, the search loop still works on web search alone
  (the miner simply contributes nothing).

To choose which public channels/groups to mine, edit
`~/.config/blackout-kit/config.json` (created with
`python3 -c "from tools.blackout_kit.config import KitConfig; KitConfig().save()"`):
set `"sources"` to a list like `["me", "@channelname"]`. Only public
channels/groups and `"me"` are accepted.

### 2. Lambda env var + redeploy (bot `/key`)

The bot code is in the repo, but the Lambda cannot be redeployed from
this VM (the available IAM credentials are EC2 Start/Stop only).
From a machine with Lambda permissions, with the key value copied
from `~/.config/blackout-kit/key` (the base64 text in that file):

```bash
# 1. Set the key as an env var on the Lambda function
aws lambda update-function-configuration \
  --function-name navaroute-telegram-bot \
  --region us-east-2 \
  --environment "Variables={TELEGRAM_BOT_TOKEN=<existing token>,TELEGRAM_OWNER_ID=204376459,BLACKOUT_KEY=<base64 key from the key file>}"
```

> Keep every existing env var on the function in that call — the
> update replaces the whole environment. List them first with
> `aws lambda get-function-configuration --function-name
> navaroute-telegram-bot --region us-east-2`.

```bash
# 2. Redeploy the function code with the updated bot.py
# (from the navaroute repo root, after pulling the merged PR):
cd tools/telegram_bot
zip -r /tmp/bot.zip bot.py
aws lambda update-function-code \
  --function-name navaroute-telegram-bot \
  --region us-east-2 \
  --zip-file fileb:///tmp/bot.zip
```

Then test with Ryan's account: send `/key` to @awsmanibot — the bot
replies with the key. Send `/key` from any other account to confirm
it refuses.

## Key rotation

If the key is ever exposed: generate a new one (`genkey` refuses to
overwrite — delete `~/.config/blackout-kit/key` deliberately first),
re-encrypt and republish all four feeds, update `BLACKOUT_KEY` on
the Lambda, and re-run the Telegram login (the session file is
encrypted with the old key). Old feed files become undecryptable by
design.
