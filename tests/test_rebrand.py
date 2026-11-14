import base64
import hashlib
import json
import os
from pathlib import Path
import re
import unittest

from src.parsers import KNOWN_ISO_CODES, parse_proxy_uri

ROOT = Path(__file__).resolve().parents[1]
# Full 64k-node snapshot check (~70s) runs only when opted in or on the
# nightly snapshot job. The default CI run samples 200 rows per file,
# which still exercises parsing, canonicalisation, base64 mirrors and
# per-file invariants at ~1s.
FULL_SNAPSHOT = os.environ.get("NAVAROUTE_FULL_SNAPSHOT") == "1"
SAMPLE_ROWS = 200


class TestPublicMigration(unittest.TestCase):
    def test_unique_markers_and_active_prose_no_legacy_product_identity(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("<!-- NAVAROUTE:STATS:START -->"), 1)
        self.assertEqual(text.count("<!-- NAVAROUTE:STATS:END -->"), 1)
        for file in ("README.md", "CONTRIBUTING.md", "docs/ARCHITECTURE.md", ".env.example", "src/__init__.py"):
            text = (ROOT / file).read_text(encoding="utf-8")
            self.assertNotIn("mani-moh/pattn", text)
            self.assertNotIn("PattN Desktop", text)
            self.assertIn("NavaRoute", text)

    def test_every_generated_profile_and_decoded_uri_is_canonical(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        counts = dict((name, int(count)) for name, count in re.findall(r"\| \[([^\]]+\.txt)\]\([^\n]+\) \| (\d+) \|", readme))
        all_nodes = set()
        groups = {}
        for path in (ROOT / "subs").rglob("*.txt"):
            if path.stem.endswith("_base64"):
                continue
            with self.subTest(file=path.relative_to(ROOT).as_posix()):
                text = path.read_text(encoding="utf-8")
                title = text.splitlines()[0].removeprefix("#profile-title: base64:")
                self.assertTrue(base64.b64decode(title, validate=True).decode().startswith("NavaRoute "))
                rows = [line for line in text.splitlines() if line and not line.startswith("#")]
                encoded = path.with_name(path.stem + "_base64.txt").read_text(encoding="utf-8")
                if FULL_SNAPSHOT:
                    self.assertTrue(base64.b64decode(encoded, validate=True).decode() == "\n".join(rows), "plain/Base64 rows differ")
                    # Tier-1 / tier-2 variant feeds are published by the
                    # tier workflows, not the aggregator, so the README
                    # stats table has no row for them; their row counts
                    # are pinned against tier1_results.json instead
                    # (tests/test_tier1.py TierVariantCountsTests).
                    if not (path.stem.startswith("tier1_") or path.stem.startswith("iran_")):
                        self.assertEqual(counts[path.relative_to(ROOT / "subs").as_posix()], len(rows))
                else:
                    # Sample mode: the base64 mirror still covers every
                    # row (cheap decode), but only the first 200 rows are
                    # parsed/canonicalised below.
                    self.assertTrue(base64.b64decode(encoded, validate=True).decode() == "\n".join(rows), "plain/Base64 rows differ")
                    rows = rows[:SAMPLE_ROWS]
                fingerprints = set()
                for row in rows:
                    node = parse_proxy_uri(row)
                    self.assertIsNotNone(node, "unparseable public row")
                    country = re.search(r"\[\S+ ([A-Z]{2})\]", node.remark)
                    self.assertIsNotNone(country, "generated country claim missing")
                    self.assertEqual(node.country_code, country.group(1), "reingestion changed explicit country metadata")
                    self.assertEqual(node.healthcheck_kind, "untested")
                    self.assertFalse(node.passed_healthcheck)
                    self.assertNotIn("PattN", node.remark)
                    self.assertNotIn("[PattN F&F]", node.remark)
                    self.assertNotIn("[PattN IPv6-OOB]", node.remark)
                    self.assertNotIn("[PattN SNI-Spoof]", node.remark)
                    fingerprint = node.semantic_fingerprint()
                    self.assertEqual(parse_proxy_uri(node.to_uri()).semantic_fingerprint(), fingerprint)
                    fingerprints.add(fingerprint)
                    if path.parent.name == "countries":
                        country = re.search(r"\[\S+ ([A-Z]{2})\]", node.remark)
                        self.assertIsNotNone(country, "generated country claim missing")
                        self.assertEqual(country.group(1).lower(), path.stem)
                    if path.stem in ("navaroute-special", "pattn-special"):
                        self.assertTrue(node.has_special_parameters(), "special group row has no supplied special parameter")
                    if row.startswith("vmess://") and "@" not in row:
                        record = json.loads(base64.b64decode(row[8:], validate=True))
                        self.assertNotIn("PattN", record["ps"])
                self.assertEqual(len(fingerprints), len(rows), "duplicate complete public identities")
                if path.name == "all.txt":
                    all_nodes = fingerprints
                groups[path.relative_to(ROOT / "subs").as_posix()] = fingerprints
                if path.parent.name == "countries":
                    self.assertIn(path.stem.upper(), KNOWN_ISO_CODES)
                    self.assertGreaterEqual(len(rows), 5)
        self.assertTrue(all_nodes, "accepted public pool must not be empty")
        if FULL_SNAPSHOT:
            # Cross-file subset checks only hold on complete rows.
            # Tier-1 / tier-2 variant feeds are a point-in-time test
            # snapshot: they legitimately lag the latest aggregation
            # (a feed refresh drops configs the last tier run passed),
            # so their identities may no longer be in all.txt.
            for name, group in groups.items():
                if name.startswith("tier1_") or name.startswith("iran_"):
                    continue
                self.assertTrue(group <= all_nodes, "feed contains identity outside accepted public pool")
            self.assertTrue(groups["top100.txt"] <= groups["reachable.txt"])
            self.assertLessEqual(len(groups["top100.txt"]), 100)
            self.assertLessEqual(len(groups["lite500.txt"]), 500)

    def test_historical_migration_has_empty_evidence_and_retired_json(self):
        path = ROOT / "docs/migration-manifest.json"
        self.assertTrue(path.is_file(), "historical migration provenance missing")
        manifest = json.loads(path.read_bytes())
        self.assertFalse(manifest["tested"])
        self.assertFalse(manifest["owner_evidence"])
        self.assertEqual(manifest["observation_status"], "untested")
        self.assertEqual(manifest["input"]["rows"], manifest["validated_input_rows"] + sum(manifest["rejected_by_reviewed_parser"].values()))
        self.assertEqual(manifest["accepted_public_connections"], manifest["validated_input_rows"] - sum(manifest["factory_exclusions"].values()) - manifest["canonical_duplicates_collapsed"])
        for name in ("reachable", "top100", "verified"):
            self.assertEqual(manifest["counts"][name + ".txt"], 0)
        # The immutable migration ledger is not a demand that future genuine generations remain untested.
        if f'# snapshot-time: {manifest["input"]["generation_time"]}' in (ROOT / "subs/all.txt").read_text(encoding="utf-8"):
            for name in ("reachable", "top100", "verified"):
                self.assertFalse((ROOT / f"subs/{name}_base64.txt").read_bytes(), "historical migration must not invent socket evidence")
            actual = {p.relative_to(ROOT / "subs").as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "subs").rglob("*") if p.is_file()}
            self.assertTrue(actual == manifest["output_sha256"], "historical migration inventory/digests differ")
        self.assertEqual(json.loads((ROOT / "subs/categories/serverless-fragment.json").read_text()), [])
        self.assertTrue((ROOT / "subs/categories/navaroute-special_base64.txt").is_file(), "primary special feed missing")
        a = (ROOT / "subs/categories/navaroute-special_base64.txt").read_bytes()
        self.assertTrue(a == (ROOT / "subs/categories/pattn-special_base64.txt").read_bytes(), "special alias rows differ")
        self.assertTrue((ROOT / "subs/reachable_base64.txt").read_bytes() == (ROOT / "subs/verified_base64.txt").read_bytes(), "deprecated reachability mirror differs")


if __name__ == "__main__":
    unittest.main()
