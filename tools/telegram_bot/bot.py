"""Long-polling Telegram bot for NavaRoute owner controls.

Stdlib only. Read-only commands (/status, /feeds) work for anyone;
triggers (run discovery / run update) are owner-only and need a
confirmation tap first.

Configuration (environment):
  TELEGRAM_BOT_TOKEN  required, from @BotFather
  TELEGRAM_OWNER_ID   Telegram numeric user id of the owner; triggers
                      refuse everyone else (and refuse everyone when unset)
  GITHUB_REPO         default "mani-moh/navaroute"
  FEED_DATA_REPO      default "mani-moh/feed-data"
  MIRROR_URL          default "https://repoget.s3.ir-central1.arvanstorage.ir/index.html"
  SITE_URL            default "https://mani-moh.github.io/navaroute/"
  GITHUB_TOKEN        optional; without it the bot shells out to `gh api`

Run:  TELEGRAM_BOT_TOKEN=... python3 tools/telegram_bot/bot.py
Logs go to stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

TELEGRAM_API = "https://api.telegram.org"
GITHUB_API = "https://api.github.com"

DISCOVERY_WORKFLOW = "daily-deep-discovery.yml"
UPDATE_WORKFLOW = "update-subs.yml"
TIER1_WORKFLOW = "tier1-pattn-test.yml"
SITE_WORKFLOW = "public-site.yml"
MIRROR_WORKFLOW = "deploy.yml"

DISCOVERY_INPUTS = {
    "max_api_requests": "500",
    "max_time_seconds": "5400",
    "max_channels": "2000",
}
UPDATE_INPUTS = {
    "run_healthcheck": "true",
    "max_test_nodes": "1000",
}

# (label, repo key, workflow file) shown by /status.
STATUS_WORKFLOWS = (
    ("Discovery", "repo", DISCOVERY_WORKFLOW),
    ("Update", "repo", UPDATE_WORKFLOW),
    ("Tier-1 test", "repo", TIER1_WORKFLOW),
    ("Public site", "repo", SITE_WORKFLOW),
    ("Mirror deploy", "feed_data", MIRROR_WORKFLOW),
)

# (label, file) shown by /feeds. Labels are neutral on purpose: the bot
# never describes what the links are for.
FEED_FILES = (
    ("working", "tier1_all_base64.txt"),
    ("online", "reachable_base64.txt"),
    ("top 100", "top100_base64.txt"),
    ("lite", "lite500_base64.txt"),
    ("working · top 100", "tier1_top100_base64.txt"),
    ("working · lite", "tier1_lite500_base64.txt"),
    ("working · online", "tier1_reachable_base64.txt"),
)

MENU_TEXT = (
    "Choose an action below.\n"
    "Status and Links work for anyone; running a workflow is owner-only."
)
HELP_TEXT = (
    "Commands:\n"
    "/start - menu\n"
    "/status - latest workflow runs\n"
    "/feeds - links page and feed links\n"
    "/help - this help\n\n"
    "Running discovery or an update is owner-only and asks for "
    "confirmation first."
)
UNKNOWN_TEXT = "Unknown command. Try /help."


@dataclass
class Config:
    bot_token: str
    owner_id: str | None
    github_repo: str = "mani-moh/navaroute"
    feed_data_repo: str = "mani-moh/feed-data"
    mirror_url: str = "https://repoget.s3.ir-central1.arvanstorage.ir/index.html"
    site_url: str = "https://mani-moh.github.io/navaroute/"

    @property
    def mirror_base(self) -> str:
        url = self.mirror_url.rstrip("/")
        if url.endswith("/index.html"):
            url = url[: -len("/index.html")]
        return url

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Config":
        env = os.environ if env is None else env
        token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            print(
                "TELEGRAM_BOT_TOKEN is not set; create a bot with @BotFather "
                "and export its token.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        owner = env.get("TELEGRAM_OWNER_ID", "").strip() or None
        return cls(
            bot_token=token,
            owner_id=owner,
            github_repo=env.get("GITHUB_REPO", "") or "mani-moh/navaroute",
            feed_data_repo=env.get("FEED_DATA_REPO", "") or "mani-moh/feed-data",
            mirror_url=env.get("MIRROR_URL", "")
            or "https://repoget.s3.ir-central1.arvanstorage.ir/index.html",
            site_url=env.get("SITE_URL", "") or "https://mani-moh.github.io/navaroute/",
        )


def _menu_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Status", "callback_data": "status"},
                {"text": "Links", "callback_data": "feeds"},
            ],
            [
                {"text": "Run discovery", "callback_data": "discover"},
                {"text": "Run update", "callback_data": "aggregate"},
            ],
        ]
    }


def _confirm_keyboard(kind: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Yes, run it", "callback_data": f"confirm_{kind}"},
                {"text": "Cancel", "callback_data": "cancel"},
            ]
        ]
    }


def _format_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value or "unknown time"


class TelegramApi:
    """Thin urllib wrapper over the Telegram Bot API."""

    def __init__(self, token: str, timeout: int = 60) -> None:
        self._base = f"{TELEGRAM_API}/bot{token}"
        self._timeout = timeout

    def _call(self, method: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base}/{method}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API {method} failed: {body}")
        return body["result"]

    def get_updates(self, offset: int | None, timeout: int = 30) -> list:
        return self._call("getUpdates", {"offset": offset, "timeout": timeout})

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        payload: dict = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call("sendMessage", payload)

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> dict:
        payload: dict = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        return self._call("answerCallbackQuery", payload)

    def edit_message_text(
        self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None
    ) -> dict:
        payload: dict = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call("editMessageText", payload)


class GitHubClient:
    """Latest-run reads and workflow dispatches.

    Uses GITHUB_TOKEN over HTTPS when set; otherwise shells out to `gh`.
    """

    def __init__(self, token: str | None = None) -> None:
        self._token = token or os.environ.get("GITHUB_TOKEN", "") or None

    def latest_run(self, repo: str, workflow: str) -> dict | None:
        path = f"repos/{repo}/actions/workflows/{workflow}/runs?per_page=1"
        data = self._request("GET", path)
        runs = data.get("workflow_runs") or []
        return runs[0] if runs else None

    def dispatch(self, repo: str, workflow: str, inputs: dict) -> bool:
        path = f"repos/{repo}/actions/workflows/{workflow}/dispatches"
        self._request("POST", path, {"ref": "master", "inputs": inputs})
        return True

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if self._token:
            return self._request_http(method, path, payload)
        return self._request_gh(method, path, payload)

    def _request_http(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{GITHUB_API}/{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GitHub API {method} {path} failed: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GitHub API {method} {path} failed: {exc.reason}") from exc

    def _request_gh(self, method: str, path: str, payload: dict | None = None) -> dict:
        cmd = ["gh", "api", path, "--method", method]
        if payload is not None:
            cmd += ["--input", "-"]
        proc = subprocess.run(
            cmd,
            input=json.dumps(payload).encode("utf-8") if payload is not None else None,
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"gh api {method} {path} failed: {detail}")
        raw = proc.stdout.decode("utf-8").strip()
        return json.loads(raw) if raw else {}


class Bot:
    """Update handling: routing, owner gate, confirm flow, status/feeds."""

    def __init__(
        self,
        config: Config,
        api: TelegramApi | None = None,
        github: GitHubClient | None = None,
        logger=None,
        start_time: float | None = None,
    ) -> None:
        self.config = config
        self.api = api if api is not None else TelegramApi(config.bot_token)
        self.github = github if github is not None else GitHubClient()
        self._log = logger if logger is not None else self._default_logger
        # Messages sent before this process started are ignored, so a
        # restart never replays old commands.
        self.start_time = int(start_time if start_time is not None else time.time())
        self._offset: int | None = None

    @staticmethod
    def _default_logger(message: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"{stamp} {message}", flush=True)

    def handle_update(self, update: dict) -> None:
        if "message" in update:
            self._handle_message(update["message"])
        elif "callback_query" in update:
            self._handle_callback(update["callback_query"])

    def _is_fresh(self, message: dict) -> bool:
        return int(message.get("date", 0)) >= self.start_time

    def _handle_message(self, message: dict) -> None:
        if not self._is_fresh(message):
            return
        chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip()
        command = text.split()[0] if text else ""
        if command in ("/start", "/menu"):
            self.api.send_message(chat_id, MENU_TEXT, _menu_keyboard())
        elif command == "/status":
            self.api.send_message(chat_id, self.status_text())
        elif command in ("/feeds", "/links"):
            self.api.send_message(chat_id, self.feeds_text())
        elif command == "/help":
            self.api.send_message(chat_id, HELP_TEXT)
        elif command.startswith("/"):
            self.api.send_message(chat_id, UNKNOWN_TEXT)
        # Plain text is ignored.

    def _handle_callback(self, callback: dict) -> None:
        data = callback.get("data") or ""
        message = callback.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")
        user_id = (callback.get("from") or {}).get("id")
        if chat_id is None:
            return
        if data == "status":
            self.api.answer_callback_query(callback["id"])
            self.api.send_message(chat_id, self.status_text())
        elif data == "feeds":
            self.api.answer_callback_query(callback["id"])
            self.api.send_message(chat_id, self.feeds_text())
        elif data in ("discover", "aggregate"):
            error = self._owner_check(user_id)
            if error is not None:
                self.api.answer_callback_query(callback["id"], error)
                return
            self.api.answer_callback_query(callback["id"])
            verb = "discovery" if data == "discover" else "update"
            self.api.edit_message_text(
                chat_id, message_id, f"Run {verb} now?", _confirm_keyboard(data)
            )
        elif data in ("confirm_discover", "confirm_aggregate"):
            error = self._owner_check(user_id)
            if error is not None:
                self.api.answer_callback_query(callback["id"], error)
                return
            self.api.answer_callback_query(callback["id"], "Starting...")
            kind = "discover" if data == "confirm_discover" else "aggregate"
            self._run_trigger(chat_id, message_id, kind)
        elif data == "cancel":
            self.api.answer_callback_query(callback["id"], "Cancelled.")
            if message_id is not None:
                self.api.edit_message_text(chat_id, message_id, "Cancelled.")
        else:
            self.api.answer_callback_query(callback["id"], "Unknown action.")

    def _owner_check(self, user_id: int | None) -> str | None:
        """Return an error reply when this user may not trigger, else None.

        Denied attempts are logged with the sender id (server-side only;
        the id is never sent back to the chat).
        """
        if self.config.owner_id is None:
            self._log(f"trigger attempt with no owner configured (user {user_id})")
            return "Running a workflow is not set up yet: no owner is configured."
        if user_id is None or str(user_id) != self.config.owner_id:
            self._log(f"trigger attempt denied (user {user_id})")
            return "This action is owner-only."
        return None

    def _run_trigger(self, chat_id: int, message_id: int | None, kind: str) -> None:
        if kind == "discover":
            workflow, inputs, label = DISCOVERY_WORKFLOW, DISCOVERY_INPUTS, "Discovery"
        else:
            workflow, inputs, label = UPDATE_WORKFLOW, UPDATE_INPUTS, "Update"
        try:
            self.github.dispatch(self.config.github_repo, workflow, inputs)
        except Exception as exc:
            self._log(f"dispatch {workflow} failed: {exc}")
            text = f"{label} could not be started: {exc}"
        else:
            self._log(f"dispatched {workflow}")
            text = (
                f"{label} started.\n"
                f"https://github.com/{self.config.github_repo}"
                f"/actions/workflows/{workflow}"
            )
        if message_id is not None:
            self.api.edit_message_text(chat_id, message_id, text)
        else:
            self.api.send_message(chat_id, text)

    def status_text(self) -> str:
        lines = []
        for label, repo_key, workflow in STATUS_WORKFLOWS:
            repo = self.config.github_repo if repo_key == "repo" else self.config.feed_data_repo
            try:
                run = self.github.latest_run(repo, workflow)
            except Exception as exc:
                self._log(f"status {workflow} failed: {exc}")
                lines.append(f"{label}: unknown (could not reach GitHub)")
                continue
            if run is None:
                lines.append(f"{label}: no runs yet")
                continue
            lines.append(self._format_run(label, run))
        return "Latest runs:\n" + "\n".join(lines)

    @staticmethod
    def _format_run(label: str, run: dict) -> str:
        conclusion = run.get("conclusion") or run.get("status") or "unknown"
        started = _format_time(run.get("created_at") or run.get("updated_at") or "")
        url = run.get("html_url", "")
        return f"{label}: {conclusion} — {started} — {url}"

    def feeds_text(self) -> str:
        lines = ["Links", "", f"Page: {self.config.mirror_url}", f"Site: {self.config.site_url}", ""]
        for label, filename in FEED_FILES:
            lines.append(f"{label}: {self.config.mirror_base}/{filename}")
        return "\n".join(lines)

    def poll_once(self) -> None:
        try:
            updates = self.api.get_updates(self._offset, timeout=30)
        except Exception as exc:
            self._log(f"getUpdates failed: {exc}")
            time.sleep(5)
            return
        for update in updates:
            self._offset = update["update_id"] + 1
            try:
                self.handle_update(update)
            except Exception as exc:
                self._log(f"update {update.get('update_id')} failed: {exc}")

    def run(self) -> None:
        self._log("bot started")
        while True:
            self.poll_once()


def main() -> None:
    config = Config.from_env()
    bot = Bot(config)
    try:
        bot.run()
    except KeyboardInterrupt:
        print("stopped", flush=True)


if __name__ == "__main__":
    main()
