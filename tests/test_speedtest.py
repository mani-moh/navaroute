"""Offline unit tests for throughput testing, state pruning, ranking, and speed-100 feeds."""
from __future__ import annotations

import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import public_git, speedtest_feeds
from src.healthcheck import CoreInfrastructureError
from src.speedtest import (
    SPEED_FEED_LIMIT,
    SpeedBatchResult,
    build_speed_results,
    collect_candidates,
    load_speed_state,
    measure_batch_xray,
    prune_speed_state,
    rank_top_speed,
    render_speed_feed,
    save_speed_state,
    select_due_speed,
    speed_backoff_hours,
    update_speed_state,
)

NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)


def _vless(idx: int, host: str = "1.1.1.1") -> str:
    uid = f"00000000-0000-4000-8000-{idx:012d}"
    return f"vless://{uid}@{host}:{443 + (idx % 100)}?security=tls&type=tcp#node-{idx}"


class TestSpeedtestStateAndRanking(unittest.TestCase):
    def test_backoff_schedule(self) -> None:
        self.assertEqual(speed_backoff_hours(0), 0)
        self.assertEqual(speed_backoff_hours(1), 8)
        self.assertEqual(speed_backoff_hours(2), 16)
        self.assertEqual(speed_backoff_hours(3), 32)
        self.assertEqual(speed_backoff_hours(4), 48)
        self.assertEqual(speed_backoff_hours(10), 48)

    def test_state_roundtrip_and_pruning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speed_state.json"
            old_ts = (NOW - timedelta(days=31)).isoformat()
            fresh_ts = (NOW - timedelta(hours=2)).isoformat()
            raw = {
                "fp_live": {"failures": 0, "last_test": fresh_ts, "speed_mbps": 42.5, "latency_ms": 55.0},
                "fp_expired": {"failures": 0, "last_test": old_ts, "speed_mbps": 10.0, "latency_ms": 80.0},
                "fp_dead": {"failures": 1, "last_test": fresh_ts, "speed_mbps": None, "latency_ms": None},
            }
            save_speed_state(path, raw)
            pruned = load_speed_state(path, live_fingerprints={"fp_live", "fp_expired"}, now=NOW)
            self.assertEqual(set(pruned.keys()), {"fp_live"})
            self.assertEqual(pruned["fp_live"]["speed_mbps"], 42.5)
            self.assertEqual(prune_speed_state(raw, {"fp_live"}, now=NOW), {"fp_live": raw["fp_live"]})

    def test_update_and_select_due_priority(self) -> None:
        cands = [
            {"uri": _vless(1), "fingerprint": "fp1"},
            {"uri": _vless(2), "fingerprint": "fp2"},
            {"uri": _vless(3), "fingerprint": "fp3"},
        ]
        state = {}
        state = update_speed_state(state, {"fp1": (20.0, 50.0), "fp2": None}, NOW - timedelta(hours=2))
        # fp2 has failure streak 1 (8h backoff) -> skipped at +2h; fp3 is untested -> first, then fp1
        due = select_due_speed(cands, state, NOW, limit=10)
        self.assertEqual([d["fingerprint"] for d in due], ["fp3", "fp1"])

    def test_deterministic_ranking_and_top_100_cap(self) -> None:
        cands = [{"uri": _vless(i), "fingerprint": f"fp_{i:03d}"} for i in range(120)]
        state = {}
        for i in range(120):
            # Give 0..119 distinct or tied speeds; 105 & 106 tie on speed, 105 has lower latency
            speed = 50.0 if i in (105, 106) else float(i + 1)
            lat = 10.0 if i == 105 else (25.0 if i == 106 else 40.0)
            state[f"fp_{i:03d}"] = {
                "failures": 0 if i != 119 else 1,
                "last_test": NOW.isoformat(),
                "speed_mbps": speed if i != 119 else None,
                "latency_ms": lat if i != 119 else None,
            }
        # Tie-break on same speed and same latency uses fingerprint ascending
        state["fp_010"]["speed_mbps"] = 200.0
        state["fp_010"]["latency_ms"] = 15.0
        state["fp_009"]["speed_mbps"] = 200.0
        state["fp_009"]["latency_ms"] = 15.0

        ranked = rank_top_speed(cands, state, limit=SPEED_FEED_LIMIT)
        self.assertEqual(len(ranked), 100)
        self.assertEqual(ranked[0], _vless(9))   # fp_009 < fp_010
        self.assertEqual(ranked[1], _vless(10))
        self.assertNotIn(_vless(119), ranked)

    def test_render_speed_feed_and_base64_twin(self) -> None:
        uris = [_vless(1), _vless(2)]
        plain, encoded = render_speed_feed(uris, NOW.isoformat(), interval_hours=4)
        self.assertIn("#profile-title: base64:", plain)
        self.assertIn("#profile-update-interval: 4", plain)
        self.assertIn("GitHub runner locations", plain)
        header, body = plain.split("\n\n", 1)
        self.assertTrue(header.startswith("#"))
        self.assertEqual(body.strip(), "\n".join(uris))
        self.assertEqual(base64.b64decode(encoded).decode("utf-8"), "\n".join(uris))

    def test_collect_candidates_dedup_and_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            subs = Path(tmp)
            (subs / "tested.txt").write_text(_vless(1) + "\n" + _vless(2) + "\n", encoding="utf-8")
            (subs / "fastest-100.txt").write_text(_vless(2) + "\n" + _vless(3) + "\n", encoding="utf-8")
            (subs / "online.txt").write_text(_vless(4) + "\n", encoding="utf-8")
            cands = collect_candidates(subs, max_candidates=3)
            self.assertEqual([c["uri"] for c in cands], [_vless(1), _vless(2), _vless(3)])

    def test_measure_batch_xray_partial_results_on_budget(self) -> None:
        from src.models import SourceConfig
        from src.parsers import parse_proxy_uri

        src = SourceConfig("t", "t", "https://example.com")
        nodes = [parse_proxy_uri(_vless(i), src) for i in range(4)]
        prepared = [{"protocol": "vless"} for _ in range(4)]

        calls = 0

        def fake_run(items, xray_bin, timeout, test_url, workers):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {idx: (33.3, 40.0) for idx, _ in items}
            raise CoreInfrastructureError("simulated batch failure")

        with patch("src.speedtest._run_xray_speed_items", side_effect=fake_run):
            res = measure_batch_xray(
                nodes,
                "/usr/bin/xray" if Path("/usr/bin/xray").is_absolute() else "C:/xray/xray.exe",
                batch_size=2,
                workers=2,
                prepared=prepared,
            )
        self.assertIsInstance(res, SpeedBatchResult)
        self.assertEqual(set(res.passed.keys()), {0, 1})
        self.assertEqual(res.attempted, {0, 1})

    def test_driver_end_to_end_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subs = root / "subs"
            subs.mkdir()
            (subs / "tested.txt").write_text(_vless(1) + "\n" + _vless(2) + "\n", encoding="utf-8")
            state_path = root / "data" / "speed_state.json"
            results_path = root / "data" / "speed_results.json"

            fake_batch = SpeedBatchResult(passed={0: (88.5, 22.0), 1: (12.0, 90.0)}, attempted={0, 1})
            with patch("scripts.speedtest_feeds.measure_batch_xray", return_value=fake_batch):
                rc = speedtest_feeds.main([
                    "--xray-bin", str(root / "xray"),
                    "--subs-dir", str(subs),
                    "--state-path", str(state_path),
                    "--results-path", str(results_path),
                ])
            self.assertEqual(rc, 0)
            plain = (subs / "speed-100.txt").read_text(encoding="utf-8")
            b64 = (subs / "speed-100_base64.txt").read_text(encoding="utf-8")
            self.assertIn(_vless(1), plain)
            self.assertEqual(base64.b64decode(b64).decode("utf-8").splitlines(), [_vless(1), _vless(2)])
            report = json.loads(results_path.read_text(encoding="utf-8"))
            self.assertEqual(report["top_speed_mbps"], 88.5)
            self.assertEqual(report["top_feed_count"], 2)

    def test_public_git_allows_speed_outputs(self) -> None:
        for path in (
            "subs/speed-100.txt",
            "subs/speed-100_base64.txt",
            "subs/speed-100-tested.txt",
            "subs/speed-100-tested-iran_base64.txt",
            "data/speed_state.json",
            "data/speed_results.json",
        ):
            self.assertTrue(public_git.public_output(path), f"public_git rejected {path}")

    def test_build_speed_results_handles_empty(self) -> None:
        report = build_speed_results([], {}, set(), NOW, 0)
        self.assertEqual(report["passed"], 0)
        self.assertIsNone(report["top_speed_mbps"])


if __name__ == "__main__":
    unittest.main()
