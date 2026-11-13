"""Tests for the Lambda webhook entrypoint (tools/telegram_bot/lambda_handler.py)."""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "telegram_bot"))

import lambda_handler as lmod  # noqa: E402

UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 1,
        "date": 1_700_000_000,
        "chat": {"id": 111},
        "from": {"id": 222},
        "text": "/status",
    },
}


class _BotSpy:
    """Records Bot construction and handle_update calls; can raise."""

    created: list["_BotSpy"] = []

    def __init__(self, config, start_time=None):
        self.config = config
        self.start_time = start_time
        self.handled: list[dict] = []
        self.raise_on_handle = False
        _BotSpy.created.append(self)

    def handle_update(self, update):
        if self.raise_on_handle:
            raise RuntimeError("boom")
        self.handled.append(update)


class _FakeConfig:
    @classmethod
    def from_env(cls):
        return object()


class TestLambdaHandler(unittest.TestCase):
    def setUp(self) -> None:
        _BotSpy.created.clear()
        self._bot_patch = mock.patch.object(lmod, "Bot", _BotSpy)
        self._config_patch = mock.patch.object(lmod, "Config", _FakeConfig)
        self._bot_patch.start()
        self._config_patch.start()

    def tearDown(self) -> None:
        self._bot_patch.stop()
        self._config_patch.stop()

    def test_plain_body_calls_handle_update_once(self) -> None:
        result = lmod.handler({"body": json.dumps(UPDATE)}, None)
        self.assertEqual(result, {"statusCode": 200, "body": "ok"})
        self.assertEqual(len(_BotSpy.created), 1)
        bot = _BotSpy.created[0]
        self.assertEqual(bot.handled, [UPDATE])
        # Webhook delivery is the update itself: the polling freshness
        # filter must not drop it, so start_time is 0.
        self.assertEqual(bot.start_time, 0)

    def test_base64_body_is_decoded(self) -> None:
        raw = base64.b64encode(json.dumps(UPDATE).encode("utf-8")).decode("ascii")
        result = lmod.handler({"body": raw, "isBase64Encoded": True}, None)
        self.assertEqual(result, {"statusCode": 200, "body": "ok"})
        self.assertEqual(_BotSpy.created[0].handled, [UPDATE])

    def test_malformed_json_returns_200_without_handling(self) -> None:
        result = lmod.handler({"body": "{not json"}, None)
        self.assertEqual(result, {"statusCode": 200, "body": "ok"})
        self.assertEqual(_BotSpy.created, [])

    def test_non_object_body_returns_200_without_handling(self) -> None:
        result = lmod.handler({"body": "[1, 2, 3]"}, None)
        self.assertEqual(result, {"statusCode": 200, "body": "ok"})
        self.assertEqual(_BotSpy.created, [])

    def test_empty_and_missing_body_return_200(self) -> None:
        for event in ({}, {"body": ""}, {"body": None}):
            with self.subTest(event=event):
                result = lmod.handler(event, None)
                self.assertEqual(result, {"statusCode": 200, "body": "ok"})
        self.assertEqual(_BotSpy.created, [])

    def test_bot_exception_returns_200_not_500(self) -> None:
        with mock.patch.object(_BotSpy, "handle_update", side_effect=RuntimeError("boom")):
            result = lmod.handler({"body": json.dumps(UPDATE)}, None)
        self.assertEqual(result, {"statusCode": 200, "body": "ok"})

    def test_missing_token_returns_200(self) -> None:
        class _NoTokenConfig:
            @classmethod
            def from_env(cls):
                raise SystemExit(2)

        with mock.patch.object(lmod, "Config", _NoTokenConfig):
            result = lmod.handler({"body": json.dumps(UPDATE)}, None)
        self.assertEqual(result, {"statusCode": 200, "body": "ok"})
        self.assertEqual(_BotSpy.created, [])


if __name__ == "__main__":
    unittest.main()
