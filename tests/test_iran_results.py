from __future__ import annotations

import base64
import copy
import hashlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.models import ProxyNode

SHA = "a" * 40
DIGEST = "b" * 64


def manifest_for(nodes):
    return dict(schema_version=1, fingerprint_version=1, repository="mani-moh/navaroute",
                source_sha=SHA, source_digest=DIGEST, tester_revision=SHA,
                started_at="2026-11-10T12:00:00+00:00", completed_at="2026-11-10T12:01:00Z",
                context="owner-attested-direct-iran", core={"version": "Xray 25.10.15", "sha256": DIGEST},
                policy="https-204-v1", rounds=2,
                counts={"candidates": len(nodes), "tested": len(nodes), "passed": len(nodes), "failed": 0, "unsupported": 0, "untested": 0},
                observations=[{"fingerprint": n.semantic_fingerprint(), "status": "passed", "latency_ms": 12.5} for n in nodes])


def owner_responses():
    return {
        "user": {"login": "mani-moh", "id": 72442823, "type": "User"},
        "repos/mani-moh/navaroute": {"id": 1451928662, "full_name": "mani-moh/navaroute", "private": False,
            "owner": {"login": "mani-moh", "id": 72442823, "type": "User"}, "default_branch": "master", "permissions": {"admin": True, "push": True}},
        "repos/mani-moh/navaroute/branches/iran-results": {"name": "iran-results", "protected": True, "commit": {"sha": SHA}},
        "repos/mani-moh/navaroute/branches/iran-results/protection": {
            "enforce_admins": {"enabled": False}, "required_pull_request_reviews": {"dismiss_stale_reviews": True,
            "require_code_owner_reviews": True, "require_last_push_approval": True, "required_approving_review_count": 1,
            "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []}},
            "allow_force_pushes": {"enabled": False}, "allow_deletions": {"enabled": False}},
        "repos/mani-moh/navaroute/actions/permissions/workflow": {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False},
        "repos/mani-moh/navaroute/collaborators?affiliation=all&per_page=100&page=1": [{"login": "mani-moh", "id": 72442823, "type": "User", "permissions": {"admin": True, "push": True}}],
        "repos/mani-moh/navaroute/keys?per_page=100&page=1": [],
        "repos/mani-moh/navaroute/rulesets?includes_parents=true&per_page=100&page=1": [],
        "repos/mani-moh/navaroute/actions/secrets?per_page=100": {"total_count": 1, "secrets": [{"name": "NVIDIA_API_KEY"}]},
        f"repos/mani-moh/navaroute/contents/.github/CODEOWNERS?ref={SHA}": {"type": "file", "encoding": "base64", "content": base64.b64encode(b"* @mani-moh\n").decode()},
    }


class IranFixture(unittest.TestCase):
    def setUp(self):
        system = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH"}}
        environment = patch.dict(os.environ, system, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        from src import iran_results
        self.results = iran_results
        self.nodes = [ProxyNode("trojan", "1.1.1.1", 443, "public-fixture", remark="Public")]
        self.manifest = manifest_for(self.nodes)


class TestIranManifest(IranFixture):
    def test_render_is_exact_public_subset_without_manifest_credentials(self):
        files = self.results.render_result_files(self.manifest, self.nodes, source_sha=SHA, source_digest=DIGEST)
        self.assertEqual(set(files), {"data/iran-verification.json", "subs/iran-tested.txt", "subs/iran-tested_base64.txt"})
        self.assertNotIn("public-fixture", files["data/iran-verification.json"])
        plain = files["subs/iran-tested.txt"]
        self.assertIn("trojan://public-fixture@1.1.1.1:443#Public", plain)
        self.assertEqual(base64.b64decode(files["subs/iran-tested_base64.txt"]).decode(), "trojan://public-fixture@1.1.1.1:443#Public\n")

    def test_unknown_fields_fingerprints_duplicates_private_values_and_invalid_numbers_rejected(self):
        edits = [lambda m: m.update(owner="mani-moh"), lambda m: m.update(repository="other/repo"),
                 lambda m: m.update(source_sha="master"), lambda m: m.update(source_digest="c" * 64),
                 lambda m: m.update(tester_revision="master"), lambda m: m.update(context="private-sentinel"),
                 lambda m: m["core"].update(version="C:/private-sentinel/xray"),
                 lambda m: m["observations"][0].update(uri="private-sentinel"),
                 lambda m: m["observations"][0].update(fingerprint="c" * 64),
                 lambda m: m["observations"].append(copy.deepcopy(m["observations"][0])),
                 lambda m: m["observations"][0].update(latency_ms=-1),
                 lambda m: m["observations"][0].update(latency_ms=float("nan")),
                 lambda m: m["observations"][0].update(latency_ms=float("inf")),
                 lambda m: m["observations"][0].update(latency_ms=True),
                 lambda m: m.update(rounds=0), lambda m: m.update(rounds=True),
                 lambda m: m.update(completed_at="2026-11-10T11:59:00Z"),
                 lambda m: m.update(started_at="2026-11-10T12:00:00+03:30"),
                 lambda m: m["counts"].update(tested=0)]
        for edit in edits:
            m = copy.deepcopy(self.manifest)
            edit(m)
            with self.subTest(edit=edit), self.assertRaises(self.results.IranResultsError):
                self.results.validate_manifest(m, self.nodes, source_sha=SHA, source_digest=DIGEST)

    def test_genuine_zero_survivors_allowed_but_zero_actual_work_and_oversize_rejected(self):
        m = copy.deepcopy(self.manifest)
        m["observations"][0].update(status="failed", latency_ms=None)
        m["counts"].update(passed=0, failed=1)
        files = self.results.render_result_files(m, self.nodes, source_sha=SHA, source_digest=DIGEST)
        self.assertNotIn("trojan://", files["subs/iran-tested.txt"])
        m["observations"][0]["status"] = "unsupported"
        m["counts"].update(tested=0, failed=0, unsupported=1)
        with self.assertRaises(self.results.IranResultsError):
            self.results.validate_manifest(m, self.nodes, source_sha=SHA, source_digest=DIGEST)
        with patch.object(self.results, "MAX_MANIFEST_BYTES", 1), self.assertRaises(self.results.IranResultsError):
            self.results.validate_manifest(self.manifest, self.nodes, source_sha=SHA, source_digest=DIGEST)

    def test_render_preserves_exact_snapshot_uri_and_rejects_changed_raw_identity(self):
        from src.parsers import parse_proxy_uri
        raw = "trojan://public%2Dfixture@1.1.1.1:443#Public"
        node = parse_proxy_uri(raw)
        files = self.results.render_result_files(manifest_for([node]), [node], source_sha=SHA, source_digest=DIGEST)
        self.assertEqual(base64.b64decode(files["subs/iran-tested_base64.txt"]).decode(), raw + "\n")
        node.raw_uri = "trojan://private-sentinel@8.8.8.8:443"
        with self.assertRaises(self.results.IranResultsError):
            self.results.render_result_files(manifest_for([node]), [node], source_sha=SHA, source_digest=DIGEST)


class TestOwnerAuthority(IranFixture):
    def test_publication_always_verifies_protected_code(self):
        with patch.object(self.results, "authorize_owner", return_value={"head": SHA}), \
             patch.object(self.results, "verify_tester_checkout", side_effect=self.results.IranResultsError("unreviewed code")), \
             patch.object(self.results, "gh_api", side_effect=AssertionError("no upload from unreviewed code")):
            with self.assertRaises(self.results.IranResultsError):
                self.results.publish_results(self.manifest, self.nodes, source_sha=SHA, source_digest=DIGEST,
                    captured_head=SHA, attest_access_reviewed=True)

    def test_command_output_is_bounded_and_timeout_kills_only_owned_process(self):
        from unittest.mock import MagicMock
        proc = MagicMock()
        proc.stdout = io.BytesIO(b"x" * 20)
        proc.poll.return_value = None
        with patch.object(self.results.subprocess, "Popen", return_value=proc), \
             self.assertRaises(self.results.IranResultsError):
            self.results._command(["gh", "api"], max_bytes=4)
        proc.kill.assert_called_once()
        proc.wait.assert_called()
        proc = MagicMock()
        proc.stdout = io.BytesIO(b"{}")
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("gh", .01), 0]
        with patch.object(self.results.subprocess, "Popen", return_value=proc), self.assertRaises(self.results.IranResultsError):
            self.results._command(["gh", "api"], timeout=.01)
        proc.kill.assert_called_once()
        self.assertEqual(proc.wait.call_count, 2)

    def test_wrong_auth_repo_protection_and_visible_admin_fail_closed(self):
        edits = [lambda r: r["user"].update(login="other"), lambda r: r["user"].update(id=1),
                 lambda r: r["repos/mani-moh/navaroute"].update(id=1),
                 lambda r: r["repos/mani-moh/navaroute"]["owner"].update(id=1),
                 lambda r: r["repos/mani-moh/navaroute"].update(private=True),
                 lambda r: r["repos/mani-moh/navaroute"]["permissions"].update(admin=False),
                 lambda r: r["repos/mani-moh/navaroute/branches/iran-results/protection"]["enforce_admins"].update(enabled=True),
                 lambda r: r["repos/mani-moh/navaroute/branches/iran-results/protection"]["required_pull_request_reviews"].update(required_approving_review_count=0),
                 lambda r: r["repos/mani-moh/navaroute/branches/iran-results/protection"]["required_pull_request_reviews"].update(require_last_push_approval=False),
                 lambda r: r["repos/mani-moh/navaroute/branches/iran-results/protection"]["required_pull_request_reviews"]["bypass_pull_request_allowances"].update(apps=[{"id": 1}]),
                 lambda r: r["repos/mani-moh/navaroute/actions/permissions/workflow"].update(can_approve_pull_request_reviews=True),
                 lambda r: r["repos/mani-moh/navaroute/collaborators?affiliation=all&per_page=100&page=1"].append({"login": "bot", "id": 1, "type": "Bot", "permissions": {"admin": True}}),
                 lambda r: r["repos/mani-moh/navaroute/keys?per_page=100&page=1"].append({"read_only": False}),
                 lambda r: r["repos/mani-moh/navaroute/actions/secrets?per_page=100"].update(total_count=2)]
        for edit in edits:
            responses = owner_responses()
            edit(responses)
            with self.subTest(edit=edit), patch.object(self.results, "gh_api", side_effect=lambda path, **kw: responses[path]):
                with self.assertRaises(self.results.IranResultsError):
                    self.results.authorize_owner(attest_access_reviewed=True)
        with patch.object(self.results, "gh_api", side_effect=self.results.IranResultsError("authentication unavailable")), \
             self.assertRaises(self.results.IranResultsError):
            self.results.authorize_owner(attest_access_reviewed=True)

    def test_good_owner_and_attestation_are_not_app_enumeration(self):
        responses = owner_responses()
        with patch.object(self.results, "gh_api", side_effect=lambda path, **kw: responses[path]):
            self.assertEqual(self.results.authorize_owner(attest_access_reviewed=True)["head"], SHA)

    def test_gh_uses_explicit_host_and_bounded_sanitized_errors(self):
        calls = []
        from unittest.mock import MagicMock
        def popen(argv, **kw):
            calls.append((argv, kw))
            proc = MagicMock()
            proc.stdout = io.BytesIO(b'{"login":"mani-moh"}')
            proc.wait.return_value = 0
            proc.poll.return_value = 0
            return proc

        with patch.dict(os.environ, {"GH_HOST": "evil.example", "GH_REPO": "evil/repo", "NVIDIA_API_KEY": "private-sentinel"}), \
             patch("src.iran_results.subprocess.Popen", side_effect=popen):
            self.assertEqual(self.results.gh_api("user"), {"login": "mani-moh"})
        argv, kw = calls[0]
        self.assertIn("--hostname", argv)
        self.assertIn("github.com", argv)
        self.assertNotIn("GH_HOST", kw["env"])
        self.assertNotIn("GH_REPO", kw["env"])
        self.assertNotIn("NVIDIA_API_KEY", kw["env"])


class TestApiTransport(IranFixture):
    def test_gh_api_rejects_non_canonical_endpoints(self):
        for path in ("", "repos/other/repo", "repos/mani-moh/navaroute/issues", "user bad", "repos/mani-moh/navaroute/../secret"):
            with self.subTest(path=path), self.assertRaises(self.results.IranResultsError):
                self.results.gh_api(path)

    def test_gh_api_rejects_non_json_and_non_object_responses(self):
        for raw in (b"not json", b"42", b'"just a string"'):
            with self.subTest(raw=raw), patch.object(self.results, "_command", return_value=raw), \
                 self.assertRaises(self.results.IranResultsError):
                self.results.gh_api("user")

    def test_pages_rejects_non_list_and_oversized_inventories(self):
        with patch.object(self.results, "gh_api", return_value={"not": "a list"}), \
             self.assertRaises(self.results.IranResultsError):
            self.results._pages("collaborators?affiliation=all")
        with patch.object(self.results, "gh_api", return_value=[{}] * 101), \
             self.assertRaises(self.results.IranResultsError):
            self.results._pages("collaborators?affiliation=all")
        with patch.object(self.results, "gh_api", return_value=[{}] * 100), \
             self.assertRaises(self.results.IranResultsError):
            self.results._pages("collaborators?affiliation=all")

    def test_content_rejects_non_file_and_bad_base64(self):
        for data in ({"type": "dir", "encoding": "base64", "content": "aGVsbG8="},
                     {"type": "file", "encoding": "utf-8", "content": "aGVsbG8="},
                     {"type": "file", "encoding": "base64", "content": "!!!not-base64!!!"}):
            with self.subTest(data=data), patch.object(self.results, "gh_api", return_value=data), \
                 self.assertRaises(self.results.IranResultsError):
                self.results._content("subs/all.txt", SHA)

    def test_command_failure_branches_are_bounded_errors(self):
        from unittest.mock import MagicMock
        def popen(argv, **kw):
            proc = MagicMock()
            proc.stdout = io.BytesIO(b"{}")
            proc.wait.return_value = 1
            proc.poll.return_value = 0
            return proc
        with patch("src.iran_results.subprocess.Popen", side_effect=popen), \
             self.assertRaises(self.results.IranResultsError):
            self.results.gh_api("user")
        with patch("src.iran_results.subprocess.Popen", side_effect=OSError("synthetic spawn failure")), \
             self.assertRaises(self.results.IranResultsError):
            self.results.gh_api("user")


class TestDirectPublication(IranFixture):
    def setUp(self):
        super().setUp()
        source = patch.object(self.results, "load_official_snapshot", return_value=self.nodes)
        history = patch.object(self.results, "verify_source_commit")
        source.start()
        history.start()
        self.addCleanup(source.stop)
        self.addCleanup(history.stop)

    def test_uploader_reloads_exact_official_snapshot_not_supplied_uri_authority(self):
        other = [ProxyNode("trojan", "8.8.8.8", 443, "other-public")]
        with patch.object(self.results, "authorize_owner", return_value={"head": SHA}), \
             patch.object(self.results, "verify_tester_checkout"), patch.object(self.results, "load_official_snapshot", return_value=other), \
             patch.object(self.results, "gh_api", side_effect=AssertionError("unofficial subset must not upload")):
            with self.assertRaises(self.results.IranResultsError):
                self.results.publish_results(self.manifest, self.nodes, source_sha=SHA, source_digest=DIGEST,
                    captured_head=SHA, attest_access_reviewed=True)

    def test_nonforced_commit_exact_confirmation_public_noreply_and_idempotency(self):
        files = self.results.render_result_files(self.manifest, self.nodes, source_sha=SHA, source_digest=DIGEST)
        state = {"head": SHA, "files": {}, "commits": 0}
        requests = []
        commit_sha = "c" * 40
        tree_sha = "d" * 40

        def api(path, *, method="GET", payload=None):
            requests.append((path, method, payload))
            prefix = "repos/mani-moh/navaroute/git/"
            if path == prefix + "ref/heads/iran-results":
                return {"object": {"sha": state["head"]}}
            if path.startswith(prefix + "trees/"):
                return {"sha": tree_sha, "truncated": False, "tree": [{"path": p, "mode": "100644", "type": "blob", "sha": self.results.git_blob_sha(t.encode())} for p, t in state["files"].items()]}
            if path == prefix + "blobs":
                return {"sha": self.results.git_blob_sha(base64.b64decode(payload["content"]))}
            if path == prefix + "trees":
                state["files"] = files
                return {"sha": tree_sha}
            if path == prefix + "commits" and method == "POST":
                state["commits"] += 1
                self.assertEqual(payload["parents"], [SHA])
                self.assertEqual(payload["author"]["email"], "72442823+mani-moh@users.noreply.github.com")
                self.assertEqual(payload["committer"], payload["author"])
                return {"sha": commit_sha}
            if path.startswith(prefix + "commits/"):
                return {"tree": {"sha": tree_sha}}
            if path == prefix + "refs/heads/iran-results":
                self.assertIs(payload["force"], False)
                state["head"] = payload["sha"]
                return {"object": {"sha": state["head"]}}
            raise AssertionError(path)

        with patch.object(self.results, "authorize_owner", return_value={"head": SHA}), \
             patch.object(self.results, "verify_tester_checkout"), patch.object(self.results, "gh_api", side_effect=api):
            self.assertEqual(self.results.publish_results(self.manifest, self.nodes, source_sha=SHA, source_digest=DIGEST,
                captured_head=SHA, attest_access_reviewed=True), commit_sha)
            self.assertEqual(self.results.publish_results(self.manifest, self.nodes, source_sha=SHA, source_digest=DIGEST,
                captured_head=SHA, attest_access_reviewed=True), commit_sha)
        self.assertEqual(state["commits"], 1)

    def test_conflict_and_missing_confirmation_never_report_published(self):
        def api(path, **kw):
            if "/ref/" in path:
                return {"object": {"sha": "c" * 40}}
            if "/trees/" in path:
                return {"sha": "d" * 40, "truncated": False, "tree": []}
            raise AssertionError("mutation must not run")

        with patch.object(self.results, "authorize_owner", return_value={"head": "c" * 40}), \
             patch.object(self.results, "verify_tester_checkout"), \
             patch.object(self.results, "gh_api", side_effect=api), self.assertRaises(self.results.IranResultsError):
            self.results.publish_results(self.manifest, self.nodes, source_sha=SHA, source_digest=DIGEST,
                captured_head=SHA, attest_access_reviewed=True)

    def test_failed_nonforced_update_wrong_blob_and_missing_exact_confirmation(self):
        files = self.results.render_result_files(self.manifest, self.nodes, source_sha=SHA, source_digest=DIGEST)
        commit = "c" * 40
        tree = "d" * 40
        for failure in ("blob", "update", "confirmation"):
            state = {"head": SHA}
            def api(path, *, method="GET", payload=None):
                prefix = "repos/mani-moh/navaroute/git/"
                if "/ref/" in path:
                    return {"object": {"sha": state["head"]}}
                if "/trees/" in path:
                    available = files if commit in path else {}
                    if failure == "confirmation" and state["head"] == commit:
                        available = {}
                    return {"truncated": False, "tree": [{"path": p, "mode": "100644", "type": "blob", "sha": self.results.git_blob_sha(t.encode())} for p, t in available.items()]}
                if "/commits/" in path:
                    return {"tree": {"sha": tree}}
                if path == prefix + "blobs":
                    return {"sha": "e" * 40 if failure == "blob" else self.results.git_blob_sha(base64.b64decode(payload["content"]))}
                if path == prefix + "trees":
                    return {"sha": tree}
                if path == prefix + "commits":
                    return {"sha": commit}
                if "/refs/" in path:
                    self.assertIs(payload["force"], False)
                    if failure == "update":
                        raise self.results.IranResultsError("nonforced update rejected")
                    state["head"] = commit
                    return {"object": {"sha": commit}}
                raise AssertionError(path)
            with self.subTest(failure=failure), patch.object(self.results, "authorize_owner", return_value={"head": SHA}), \
                 patch.object(self.results, "verify_tester_checkout"), patch.object(self.results, "gh_api", side_effect=api):
                with self.assertRaises(self.results.IranResultsError):
                    self.results.publish_results(self.manifest, self.nodes, source_sha=SHA, source_digest=DIGEST,
                        captured_head=SHA, attest_access_reviewed=True)


class TestProtectedCheckout(IranFixture):
    def test_clean_protected_code_all_imports_and_no_master_fallback(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            contents = {"src/__init__.py": b"", "src/iran_results.py": b"# reviewed\n",
                        "scripts/test_iran_reachability.py": b"# reviewed\n", "scripts/check_from_iran.ps1": b"# reviewed\n",
                        ".github/CODEOWNERS": b"* @mani-moh\n"}
            rows = []
            for path, content in contents.items():
                file = root / path
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(content)
                digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
                rows.append({"path": path, "mode": "100644", "type": "blob", "sha": digest})
            scenario = {"dirty": False, "branch": "HEAD", "status": "ahead", "modified": False}
            def command(argv, **kw):
                if "--show-toplevel" in argv:
                    return str(root).encode()
                if "--abbrev-ref" in argv:
                    return scenario["branch"].encode()
                if "rev-parse" in argv:
                    return SHA.encode()
                if "status" in argv:
                    return b" M src/iran_results.py" if scenario["dirty"] else b""
                if "ls-tree" in argv:
                    return "\n".join(f"100644 blob {row['sha']}\t{row['path']}" for row in rows).encode()
                raise AssertionError(argv)
            def api(path, **kw):
                if "/compare/" in path:
                    return {"status": scenario["status"]}
                if "/trees/" in path:
                    remote = copy.deepcopy(rows)
                    if scenario["modified"]:
                        remote[1]["sha"] = "f" * 40
                    return {"truncated": False, "tree": remote}
                raise AssertionError(path)
            modules = {"src": SimpleNamespace(__file__=str(root / "src/__init__.py")),
                       "src.iran_results": SimpleNamespace(__file__=str(root / "src/iran_results.py"))}
            with patch.object(self.results, "_command", side_effect=command), patch.object(self.results, "gh_api", side_effect=api), \
                 patch.object(self.results.sys, "modules", modules):
                wrapper = root / "scripts/check_from_iran.ps1"
                for newline in (b"\n", b"\r\n"):
                    wrapper.write_bytes(contents["scripts/check_from_iran.ps1"].replace(b"\n", newline))
                    with self.subTest(wrapper_newline=newline):
                        self.results.verify_tester_checkout(root, SHA, "c" * 40)
                wrapper.write_bytes(b"# unreviewed\r\n")
                with self.assertRaises(self.results.IranResultsError):
                    self.results.verify_tester_checkout(root, SHA, "c" * 40)
                wrapper.write_bytes(contents["scripts/check_from_iran.ps1"])
                python_source = root / "src/iran_results.py"
                python_source.write_bytes(contents["src/iran_results.py"].replace(b"\n", b"\r\n"))
                with self.assertRaises(self.results.IranResultsError):
                    self.results.verify_tester_checkout(root, SHA, "c" * 40)
                python_source.write_bytes(contents["src/iran_results.py"])
                for key, value in (("dirty", True), ("branch", "master"), ("status", "diverged"), ("modified", True)):
                    old = scenario[key]
                    scenario[key] = value
                    with self.subTest(key=key), self.assertRaises(self.results.IranResultsError):
                        self.results.verify_tester_checkout(root, SHA, "c" * 40)
                    scenario[key] = old
                modules["src.unreviewed"] = SimpleNamespace(__file__=str(root / "src/untracked.py"))
                with self.assertRaises(self.results.IranResultsError):
                    self.results.verify_tester_checkout(root, SHA, "c" * 40)


if __name__ == "__main__":
    unittest.main()
