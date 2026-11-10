import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.storage import atomic_write_text


class TestAtomicWriteText(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / "state.json"

    def test_atomic_writer_cleans_own_temp(self):
        unrelated = self.root / "state.tmp"
        unrelated.write_bytes(b"unrelated sentinel\x00")
        other_temporary = self.root / ".state.json.other.tmp"
        other_temporary.write_bytes(b"another writer owns this")
        before = set(self.root.iterdir())
        for content in ("new\nNavaRoute \u0646\n", "", "replacement\n"):
            with self.subTest(content=content):
                atomic_write_text(self.target, content)
                self.assertEqual(self.target.read_bytes(), content.encode("utf-8"))
                self.assertEqual(set(self.root.iterdir()), before | {self.target})
                self.assertEqual(unrelated.read_bytes(), b"unrelated sentinel\x00")
                self.assertEqual(other_temporary.read_bytes(), b"another writer owns this")

    def test_failed_replace_preserves_original(self):
        self.target.write_bytes(b"original sentinel\x00\xff")
        unrelated = self.root / "state.tmp"
        unrelated.write_bytes(b"not owned by the writer")
        before = set(self.root.iterdir())
        failure = PermissionError("replacement denied")

        def fail_replace(source, destination):
            self.assertEqual(source.parent, self.target.parent)
            self.assertNotEqual(source, self.target)
            self.assertEqual(source.read_bytes(), b"new content")
            self.assertEqual(destination, self.target)
            self.assertEqual(self.target.read_bytes(), b"original sentinel\x00\xff")
            raise failure

        with patch.object(Path, "replace", fail_replace):
            with self.assertRaises(PermissionError) as raised:
                atomic_write_text(self.target, "new content")
        self.assertIs(raised.exception, failure)
        self.assertEqual(self.target.read_bytes(), b"original sentinel\x00\xff")
        self.assertEqual(unrelated.read_bytes(), b"not owned by the writer")
        self.assertEqual(set(self.root.iterdir()), before)

    def test_failed_encoding_cleans_temp_and_preserves_original(self):
        self.target.write_bytes(b"original")
        with self.assertRaises(UnicodeEncodeError):
            atomic_write_text(self.target, "invalid surrogate: \ud800")
        self.assertEqual(self.target.read_bytes(), b"original")
        self.assertEqual(set(self.root.iterdir()), {self.target})

    def test_missing_parent_propagates_error(self):
        with self.assertRaises(FileNotFoundError):
            atomic_write_text(self.root / "missing" / "state.json", "new")
        self.assertEqual(list(self.root.iterdir()), [])
