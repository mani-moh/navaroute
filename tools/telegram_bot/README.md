# NavaRoute Telegram bot

Long-polling Telegram bot for owner controls. Stdlib only, no
dependencies. Read-only commands work for anyone; running a workflow
is owner-only and asks for confirmation first.

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

## 5. Lambda migration note

No hosting is baked in: this file is the whole bot. To move it to
AWS Lambda later, keep `Bot`, `Config`, `TelegramApi` and
`GitHubClient` as-is and add a small webhook entrypoint that builds
a `Bot` from env, feeds one Telegram update JSON into
`bot.handle_update(update)`, and returns. Switch the Telegram webhook
with `setWebhook` to the Lambda function URL. Long polling and the
webhook entrypoint must never run at the same time — Telegram returns
409 while two pollers are active.
