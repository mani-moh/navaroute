from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from src.models import ProxyNode


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
