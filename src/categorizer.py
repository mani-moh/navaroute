from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import shutil
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from src.models import ProxyNode, SourceStats, health_observation_time
from src.parsers import KNOWN_ISO_CODES, parse_proxy_uri
from src.project import DEFAULT_BRANCH, PROJECT_NAME, detect_repo_slug
from src.storage import atomic_write_text

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
MAX_PLAIN_BYTES = 64 * 1024 * 1024
MAX_ENCODED_BYTES = 100 * 1024 * 1024
MAX_GENERATION_BYTES = 512 * 1024 * 1024
STATS_START = "<!-- NAVAROUTE:STATS:START -->"
STATS_END = "<!-- NAVAROUTE:STATS:END -->"
PROTOCOLS = ("vless", "vmess", "trojan", "ss", "hysteria2", "tuic")
CORE_FEEDS = ("all", "reachable", "verified", "top100", "lite500")
CATEGORIES = ("supplied-parameters", "pattn-special", "iran-optimized", "reality",
              "cdn-ws-grpc", "confighub", "whitelist")
PROTECTED_DIRECTORIES = {"data", "iran-results", "iran-tested", "iran-verification", "local_subs"}


def _snapshot_time(value: str | None) -> tuple[str, datetime]:
    if value is None:
        value = datetime.now(timezone.utc).isoformat()
    try:
        if not isinstance(value, str) or "T" not in value:
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() != timedelta(0):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Snapshot time must be a UTC ISO timestamp") from None
    return value, parsed


