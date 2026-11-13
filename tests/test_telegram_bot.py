"""Tests for the Telegram bot (tools/telegram_bot/bot.py)."""

from __future__ import annotations

import io
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "telegram_bot"))

import bot as botmod  # noqa: E402

# Words the /feeds message must never show in visible text. URLs are
# stripped first (the site link itself contains the repo name).
FORBIDDEN = (
    "vpn", "proxy", "bypass", "config", "subscription", "navaroute",
    "xray", "vless", "vmess", "trojan", "shadowsocks", "iran", "telegram",
)

NOW = 1_700_000_000


def _visible_text(text: str) -> str:
    return re.sub(r"https?://\S+", "", text).lower()


class FakeApi:
    """Records Telegram API calls; get_updates returns queued updates."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.answered: list[dict] = []
        self.edited: list[dict] = []
        self.updates: list[dict] = []

    def get_updates(self, offset, timeout=30):
        return list(self.updates)

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return {"message_id": len(self.sent)}

    def answer_callback_query(self, callback_query_id, text=None):
        self.answered.append({"id": callback_query_id, "text": text})
        return True

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edited.append(
            {"chat_id": chat_id, "message_id": message_id, "text": text,
             "reply_markup": reply_markup}
        )
        return True


class FakeGitHub:
    def __init__(self, runs=None) -> None:
        self.runs = runs or {}
        self.dispatched: list[tuple] = []
        self.fail_dispatch = False
        self.fail_latest = False

    def latest_run(self, repo, workflow):
        if self.fail_latest:
            raise RuntimeError("github down")
        return self.runs.get((repo, workflow))

    def dispatch(self, repo, workflow, inputs):
        if self.fail_dispatch:
            raise RuntimeError("boom")
        self.dispatched.append((repo, workflow, inputs))
        return True


def _message(text, chat_id=111, user_id=222, date=NOW):
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": date,
            "chat": {"id": chat_id},
            "from": {"id": user_id},
            "text": text,
        },
    }


def _callback(data, user_id=222, chat_id=111, message_id=7):
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "data": data,
            "from": {"id": user_id},
            "message": {"message_id": message_id, "chat": {"id": chat_id}},
        },
    }


def _make_bot(owner_id=None, github=None):
    config = botmod.Config(bot_token="TEST", owner_id=owner_id)
    api = FakeApi()
    logs: list[str] = []
    bot = botmod.Bot(
        config, api=api, github=github or FakeGitHub(), logger=logs.append, start_time=NOW
    )
    return bot, api, logs


class TestConfig(unittest.TestCase):
    def test_missing_token_fails_fast(self) -> None:
        with self.assertRaises(SystemExit):
            botmod.Config.from_env({})

    def test_from_env_defaults(self) -> None:
        config = botmod.Config.from_env({"TELEGRAM_BOT_TOKEN": "tok"})
        self.assertEqual(config.github_repo, "mani-moh/navaroute")
        self.assertEqual(config.feed_data_repo, "mani-moh/feed-data")
        self.assertIsNone(config.owner_id)
        self.assertEqual(
            config.mirror_base, "https://repoget.s3.ir-central1.arvanstorage.ir"
        )

    def test_mirror_base_keeps_bare_url(self) -> None:
        config = botmod.Config(bot_token="t", owner_id=None, mirror_url="https://x.invalid/")
        self.assertEqual(config.mirror_base, "https://x.invalid")

    def test_format_time(self) -> None:
        self.assertEqual(botmod._format_time("2026-11-13T22:13:25Z"), "2026-11-13 22:13 UTC")
        self.assertEqual(botmod._format_time("garbage"), "garbage")
        self.assertEqual(botmod._format_time(""), "unknown time")


class TestRouting(unittest.TestCase):
    def test_start_sends_menu_with_keyboard(self) -> None:
        bot, api, _ = _make_bot()
        bot.handle_update(_message("/start"))
        self.assertEqual(len(api.sent), 1)
        self.assertIn("Choose an action", api.sent[0]["text"])
        keyboard = api.sent[0]["reply_markup"]["inline_keyboard"]
        callbacks = [b["callback_data"] for row in keyboard for b in row]
        self.assertEqual(callbacks, ["status", "feeds", "discover", "aggregate"])

    def test_help_and_unknown(self) -> None:
        bot, api, _ = _make_bot()
        bot.handle_update(_message("/help"))
        self.assertIn("/status", api.sent[0]["text"])
        bot.handle_update(_message("/bogus"))
        self.assertIn("Unknown command", api.sent[1]["text"])

    def test_plain_text_is_ignored(self) -> None:
        bot, api, _ = _make_bot()
        bot.handle_update(_message("hello"))
        self.assertEqual(api.sent, [])

    def test_old_messages_are_ignored(self) -> None:
        bot, api, _ = _make_bot()
        bot.handle_update(_message("/status", date=NOW - 100))
        self.assertEqual(api.sent, [])
        bot.handle_update(_message("/status", date=NOW))
        self.assertEqual(len(api.sent), 1)

    def test_callback_status_and_feeds(self) -> None:
        bot, api, _ = _make_bot()
        bot.handle_update(_callback("status"))
        self.assertIn("Latest runs", api.sent[0]["text"])
        bot.handle_update(_callback("feeds"))
        self.assertIn("Links", api.sent[1]["text"])
        self.assertEqual([a["id"] for a in api.answered], ["cb1", "cb1"])

    def test_poll_once_advances_offset_and_ignores_bad_update(self) -> None:
        bot, api, logs = _make_bot()
        # Second update is a message with no "chat": _handle_message raises,
        # poll_once logs it and keeps going.
        bad = {"update_id": 9, "message": {"message_id": 1, "date": NOW, "text": "/start"}}
        api.updates = [_message("/start"), bad]
        bot.poll_once()
        self.assertEqual(len(api.sent), 1)
        self.assertEqual(bot._offset, 10)
        self.assertTrue(any("update 9 failed" in line for line in logs))

    def test_poll_once_survives_getupdates_failure(self) -> None:
        bot, api, logs = _make_bot()

        def _boom(offset, timeout=30):
            raise RuntimeError("network down")

        api.get_updates = _boom
        with mock.patch.object(botmod.time, "sleep") as sleep:
            bot.poll_once()
        sleep.assert_called_once_with(5)
        self.assertTrue(any("getUpdates failed" in line for line in logs))


class TestStatus(unittest.TestCase):
    def test_status_formats_runs(self) -> None:
        runs = {
            ("mani-moh/navaroute", "daily-deep-discovery.yml"): {
                "conclusion": "success",
                "created_at": "2026-11-13T22:13:25Z",
                "html_url": "https://github.com/mani-moh/navaroute/actions/runs/1",
            },
            ("mani-moh/navaroute", "update-subs.yml"): {
                "conclusion": None,
                "status": "in_progress",
                "created_at": "2026-11-14T01:00:00Z",
                "html_url": "https://github.com/mani-moh/navaroute/actions/runs/2",
            },
            ("mani-moh/feed-data", "deploy.yml"): {
                "conclusion": "failure",
                "created_at": "2026-11-13T20:00:00Z",
                "html_url": "https://github.com/mani-moh/feed-data/actions/runs/3",
            },
        }
        bot, api, _ = _make_bot(github=FakeGitHub(runs))
        bot.handle_update(_message("/status"))
        text = api.sent[0]["text"]
        self.assertIn("Discovery: success — 2026-11-13 22:13 UTC — "
                      "https://github.com/mani-moh/navaroute/actions/runs/1", text)
        self.assertIn("Update: in_progress — 2026-11-14 01:00 UTC", text)
        self.assertIn("Tier-1 test: no runs yet", text)
        self.assertIn("Public site: no runs yet", text)
        self.assertIn("Mirror deploy: failure — 2026-11-13 20:00 UTC — "
                      "https://github.com/mani-moh/feed-data/actions/runs/3", text)

    def test_status_fetch_failure_reports_unknown(self) -> None:
        github = FakeGitHub()
        github.fail_latest = True
        bot, api, _ = _make_bot(github=github)
        bot.handle_update(_message("/status"))
        self.assertIn("Discovery: unknown (could not reach GitHub)", api.sent[0]["text"])


class TestFeeds(unittest.TestCase):
    def test_feeds_contains_links_and_neutral_labels(self) -> None:
        bot, api, _ = _make_bot()
        bot.handle_update(_message("/feeds"))
        text = api.sent[0]["text"]
        self.assertIn("https://repoget.s3.ir-central1.arvanstorage.ir/index.html", text)
        self.assertIn("https://mani-moh.github.io/navaroute/", text)
        for filename in (
            "tier1_all_base64.txt", "reachable_base64.txt", "top100_base64.txt",
            "lite500_base64.txt", "tier1_top100_base64.txt", "tier1_lite500_base64.txt",
            "tier1_reachable_base64.txt",
        ):
            self.assertIn(
                f"https://repoget.s3.ir-central1.arvanstorage.ir/{filename}", text
            )
        for label in ("working", "online", "top 100", "lite"):
            self.assertIn(label, text)
        visible = _visible_text(text)
        for word in FORBIDDEN:
            self.assertNotIn(word, visible, f"forbidden word {word!r} in /feeds text")


class TestOwnerGate(unittest.TestCase):
    def test_trigger_without_owner_is_refused_and_logged(self) -> None:
        bot, api, logs = _make_bot(owner_id=None)
        bot.handle_update(_callback("discover", user_id=222))
        self.assertEqual(api.sent, [])
        self.assertEqual(api.edited, [])
        self.assertIn("not set up yet", api.answered[0]["text"])
        self.assertTrue(any("222" in line for line in logs))
        self.assertEqual(bot.github.dispatched, [])

    def test_trigger_by_non_owner_is_refused(self) -> None:
        bot, api, logs = _make_bot(owner_id="999")
        bot.handle_update(_callback("aggregate", user_id=222))
        self.assertIn("owner-only", api.answered[0]["text"])
        self.assertTrue(any("222" in line for line in logs))
        self.assertEqual(bot.github.dispatched, [])

    def test_owner_trigger_shows_confirmation_without_dispatching(self) -> None:
        bot, api, _ = _make_bot(owner_id="222")
        bot.handle_update(_callback("discover", user_id=222))
        self.assertEqual(bot.github.dispatched, [])
        self.assertEqual(len(api.edited), 1)
        self.assertIn("Run discovery now?", api.edited[0]["text"])
        callbacks = [b["callback_data"] for row in api.edited[0]["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual(callbacks, ["confirm_discover", "cancel"])

    def test_confirm_dispatches_discovery(self) -> None:
        bot, api, _ = _make_bot(owner_id="222")
        bot.handle_update(_callback("confirm_discover", user_id=222))
        self.assertEqual(
            bot.github.dispatched,
            [("mani-moh/navaroute", "daily-deep-discovery.yml",
              {"max_api_requests": "500", "max_time_seconds": "5400", "max_channels": "2000"})],
        )
        self.assertIn("Discovery started.", api.edited[0]["text"])
        self.assertIn("daily-deep-discovery.yml", api.edited[0]["text"])

    def test_confirm_dispatches_update(self) -> None:
        bot, api, _ = _make_bot(owner_id="222")
        bot.handle_update(_callback("confirm_aggregate", user_id=222))
        self.assertEqual(
            bot.github.dispatched,
            [("mani-moh/navaroute", "update-subs.yml",
              {"run_healthcheck": "true", "max_test_nodes": "1000"})],
        )
        self.assertIn("Update started.", api.edited[0]["text"])

    def test_confirm_by_non_owner_is_refused(self) -> None:
        bot, api, _ = _make_bot(owner_id="999")
        bot.handle_update(_callback("confirm_discover", user_id=222))
        self.assertEqual(bot.github.dispatched, [])
        self.assertIn("owner-only", api.answered[0]["text"])

    def test_cancel_edits_to_cancelled(self) -> None:
        bot, api, _ = _make_bot(owner_id="222")
        bot.handle_update(_callback("cancel", user_id=222))
        self.assertEqual(api.edited[0]["text"], "Cancelled.")
        self.assertEqual(bot.github.dispatched, [])

    def test_dispatch_failure_is_reported(self) -> None:
        github = FakeGitHub()
        github.fail_dispatch = True
        bot, api, logs = _make_bot(owner_id="222", github=github)
        bot.handle_update(_callback("confirm_discover", user_id=222))
        self.assertIn("could not be started", api.edited[0]["text"])
        self.assertTrue(any("dispatch" in line for line in logs))


class TestGitHubClient(unittest.TestCase):
    def test_http_dispatch_sends_token_and_inputs(self) -> None:
        seen: list[dict] = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            seen.append({"url": req.full_url, "method": req.get_method(),
                         "headers": dict(req.header_items()),
                         "body": json.loads(req.data.decode("utf-8"))})
            return Resp(b"{}")

        with mock.patch.object(botmod.urllib.request, "urlopen", fake_urlopen):
            client = botmod.GitHubClient(token="ghp_test")
            self.assertTrue(client.dispatch("mani-moh/navaroute", "update-subs.yml",
                                            {"run_healthcheck": "true"}))
        self.assertEqual(len(seen), 1)
        call = seen[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(
            call["url"],
            "https://api.github.com/repos/mani-moh/navaroute/actions/workflows/"
            "update-subs.yml/dispatches",
        )
        self.assertEqual(call["headers"]["Authorization"], "Bearer ghp_test")
        self.assertEqual(call["body"], {"ref": "master", "inputs": {"run_healthcheck": "true"}})

    def test_http_latest_run_parses_payload(self) -> None:
        payload = {"workflow_runs": [{"conclusion": "success", "html_url": "https://x/1"}]}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch.object(
            botmod.urllib.request, "urlopen", lambda req, timeout=None: Resp(
                json.dumps(payload).encode("utf-8"))
        ):
            client = botmod.GitHubClient(token="ghp_test")
            run = client.latest_run("mani-moh/navaroute", "daily-deep-discovery.yml")
        self.assertEqual(run["conclusion"], "success")

    def test_gh_fallback_dispatch(self) -> None:
        seen: list[dict] = []

        def fake_run(cmd, input=None, capture_output=None, timeout=None):
            seen.append({"cmd": cmd, "input": json.loads(input.decode("utf-8"))})
            return mock.Mock(returncode=0, stdout=b"{}", stderr=b"")

        env = {"PATH": "/usr/bin:/bin"}  # no GITHUB_TOKEN
        with mock.patch.object(botmod.subprocess, "run", fake_run), \
                mock.patch.dict(botmod.os.environ, env, clear=True):
            client = botmod.GitHubClient()
            self.assertTrue(client.dispatch("mani-moh/navaroute", "update-subs.yml",
                                            {"run_healthcheck": "true"}))
        self.assertEqual(
            seen[0]["cmd"],
            ["gh", "api",
             "repos/mani-moh/navaroute/actions/workflows/update-subs.yml/dispatches",
             "--method", "POST", "--input", "-"],
        )
        self.assertEqual(seen[0]["input"],
                         {"ref": "master", "inputs": {"run_healthcheck": "true"}})

    def test_gh_failure_raises(self) -> None:
        def fake_run(cmd, input=None, capture_output=None, timeout=None):
            return mock.Mock(returncode=1, stdout=b"", stderr=b"HTTP 404")

        with mock.patch.object(botmod.subprocess, "run", fake_run), \
                mock.patch.dict(botmod.os.environ, {}, clear=True):
            client = botmod.GitHubClient()
            with self.assertRaisesRegex(RuntimeError, "gh api .* failed: HTTP 404"):
                client.latest_run("mani-moh/navaroute", "nope.yml")


if __name__ == "__main__":
    unittest.main()
