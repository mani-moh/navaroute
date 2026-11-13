import json
from pathlib import Path
import unittest

from src.discovery.state_manager import DiscoveryStateManager, serialize_state
from src.fetcher import load_sources
from src.models import SourceConfig

ROOT = Path(__file__).resolve().parents[1]


class TestRepositoryInputs(unittest.TestCase):
    def test_shipped_discovery_checkpoint_loads_without_discarding_saved_state(self):
        path = ROOT / "data/discovery_state.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        state = DiscoveryStateManager(str(path)).get_state()
        self.assertEqual({key: state[key] for key in saved}, saved)
        self.assertEqual(json.loads(serialize_state(state)), state)

    def test_shipped_unified_registry_loads_every_enabled_public_source(self):
        # One registry: sources.json carries curated and discovered
        # sources alike, each with a description and an origin.
        rows = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))["sources"]
        expected_ids = set()
        seen_urls = set()
        for index, row in enumerate(rows):
            with self.subTest(row=index):
                source = SourceConfig.from_dict(row)
                self.assertTrue(source.description, "every source needs a description")
                self.assertIn(source.origin, ("curated", "discovered"))
                self.assertNotIn(source.url, seen_urls, "duplicate source URL")
                seen_urls.add(source.url)
                if source.enabled:
                    expected_ids.add(source.id)
        self.assertEqual(len(expected_ids), len({row["id"] for row in rows if row.get("enabled", True)}))
        loaded = load_sources(str(ROOT / "sources.json"))
        self.assertEqual({source.id for source in loaded}, expected_ids)
        self.assertFalse((ROOT / "sources.discovered.json").exists(),
                         "legacy second registry must not exist anymore")
