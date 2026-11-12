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

    def test_shipped_registries_load_every_enabled_public_source(self):
        expected_ids = set()
        for filename in ("sources.json", "sources.discovered.json"):
            rows = json.loads((ROOT / filename).read_text(encoding="utf-8"))["sources"]
            for index, row in enumerate(rows):
                with self.subTest(registry=filename, row=index):
                    source = SourceConfig.from_dict(row)
                    if source.enabled:
                        expected_ids.add(source.id)
        loaded = load_sources(str(ROOT / "sources.json"), str(ROOT / "sources.discovered.json"))
        self.assertEqual({source.id for source in loaded}, expected_ids)
