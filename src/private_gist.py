from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from src.dedup import deduplicate_nodes
from src.fetcher import fetch_all_sources, load_sources
from src.models import ProxyNode
from src.parsers import parse_proxy_uri
from src.transform_pattn import generate_pattn_variants


def _format_private_file(title: str, nodes: list[ProxyNode]) -> tuple[str, str]:
    """Returns (plain_content_with_header, base64_content)."""
    b64_title = base64.b64encode(title.encode("utf-8")).decode("utf-8")
    headers = [
        f"#profile-title: base64:{b64_title}",
        "#profile-update-interval: 6",
        "#subscription-userinfo: upload=0; download=0; total=10737418240000000; expire=4102444800",
        "",
    ]
    uris = [n.to_uri(include_formatted_remark=True) for n in nodes]
    plain_content = "\n".join(headers + uris) + ("\n" if uris else "")
    uris_joined = "\n".join(uris)
    b64_content = (
        base64.b64encode(uris_joined.encode("utf-8")).decode("utf-8") if uris else ""
    )
    return plain_content, b64_content


def publish_private_subs(
    all_nodes: list[ProxyNode],
    private_dir: str = "private_subs",
    gist_token: Optional[str] = None,
    gist_id: Optional[str] = None,
) -> dict[str, str]:
    """Generates git-ignored private subscription files with user's private BPB configs

    placed at the top. Optionally uploads them to a GitHub Secret Gist.
    """
    out_dir = Path(private_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}

    # Separate private nodes from public nodes
    private_nodes = [n for n in all_nodes if n.is_private]
    public_nodes = [n for n in all_nodes if not n.is_private]

    # Verified public nodes
    healthcheck_ran = any(n.passed_healthcheck for n in public_nodes)
    if healthcheck_ran:
        verified_public = [n for n in public_nodes if n.passed_healthcheck]
    else:
        verified_public = [n for n in public_nodes if n.is_verified_source]

    verified_public.sort(
        key=lambda n: (
            not n.passed_healthcheck,
            not n.is_verified_source,
            n.latency_ms if n.latency_ms is not None else 9999.0,
        )
    )

    # 1. bpb_only (only private BPB nodes)
    bpb_plain, bpb_b64 = _format_private_file("PattN Private BPB Only", private_nodes)
    p_bpb = out_dir / "bpb_only.txt"
    p_bpb_b64 = out_dir / "bpb_only_base64.txt"
    p_bpb.write_text(bpb_plain, encoding="utf-8")
    p_bpb_b64.write_text(bpb_b64, encoding="utf-8")
    results["bpb_only"] = str(p_bpb)
    results["bpb_only_base64"] = str(p_bpb_b64)

    # 2. private_verified (private BPB nodes at top + all verified public nodes)
    pv_nodes = private_nodes + verified_public
    pv_plain, pv_b64 = _format_private_file("PattN Private + Verified", pv_nodes)
    p_pv = out_dir / "private_verified.txt"
    p_pv_b64 = out_dir / "private_verified_base64.txt"
    p_pv.write_text(pv_plain, encoding="utf-8")
    p_pv_b64.write_text(pv_b64, encoding="utf-8")
    results["private_verified"] = str(p_pv)
    results["private_verified_base64"] = str(p_pv_b64)

    # 3. private_all (private BPB nodes at top + all public nodes)
    pa_nodes = private_nodes + public_nodes
    pa_plain, pa_b64 = _format_private_file("PattN Private + All Public", pa_nodes)
    p_pa = out_dir / "private_all.txt"
    p_pa_b64 = out_dir / "private_all_base64.txt"
    p_pa.write_text(pa_plain, encoding="utf-8")
    p_pa_b64.write_text(pa_b64, encoding="utf-8")
    results["private_all"] = str(p_pa)
    results["private_all_base64"] = str(p_pa_b64)

    # Optional GitHub Secret Gist upload
    token = gist_token or os.environ.get("GIST_TOKEN")
    target_gist_id = gist_id or os.environ.get("GIST_ID")

    if token:
        files_payload = {
            "bpb_only.txt": {"content": bpb_plain},
            "private_verified.txt": {"content": pv_plain},
            "private_verified_base64.txt": {"content": pv_b64},
        }

        api_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PattN-Subscription-Pipeline",
            "Content-Type": "application/json",
        }

        try:
            if target_gist_id:
                # Update existing Gist
                url = f"https://api.github.com/gists/{target_gist_id}"
                req_data = json.dumps({"files": files_payload}).encode("utf-8")
                req = urllib.request.Request(
                    url, data=req_data, headers=api_headers, method="PATCH"
                )
            else:
                # Create new secret Gist
                url = "https://api.github.com/gists"
                req_data = json.dumps(
                    {
                        "description": "PattN Private Subscriptions (Confidential)",
                        "public": False,
                        "files": files_payload,
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    url, data=req_data, headers=api_headers, method="POST"
                )

            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_bytes = resp.read()
                data = json.loads(resp_bytes.decode("utf-8"))
                created_id = data.get("id")
                results["gist_id"] = created_id or ""
                results["gist_url"] = data.get("html_url", "")
                files_info = data.get("files", {})
                if "bpb_only.txt" in files_info:
                    results["gist_raw_bpb_only"] = files_info["bpb_only.txt"].get(
                        "raw_url", ""
                    )
                if "private_verified.txt" in files_info:
                    results["gist_raw_private_verified"] = files_info[
                        "private_verified.txt"
                    ].get("raw_url", "")
                if "private_verified_base64.txt" in files_info:
                    results["gist_raw_private_verified_base64"] = files_info[
                        "private_verified_base64.txt"
                    ].get("raw_url", "")

        except Exception as exc:
            results["gist_error"] = str(exc)

    return results


def load_subscription_file(
    file_path: str | Path,
    passed_healthcheck: bool = False,
) -> list[ProxyNode]:
    """Parse an existing subscription file from disk back into ProxyNode instances."""
    p = Path(file_path)
    if not p.exists():
        return []

    nodes: list[ProxyNode] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        node = parse_proxy_uri(line)
        if not node:
            continue
        node.is_private = False
        if passed_healthcheck:
            node.passed_healthcheck = True
        if "[✅]" in (node.remark or line):
            node.is_verified_source = True
        src_match = re.search(r"\|\s*src:([^\s|]+)", node.remark or line)
        if src_match:
            node.source_ids.add(src_match.group(1))
        lat_match = re.search(r"\|\s*([0-9]+(?:\.[0-9]+)?)ms", node.remark or line)
        if lat_match:
            try:
                node.latency_ms = float(lat_match.group(1))
            except ValueError:
                pass
        cc_match = re.search(r"\[([A-Z]{2})\]", node.remark or line)
        if cc_match:
            node.country_code = cc_match.group(1)
        nodes.append(node)
    return nodes


def load_public_subs_from_disk(subs_dir: str | Path = "subs") -> list[ProxyNode]:
    """Loads public proxy nodes from disk without re-fetching remote feeds.

    Prioritizes verified nodes from verified.txt (with passed_healthcheck=True)
    and merges any additional nodes from all.txt.
    """
    dir_path = Path(subs_dir)
    verified_file = dir_path / "verified.txt"
    all_file = dir_path / "all.txt"

    verified_nodes = load_subscription_file(verified_file, passed_healthcheck=True)
    all_nodes = load_subscription_file(all_file, passed_healthcheck=False)

    if not verified_nodes and not all_nodes:
        return []
    if not all_nodes:
        return verified_nodes
    if not verified_nodes:
        return all_nodes

    seen_keys = {
        (n.scheme, n.address, n.port, n.credential) for n in verified_nodes
    }
    merged = list(verified_nodes)
    for n in all_nodes:
        key = (n.scheme, n.address, n.port, n.credential)
        if key not in seen_keys:
            seen_keys.add(key)
            merged.append(n)
    return merged


def run_private_sync(
    sources_path: str = "sources.json",
    subs_dir: str = "subs",
    private_dir: str = "private_subs",
    gist_token: Optional[str] = None,
    gist_id: Optional[str] = None,
    max_workers: int = 16,
    timeout: int = 20,
    pattn_variants: bool = True,
    safe_print_fn: Any = print,
) -> dict[str, str]:
    """Lightweight sync flow: loads ONLY private sources, reads already-generated

    subs/ from disk, and publishes to private_subs/ and Secret Gist.
    Does NOT touch public subs/, README.md, or STATUS.md.
    """
    safe_print_fn("=" * 65)
    safe_print_fn("🔒 PattN Private Subscriptions Fast Sync")
    safe_print_fn("=" * 65)

    # 1. Load ONLY private sources
    sources = [
        s for s in load_sources(sources_path=sources_path, include_private=True)
        if s.is_private
    ]
    safe_print_fn(f"[1/3] Loaded {len(sources)} private sources")

    # 2. Concurrently fetch private sources
    safe_print_fn(f"[2/3] Fetching private feeds (workers={max_workers}, timeout={timeout}s)...")
    raw_nodes, json_configs, stats = fetch_all_sources(
        sources=sources,
        max_workers=max_workers,
        timeout=timeout,
    )
    deduped_nodes = deduplicate_nodes(raw_nodes)
    for n in deduped_nodes:
        n.is_private = True

    if pattn_variants:
        safe_print_fn("      Synthesizing PattN bypass variants for private nodes...")
        variants = generate_pattn_variants(deduped_nodes, max_source_nodes=250)
        for v in variants:
            v.is_private = True
        private_nodes = deduplicate_nodes(deduped_nodes + variants)
    else:
        private_nodes = deduped_nodes
    for n in private_nodes:
        n.is_private = True

    safe_print_fn(f"      Got {len(private_nodes)} private nodes")

    # 3. Read already-generated public nodes from disk
    public_nodes = load_public_subs_from_disk(subs_dir=subs_dir)
    verified_count = sum(1 for n in public_nodes if n.passed_healthcheck)
    safe_print_fn(
        f"[3/3] Loaded {len(public_nodes)} public nodes from '{subs_dir}/' "
        f"({verified_count} verified healthy)"
    )

    all_nodes = private_nodes + public_nodes

    # 4. Publish private subscriptions to private_subs/ & update Secret Gist
    safe_print_fn(f"[*] Publishing private subscriptions to '{private_dir}/'...")
    priv_results = publish_private_subs(
        all_nodes=all_nodes,
        private_dir=private_dir,
        gist_token=gist_token,
        gist_id=gist_id,
    )
    safe_print_fn(
        f"    Private subs generated: "
        f"{priv_results.get('bpb_only')} | {priv_results.get('private_verified')}"
    )
    if gid := priv_results.get("gist_id"):
        safe_print_fn(f"    Secret Gist updated: {priv_results.get('gist_url', gid)}")

    return priv_results


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="pattn-private-gist",
        description="PattN Fast Private Subscriptions & Secret Gist Sync",
    )
    parser.add_argument(
        "--sources",
        default="sources.json",
        help="Path to sources JSON (default: sources.json)",
    )
    parser.add_argument(
        "--subs-dir",
        default="subs",
        help="Directory with already generated public subs (default: subs)",
    )
    parser.add_argument(
        "--private-dir",
        default="private_subs",
        help="Output directory for private subscriptions (default: private_subs)",
    )
    parser.add_argument(
        "--gist-token",
        default=None,
        help="GitHub PAT for Secret Gist (optional, falls back to GIST_TOKEN env var)",
    )
    parser.add_argument(
        "--gist-id",
        default=None,
        help="Existing Secret Gist ID (optional, falls back to GIST_ID env var)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=16,
        help="Worker concurrency for private source fetching (default: 16)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP request timeout in seconds (default: 20)",
    )
    parser.add_argument(
        "--pattn-variants",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Generate PattN bypass variants for private nodes (default: enabled)",
    )

    args = parser.parse_args(argv)
    run_private_sync(
        sources_path=args.sources,
        subs_dir=args.subs_dir,
        private_dir=args.private_dir,
        gist_token=args.gist_token,
        gist_id=args.gist_id,
        max_workers=args.max_workers,
        timeout=args.timeout,
        pattn_variants=args.pattn_variants,
    )
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