def _plain_path(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Publisher paths must not traverse links")
    if path.exists() and not (path.is_file() or path.is_dir()):
        raise ValueError("Invalid publisher path")
    if path.is_file() and path.stat().st_nlink > 1:
        raise ValueError("Publisher files must not be hardlinked")


def _owned_feed(key: str) -> bool:
    if key == "categories/serverless-fragment.json":
        return True
    if not key.endswith(".txt"):
        return False
    stem = key.removesuffix(".txt").removesuffix("_base64")
    return (stem in CORE_FEEDS or stem in {f"categories/{name}" for name in CATEGORIES}
            or stem in {f"protocols/{name}" for name in PROTOCOLS}
            or stem.startswith("countries/") and stem.removeprefix("countries/").upper() in KNOWN_ISO_CODES
            and re.fullmatch(r"countries/[a-z]{2}", stem) is not None)


def _target(key: str, out: Path, docs: bool) -> Path:
    if key.startswith("feed/") and _owned_feed(key[5:]):
        target = out / key[5:]
    elif docs and key in ("doc/README.md", "doc/STATUS.md"):
        target = REPOSITORY_ROOT / key[4:]
    else:
        raise ValueError("Unowned publisher recovery target")
    _plain_path(target)
    if target.exists() and not target.is_file():
        raise ValueError("Publisher target is not a file")
    return target


def _cleanup_transaction(transaction: Path) -> None:
    # The recovery validator admits only this publisher's exact files and directories.
    for path in sorted(transaction.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    transaction.rmdir()


def _recover(transaction: Path, out: Path, docs: bool) -> None:
    if not transaction.exists():
        return
    _plain_path(transaction)
    if transaction.is_dir() and not any(transaction.iterdir()):
        transaction.rmdir()
        return
    manifest_path = transaction / "manifest.json"
    _plain_path(manifest_path)
    try:
        with manifest_path.open("rb") as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError
        manifest = json.loads(raw)
        if (not isinstance(manifest, dict) or set(manifest) != {"state", "files"}
                or manifest["state"] not in ("prepared", "committed") or not isinstance(manifest["files"], dict)):
            raise ValueError
        files = manifest["files"]
        expected = {manifest_path}
        total = 0
        for key, existed in files.items():
            _target(key, out, docs)
            if type(existed) is not bool:
                raise ValueError
            for role in ("new", "old"):
                path = transaction / role / key
                expected.add(path)
                _plain_path(path)
                if path.exists():
                    if not path.is_file() or path.stat().st_size > MAX_ENCODED_BYTES:
                        raise ValueError
                    total += path.stat().st_size
            if manifest["state"] == "prepared" and existed and not (transaction / "old" / key).is_file():
                raise ValueError
        if total > 2 * MAX_GENERATION_BYTES:
            raise ValueError
        allowed_dirs = {p for file in expected for p in file.parents if p.is_relative_to(transaction)}
        for path in transaction.rglob("*"):
            _plain_path(path)
            if path not in expected and path not in allowed_dirs:
                raise ValueError
    except (OSError, ValueError, TypeError):
        raise ValueError("Invalid publisher recovery generation") from None
    if manifest["state"] == "prepared":
        for key, existed in files.items():
            target = _target(key, out, docs)
            if existed:
                temporary = transaction / "new" / key
                temporary.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(transaction / "old" / key, temporary)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary.replace(target)
            else:
                target.unlink(missing_ok=True)
    _cleanup_transaction(transaction)


def _project(rendered: dict[str, str], out: Path, docs: bool, transaction: Path) -> None:
    old_countries = []
    countries = out / "countries"
    _plain_path(countries)
    if countries.exists():
        old_countries = [f"feed/countries/{p.name}" for p in countries.glob("*.txt")
                         if _owned_feed(f"countries/{p.name}")]
    keys = sorted(set(rendered) | set(old_countries))
    targets = {key: _target(key, out, docs) for key in keys}
    previous_bytes = sum(p.stat().st_size for p in targets.values() if p.exists())
    if previous_bytes > MAX_GENERATION_BYTES or any(p.exists() and p.stat().st_size > MAX_ENCODED_BYTES for p in targets.values()):
        raise ValueError("Previous publisher generation exceeds recovery limits")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{out.name}.stage-", dir=out.parent) as directory:
        stage = Path(directory)
        for key, content in rendered.items():
            path = stage / "new" / key
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, content)
        files = {key: target.exists() for key, target in targets.items()}
        for key, existed in files.items():
            if existed:
                backup = stage / "old" / key
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(targets[key], backup)
        atomic_write_text(stage / "manifest.json", json.dumps({"state": "prepared", "files": files}))
        stage.replace(transaction)
    try:
        for key, target in targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            if key in rendered:
                (transaction / "new" / key).replace(target)
            else:
                target.unlink(missing_ok=True)
        atomic_write_text(transaction / "manifest.json", json.dumps({"state": "committed", "files": files}))
    except BaseException:
        try:
            _recover(transaction, out, docs)
        except (OSError, ValueError):
            raise OSError("Publication interrupted; previous generation recovery required") from None
        raise
    _recover(transaction, out, docs)


def _bounded_lines(lines, max_bytes: int | None = None) -> str:
    parts = []
    total = 0
    for line in lines:
        total += len(line.encode("utf-8")) + 1
        if total > min(MAX_PLAIN_BYTES, MAX_PLAIN_BYTES if max_bytes is None else max_bytes):
            raise ValueError("Rendered document exceeds plain byte limit")
        parts.append(line)
    return "\n".join(parts) + "\n"


def _status(stats: list[SourceStats], count: int, reachable: int, ran: bool, timestamp: str, max_bytes: int) -> str:
    lines = [f"# {PROJECT_NAME} Upstream Sources Status", "", f"Snapshot time: {timestamp}",
             f"Socket healthcheck ran: {str(ran).lower()}; reachable: {reachable} (not credential verified).", "",
             "| Metric | Count |", "|---|---:|", f"| Configured Sources | {len(stats)} |",
             f"| Fetch-successful Sources | {sum(s.success for s in stats)} |",
             f"| Sources Yielding Usable Nodes | {sum(s.success and s.raw_count > 0 for s in stats)} |",
             f"| Raw Extracted Nodes | {sum(s.raw_count for s in stats)} |", f"| Accepted Public Nodes | {count} |",
             f"| Socket Reachable Nodes | {reachable} |", "", "| Source | Fetch | Raw | Unique | Error |", "|---|---|---:|---:|---|"]
    def rows():
        yield from lines
        for index, stat in enumerate(stats, 1):
            source = stat.source_id if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", stat.source_id) else f"Source {index}"
            yield f"| {source} | {'OK' if stat.success else 'FAIL'} | {stat.raw_count} | {stat.unique_count} | {'Fetch failed' if stat.error else '-'} |"
    return _bounded_lines(rows(), max_bytes)


def publish_subscriptions(
    nodes: list[ProxyNode], json_configs: list[dict], stats: list[SourceStats],
    output_dir: str = "subs", update_docs: bool = True, *,
    healthcheck_ran: bool = False, snapshot_time: str | None = None,
) -> dict[str, int]:
    """Publish a bounded complete generation; true healthcheck_ran attests this run's observations.

    Counts are output-relative. External outputs never project repository documents.
    Retired executable JSON is empty; this publisher never writes owner evidence.
    """
    timestamp, context = _snapshot_time(snapshot_time)
    if type(healthcheck_ran) is not bool or not isinstance(nodes, list) or not nodes:
        raise ValueError("Publication requires accepted public nodes and explicit checking state")
    if not isinstance(json_configs, list) or any(not isinstance(config, dict) for config in json_configs):
        raise ValueError("Invalid retired JSON input")
    if not isinstance(stats, list) or any(not isinstance(s, SourceStats) or type(s.success) is not bool
            or not isinstance(s.source_id, str) or any(type(v) is not int or v < 0 for v in (s.raw_count, s.unique_count, s.verified_count)) for s in stats):
        raise ValueError("Invalid source statistics")
    if stats and not any(s.success for s in stats):
        raise ValueError("All source fetches failed; previous generation retained")
    out = Path(output_dir).absolute()
    _plain_path(out)
    if any(part.lower() in PROTECTED_DIRECTORIES for part in out.parts):
        raise ValueError("Protected publisher output directory")
    out = out.resolve()
    if out == REPOSITORY_ROOT:
        raise ValueError("Protected publisher output directory")
    repository_output = out.is_relative_to(REPOSITORY_ROOT)
    docs = bool(update_docs and repository_output)
    # shortcut: one publisher per output directory; add locking before allowing concurrent writers.
    transaction = out.parent / f".{out.name}.navaroute-publish"
    _recover(transaction, out, repository_output)
    readme_path = REPOSITORY_ROOT / "README.md"
    readme = ""
    if docs:
        _target("doc/README.md", out, docs)
        with readme_path.open("rb") as stream:
            content = stream.read(MAX_PLAIN_BYTES + 1)
        if len(content) > MAX_PLAIN_BYTES:
            raise ValueError("README exceeds plain byte limit")
        readme = content.decode("utf-8")
        if (readme.count(STATS_START) != 1 or readme.count(STATS_END) != 1
                or readme.index(STATS_START) >= readme.index(STATS_END)):
            raise ValueError("README requires unique ordered NavaRoute stats markers")
    uris: dict[str, str] = {}
    fingerprints: dict[int, str] = {}
    public: list[ProxyNode] = []
    reachable_ids: set[str] = set()
    uri_bytes = 0
    for node in nodes:
        if not isinstance(node, ProxyNode) or not isinstance(node.country_code, str) or (node.country_code != "XX" and node.country_code.upper() not in KNOWN_ISO_CODES):
            raise ValueError("Invalid public node metadata")
        observed = health_observation_time(node)
        latency = node.latency_ms
        current_socket = (healthcheck_ran and node.healthcheck_kind == "socket"
                          and node.scheme not in ("hysteria2", "tuic", "wireguard")
                          and observed is not None and observed <= context)
        socket_pass = (current_socket and node.passed_healthcheck is True
                       and type(latency) in (int, float) and math.isfinite(latency) and latency >= 0)
        # Do not serialize legacy/invalid socket claims as successful current observations.
        if (node.healthcheck_kind == "socket" and (not current_socket or node.passed_healthcheck and not socket_pass)
                or observed is None):
            node = replace(node, passed_healthcheck=False, latency_ms=None, healthcheck_kind="untested", healthchecked_at=None)
        key = node.semantic_fingerprint()
        uri = node.to_uri(include_formatted_remark=True)
        parsed = parse_proxy_uri(uri)
        if parsed is None or parsed.semantic_fingerprint() != key:
            raise ValueError("Public connection does not round-trip losslessly")
        uri_bytes += len(uri.encode("utf-8")) + 1
        if uri_bytes > MAX_PLAIN_BYTES:
            raise ValueError("Public connections exceed plain byte limit")
        if key in uris:
            raise ValueError("Publisher requires deduplicated public nodes")
        uris[key] = uri
        fingerprints[id(node)] = key
        public.append(node)
        if socket_pass:
            reachable_ids.add(key)

    def ranking(node):
        key = fingerprints[id(node)]
        return (key not in reachable_ids, node.latency_ms if key in reachable_ids else math.inf, key)

    public.sort(key=ranking)
    reachable = [n for n in public if fingerprints[id(n)] in reachable_ids]
    buckets = {proto: [n for n in public if n.scheme == proto] for proto in PROTOCOLS}
    lite = []
    for index in range(max(map(len, buckets.values()), default=0)):
        for bucket in buckets.values():
            if index < len(bucket) and len(lite) < 500:
                lite.append(bucket[index])
        if len(lite) == 500:
            break
    special = [n for n in public if n.has_special_parameters()]
    groups = [("all", "All Accepted Public Configs", public), ("reachable", "Socket Reachable", reachable),
              ("verified", "Deprecated: reachability—not credential verified", reachable),
              ("top100", "Top 100 Socket Reachable — Latency First", reachable[:100]), ("lite500", "Lite 500 Protocol Balanced", lite),
              ("categories/supplied-parameters", "Supplied Special Parameters — Not Generated or Bypass Tested", special),
              ("categories/pattn-special", "Legacy Mirror of Supplied Parameters", special),
              ("categories/iran-optimized", "Iran-Related Upstream Claims — Not Iran Tested", [n for n in public if n.is_iran_related()]),
              ("categories/reality", "Supplied Reality Configs", [n for n in public if n.is_reality()]),
              ("categories/cdn-ws-grpc", "Supplied Web Transports", [n for n in public if n.has_web_transport()]),
              ("categories/confighub", "ConfigsHUB Source Claims", [n for n in public if "confighub" in n.tags]),
              ("categories/whitelist", "Whitelist Source Claims — Not Bypass Tested", [n for n in public if "whitelist" in n.tags])]
    groups.extend((f"protocols/{p}", p.upper(), buckets[p]) for p in PROTOCOLS)
    countries: dict[str, list[ProxyNode]] = {}
    for node in public:
        if node.country_code.upper() != "XX":
            countries.setdefault(node.country_code.lower(), []).append(node)
    groups.extend((f"countries/{cc}", f"{cc.upper()} Upstream Country Claims", group)
                  for cc, group in sorted(countries.items()) if len(group) >= 5)
    counts = {"categories/serverless-fragment.json": 0}
    total_bytes = 2
    plans = []
    for name, title, group in groups:
        header = f"#profile-title: base64:{base64.b64encode(f'{PROJECT_NAME} {title}'.encode()).decode()}\n#profile-update-interval: 6\n# snapshot-time: {timestamp}\n\n"
        selected = [uris[fingerprints[id(n)]] for n in group]
        size = sum(len(uri.encode("utf-8")) for uri in selected) + max(0, len(selected) - 1)
        plain_size = len(header.encode("utf-8")) + size + bool(selected)
        encoded_size = 4 * ((size + 2) // 3)
        total_bytes += plain_size + encoded_size
        if plain_size > MAX_PLAIN_BYTES or encoded_size > MAX_ENCODED_BYTES or total_bytes > MAX_GENERATION_BYTES:
            raise ValueError("Rendered subscriptions exceed generation byte limits")
        plans.append((name, header, selected))
        counts[f"{name}.txt"] = counts[f"{name}_base64.txt"] = len(group)
    rendered = {"feed/categories/serverless-fragment.json": "[]"}
    for name, header, selected in plans:
        body = "\n".join(selected)
        rendered[f"feed/{name}.txt"] = header + body + ("\n" if selected else "")
        rendered[f"feed/{name}_base64.txt"] = base64.b64encode(body.encode("utf-8")).decode("ascii")
    if docs:
        root_relative = quote(out.relative_to(REPOSITORY_ROOT).as_posix(), safe="/")
        repo_url = f"https://raw.githubusercontent.com/{detect_repo_slug()}/{DEFAULT_BRANCH}/{root_relative}"
        digest = hashlib.sha256(rendered["feed/all.txt"].encode("utf-8")).hexdigest()
        facts = [f"## {PROJECT_NAME} Generated Snapshot", "", f"Snapshot time: {timestamp}", f"all.txt SHA-256: `{digest}`", "",
                 f"Accepted public nodes: {len(public)}; socket healthcheck ran: {str(healthcheck_ran).lower()}; socket reachable: {len(reachable)}.",
                 "Reachability—not credential verified. Upstream labels and country/category labels are claims, not successful probes.",
                 "`verified.txt` is a deprecated reachable mirror. `categories/pattn-special.txt` is only a legacy raw-path mirror of supplied parameters.",
                 "Retired executable `categories/serverless-fragment.json` is `[]`.", "", "| Feed | Nodes |", "|---|---:|"]
        facts.extend(f"| [{key}]({repo_url}/{key}) | {count} |" for key, count in sorted(counts.items()))
        start = readme.index(STATS_START) + len(STATS_START)
        end = readme.index(STATS_END)
        retained_size = len(content) - len(readme[start:end].encode("utf-8"))
        generated = _bounded_lines(facts, min(MAX_PLAIN_BYTES, MAX_GENERATION_BYTES - total_bytes) - retained_size - 1)
        total_bytes += retained_size + 1 + len(generated.encode("utf-8"))
        rendered["doc/README.md"] = readme[:start] + "\n" + generated + readme[end:]
        rendered["doc/STATUS.md"] = _status(stats, len(public), len(reachable), healthcheck_ran, timestamp, MAX_GENERATION_BYTES - total_bytes)
    _project(rendered, out, docs, transaction)
    return counts
