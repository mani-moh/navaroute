from __future__ import annotations

import re
from typing import Optional

from src.models import ProxyNode

FRAGMENT_FM_JSON = (
    '{"tcp":[{"type":"fragment","settings":{"packets":"tlshello","length":"10-20","delay":"10-20"}},'
    '{"type":"fragment","settings":{"packets":"1-1","length":"1-3","delay":"5"}}]}'
)

FAST_AND_FURIOUS_CIPHERS = (
    "TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:"
    "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256"
)

IPV4_REGEX = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")


def _is_ip_address(addr: str) -> bool:
    clean = addr.strip("[]")
    if ":" in clean:
        return True
    return bool(IPV4_REGEX.match(clean))


def _extract_target_domain(node: ProxyNode) -> Optional[str]:
    """Extract a usable SNI / host domain from node parameters or address."""
    sni = (node.params.get("sni") or "").strip()
    if sni and not _is_ip_address(sni):
        return sni

    host = (node.params.get("host") or "").strip()
    if host and not _is_ip_address(host):
        return host

    addr = node.address.strip("[]").strip()
    if addr and not _is_ip_address(addr) and "." in addr:
        return addr

    return None


def generate_pattn_variants(
    nodes: list[ProxyNode], max_source_nodes: int = 250
) -> list[ProxyNode]:
    """Generate specialized PattN bypass variants (F&F, IPv6-OOB, SNI-Spoof)

    from eligible TLS-enabled base nodes.
    """
    eligible_nodes: list[ProxyNode] = []

    for node in nodes:
        # Avoid creating variants of existing PattN variants
        if "pattn_variant" in node.tags or "[PattN" in node.remark:
            continue

        # Protocols must be vless, trojan, or vmess
        if node.scheme not in ("vless", "trojan", "vmess"):
            continue

        # Must have a valid target SNI or host domain
        target_domain = _extract_target_domain(node)
        if not target_domain:
            continue

        # Check for TLS support (port 443, tls/reality, or explicit sni)
        is_tls = (
            node.port == 443
            or node.params.get("security") in ("tls", "reality")
            or bool(node.params.get("sni"))
        )
        if not is_tls:
            continue

        eligible_nodes.append(node)

    # Sort eligible base nodes by priority:
    # 1. Verified upstream first
    # 2. Passed healthcheck first
    # 3. CDN/WS/gRPC fronted first
    # 4. Lowest latency first
    def priority_key(n: ProxyNode) -> tuple[int, int, int, float]:
        p_ver = 0 if n.is_verified_source else 1
        p_health = 0 if n.passed_healthcheck else 1
        p_cdn = 0 if n.is_cdn_fronted() else 1
        p_lat = n.latency_ms if n.latency_ms is not None else 9999.0
        return (p_ver, p_health, p_cdn, p_lat)

    eligible_nodes.sort(key=priority_key)
    selected_base_nodes = eligible_nodes[:max_source_nodes]

    variants: list[ProxyNode] = []

    for base in selected_base_nodes:
        target_domain = _extract_target_domain(base)
        if not target_domain:
            continue

        base_remark = base.remark.strip() or f"{base.scheme.upper()}-{base.country_code}"

        # Clean base params: remove reality-specific keys when converting to standard TLS fronting
        clean_params = dict(base.params)
        clean_params.pop("pbk", None)
        clean_params.pop("sid", None)
        clean_params.pop("spx", None)
        clean_params.pop("flow", None)

        common_tags = set(base.tags) | {"pattn", "pattn_variant"}

        # -------------------------------------------------------------
        # 1. F&F Variant (Fast & Furious Fragment)
        # -------------------------------------------------------------
        fnf_params = dict(clean_params)
        fnf_params["sni"] = target_domain
        fnf_params["host"] = target_domain
        fnf_params["security"] = "tls"
        fnf_params["fp"] = "unsafe"
        fnf_params["cs"] = FAST_AND_FURIOUS_CIPHERS
        fnf_params["fm"] = FRAGMENT_FM_JSON

        fnf_remark = f"{base_remark} [PattN F&F]"
        fnf_node = ProxyNode(
            scheme=base.scheme,
            address="188.114.98.0",
            port=443,
            credential=base.credential,
            params=fnf_params,
            raw_uri="",
            remark=fnf_remark,
            country_code=base.country_code,
            source_ids=set(base.source_ids),
            tags=common_tags,
            is_verified_source=base.is_verified_source,
            is_private=base.is_private,
        )
        fnf_node.raw_uri = fnf_node.to_uri(include_formatted_remark=False)
        variants.append(fnf_node)

        # -------------------------------------------------------------
        # 2. IPv6-OOB Variant (Out-Of-Band IPv6 Dial)
        # -------------------------------------------------------------
        ipv6_params = dict(clean_params)
        ipv6_params["sni"] = target_domain
        ipv6_params["host"] = target_domain
        ipv6_params["security"] = "tls"
        ipv6_params["dialMode"] = "oob.0"
        ipv6_params["fp"] = "hellochrome_120"

        ipv6_remark = f"{base_remark} [PattN IPv6-OOB]"
        ipv6_node = ProxyNode(
            scheme=base.scheme,
            address="[2a06:98c1:3120::7]",
            port=443,
            credential=base.credential,
            params=ipv6_params,
            raw_uri="",
            remark=ipv6_remark,
            country_code=base.country_code,
            source_ids=set(base.source_ids),
            tags=common_tags,
            is_verified_source=base.is_verified_source,
            is_private=base.is_private,
        )
        ipv6_node.raw_uri = ipv6_node.to_uri(include_formatted_remark=False)
        variants.append(ipv6_node)

        # -------------------------------------------------------------
        # 3. SNI-Spoof Variant (Local Port / Fronting)
        # -------------------------------------------------------------
        spoof_params = dict(clean_params)
        spoof_params["sni"] = target_domain
        spoof_params["host"] = target_domain
        spoof_params["security"] = "tls"
        spoof_params["fp"] = "chrome"

        spoof_remark = f"{base_remark} [PattN SNI-Spoof]"
        spoof_node = ProxyNode(
            scheme=base.scheme,
            address="127.0.0.1",
            port=40443,
            credential=base.credential,
            params=spoof_params,
            raw_uri="",
            remark=spoof_remark,
            country_code=base.country_code,
            source_ids=set(base.source_ids),
            tags=common_tags,
            is_verified_source=base.is_verified_source,
            is_private=base.is_private,
        )
        spoof_node.raw_uri = spoof_node.to_uri(include_formatted_remark=False)
        variants.append(spoof_node)

    return variants
