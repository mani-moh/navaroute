# NavaRoute Telegram bot

Long-polling Telegram bot for owner controls, with an AWS Lambda
webhook entrypoint. Stdlib only, no dependencies. Read-only commands
work for anyone; running a workflow is owner-only and asks for
confirmation first.

Commands: `/start` (menu), `/status` (latest runs of the five
workflows), `/feeds` (links page and feed links), `/help`.

## 1. Create the bot

1. In Telegram, message `@BotFather` and send `/newbot`. Pick a name
   and a username.
2. BotFather replies with a token. **Keep it secret — never paste it
   in chat or commit it.** It goes into the env file below only.
3. Message your new bot once (`/start`) so it exists, then find your
   own numeric Telegram user id: message `@userinfobot` and copy the
   `Id` it returns. That is the owner id.

## 2. Environment

Create `~/.config/navaroute-telegram-bot/env` (mode `600`):

```sh
mkdir -p ~/.config/navaroute-telegram-bot
cat > ~/.config/navaroute-telegram-bot/env <<'EOF'
TELEGRAM_BOT_TOKEN=123456:ABC-def...
TELEGRAM_OWNER_ID=123456789
EOF
chmod 600 ~/.config/navaroute-telegram-bot/env
```

| Variable | Required | Default |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | yes | — |
| `TELEGRAM_OWNER_ID` | no (triggers refuse everyone until set) | — |
| `GITHUB_REPO` | no | `mani-moh/navaroute` |
| `FEED_DATA_REPO` | no | `mani-moh/feed-data` |
| `MIRROR_URL` | no | `https://repoget.s3.ir-central1.arvanstorage.ir/index.html` |
| `SITE_URL` | no | `https://mani-moh.github.io/navaroute/` |
| `GITHUB_TOKEN` | no | falls back to `gh api` (needs `gh auth login` with `workflow` scope) |

## 3. Run locally

```sh
set -a; . ~/.config/navaroute-telegram-bot/env; set +a
python3 tools/telegram_bot/bot.py
```

Logs go to stdout. Stop with Ctrl-C.

## 4. Run as a service (systemd, user unit)

```sh
mkdir -p ~/.config/systemd/user
cp tools/telegram_bot/navaroute-telegram-bot.service ~/.config/systemd/user/
# Edit ExecStart/WorkingDirectory in the copied file to point at your checkout.
systemctl --user daemon-reload
systemctl --user enable --now navaroute-telegram-bot
journalctl --user -u navaroute-telegram-bot -f   # logs
```

## 5. AWS Lambda (webhook) — the serverless home

The same bot code runs on Lambda via `lambda_handler.py`, which
handles one Telegram webhook update per invocation. The VM polling
service and the Lambda webhook must never run at the same time:
Telegram returns 409 while two pollers are active, and updates would
be split between them.

**Stop the VM service first:**

```sh
systemctl --user stop navaroute-telegram-bot
systemctl --user disable navaroute-telegram-bot
```

**Package and create the function:**

```sh
tools/telegram_bot/package_lambda.sh   # builds tools/telegram_bot/dist/navaroute-bot.zip
```

Create the function with runtime **Python 3.12**, handler
`lambda_handler.handler`, timeout **30s**, memory **128 MB**, and
upload the zip. Environment variables:

| Variable | Required | Notes |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | yes | same token as the VM service |
| `TELEGRAM_OWNER_ID` | yes | same owner id as the VM service; triggers refuse everyone else |
| `GITHUB_TOKEN` | yes | **required on Lambda** — there is no `gh` CLI there, so the subprocess fallback cannot work. Use a token with repo Actions read+write (a fine-grained PAT for `mani-moh/navaroute` and `mani-moh/feed-data`, or the existing `gh` token) |
| `GITHUB_REPO` / `FEED_DATA_REPO` | no | defaults `mani-moh/navaroute` / `mani-moh/feed-data` |

**Expose it and point Telegram at it:**

1. Add a **Function URL** with auth type **NONE**. It is HTTPS and
   free — no API Gateway is needed and there are no API Gateway
   charges.
2. Set the webhook (replace `<TOKEN>` and `<FUNCTION_URL>`):

```sh
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<FUNCTION_URL>"
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

`getWebhookInfo` should show the function URL and no
`last_error_message`. Send `/start` to the bot to confirm it replies.

**Going back:** to return to VM polling, delete the webhook first
(`curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"`), then
re-enable the systemd service from section 4.
