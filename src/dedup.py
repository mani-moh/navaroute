from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

from src.models import ProxyNode, health_observation_time


def deduplicate_nodes(nodes: list[ProxyNode]) -> list[ProxyNode]:
    """Merge metadata only for complete equal connections, independent of source order."""
    groups: dict[str, list[ProxyNode]] = {}
    for node in nodes:
        groups.setdefault(node.semantic_fingerprint(), []).append(node)
    deduped = []
    for fingerprint in sorted(groups):
        group = groups[fingerprint]
        representative = min(group, key=lambda n: (
            not n.is_verified_source, n.remark, sorted(n.source_ids), n.raw_uri,
            n.address, n.country_code, sorted(n.tags),
            n.scheme, json.dumps(n.params, sort_keys=True, ensure_ascii=False, allow_nan=False),
        ))
        countries = {n.country_code.upper() for n in group if n.country_code and n.country_code.upper() != "XX"}
        observations = [n for n in group if health_observation_time(n) is not None]
        observation = max(observations, key=lambda n: (
            health_observation_time(n) or datetime.min.replace(tzinfo=timezone.utc),
            n.healthcheck_kind == "xray", not n.passed_healthcheck,
            n.latency_ms if n.latency_ms is not None else -1,
            n.healthchecked_at,
        )) if observations else None
        deduped.append(replace(
            representative, params=deepcopy(representative.params),
            source_ids=set().union(*(n.source_ids for n in group)),
            tags=set().union(*(n.tags for n in group)),
            is_verified_source=any(n.is_verified_source for n in group),
            country_code=next(iter(countries)) if len(countries) == 1 else "XX",
            passed_healthcheck=observation.passed_healthcheck if observation else False,
            latency_ms=observation.latency_ms if observation else None,
            healthcheck_kind=observation.healthcheck_kind if observation else "untested",
            healthchecked_at=observation.healthchecked_at if observation else None,
        ))
    return deduped
