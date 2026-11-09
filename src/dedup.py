from __future__ import annotations

from src.models import ProxyNode


def deduplicate_nodes(nodes: list[ProxyNode]) -> list[ProxyNode]:
    """Deduplicate a list of ProxyNodes by their semantic fingerprint.

    Merges source_ids, tags, is_verified_source, passed_healthcheck,
    is_private, minimum latency_ms, and selects the best country_code.
    """
    groups: dict[str, list[ProxyNode]] = {}
    for node in nodes:
        fp = node.semantic_fingerprint()
        if fp not in groups:
            groups[fp] = []
        groups[fp].append(node)

    deduped: list[ProxyNode] = []
    for group in groups.values():
        primary = group[0]

        merged_source_ids: set[str] = set()
        merged_tags: set[str] = set()
        is_verified = False
        passed_health = False
        is_priv = False
        latencies: list[float] = []
        country_codes: list[str] = []

        # Merge parameters across duplicates
        merged_params = dict(primary.params)

        for n in group:
            merged_source_ids.update(n.source_ids)
            merged_tags.update(n.tags)
            if n.is_verified_source:
                is_verified = True
            if n.passed_healthcheck:
                passed_health = True
            if n.is_private:
                is_priv = True
            if n.latency_ms is not None:
                latencies.append(n.latency_ms)
            if n.country_code and n.country_code != "XX":
                country_codes.append(n.country_code)
            for k, v in n.params.items():
                if k not in merged_params:
                    merged_params[k] = v

        best_country = country_codes[0] if country_codes else primary.country_code
        min_latency = min(latencies) if latencies else None

        # Prefer raw_uri and remark from a verified source if available
        best_node = primary
        for n in group:
            if n.is_verified_source and not best_node.is_verified_source:
                best_node = n
                break

        merged_node = ProxyNode(
            scheme=primary.scheme,
            address=primary.address,
            port=primary.port,
            credential=primary.credential,
            params=merged_params,
            raw_uri=best_node.raw_uri or primary.raw_uri,
            remark=best_node.remark or primary.remark,
            country_code=best_country,
            source_ids=merged_source_ids,
            tags=merged_tags,
            is_verified_source=is_verified,
            passed_healthcheck=passed_health,
            latency_ms=min_latency,
            is_private=is_priv,
        )
        deduped.append(merged_node)

    return deduped
