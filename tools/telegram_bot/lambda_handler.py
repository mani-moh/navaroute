"""AWS Lambda webhook entrypoint for the NavaRoute Telegram bot.

Reuses Bot, Config, TelegramApi and GitHubClient from bot.py (stdlib
only, same as the polling service).

Deployment layout: bot.py and this file are zipped together at the top
level (see package_lambda.sh), so the Lambda handler is
``lambda_handler.handler`` and the import below resolves as top-level
``bot``. A fallback import covers running inside the repo tree.

Notes for the Lambda environment:
- GitHub access MUST use the GITHUB_TOKEN env var (the urllib path).
  There is no ``gh`` CLI on Lambda, so the subprocess fallback in
  GitHubClient cannot work there.
- The handler always returns HTTP 200, even for a malformed body or
  an internal error. Telegram retries 5xx responses, and a retried
  trigger could dispatch the same workflow twice. Errors are logged
  to stdout instead. (The bot's own _run_trigger already catches
  dispatch errors and reports them by editing the message, so a 200
  is safe for those too.)
- The Bot is built with ``start_time=0``. The polling loop drops
  messages older than process start to avoid replays after a restart,
  but a Lambda invocation IS the delivery of the update: the message
  predates the invocation by seconds, so the freshness filter would
  drop every webhook message. Webhook delivery is at-most-once per
  200 response, so no replay filter is needed here.
"""

from __future__ import annotations

import base64
import json

try:
    from bot import Bot, Config
except ImportError:  # running inside the repo tree, not the Lambda zip
    from tools.telegram_bot.bot import Bot, Config


def _parse_body(event: dict) -> dict:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    update = json.loads(body)
    if not isinstance(update, dict):
        raise ValueError("update body is not a JSON object")
    return update


def handler(event, context):
    """Handle one Telegram webhook update; always returns HTTP 200."""
    try:
        update = _parse_body(event or {})
    except Exception as exc:
        print(f"lambda_handler: bad update body: {exc!r}", flush=True)
        return {"statusCode": 200, "body": "ok"}
    try:
        config = Config.from_env()
        bot = Bot(config, start_time=0)
        bot.handle_update(update)
    except SystemExit as exc:
        # Config.from_env exits when TELEGRAM_BOT_TOKEN is missing.
        print(f"lambda_handler: config error: {exc}", flush=True)
    except Exception as exc:
        print(f"lambda_handler: update failed: {exc!r}", flush=True)
    return {"statusCode": 200, "body": "ok"}
