from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

FINGERPRINT_VERSION = 1
MAX_URI_BYTES = 16 * 1024
MAX_PARAMETER_DEPTH = 64
PARAM_ALIASES = {"net": "type", "network": "type", "service_name": "serviceName",
                 "servicename": "serviceName", "dial_mode": "dialMode", "header_type": "headerType"}
VMESS_FIELDS = {"net": "type", "tls": "security", "type": "headerType"}


class ConnectionLimitError(ValueError):
    """Resource rejection, distinct from an ordinary malformed entry."""


def bounded_uri(uri: str) -> str:
    if len(uri) > MAX_URI_BYTES or len(uri.encode("utf-8")) > MAX_URI_BYTES:
        raise ConnectionLimitError(f"connection exceeds {MAX_URI_BYTES} UTF-8 bytes")
    return uri


def canonical_params(params: dict[str, Any], scheme: str = "") -> dict[str, Any]:
    pending = [(params, 0)]
    while pending:
        value, depth = pending.pop()
        if not isinstance(value, (dict, list)):
            continue
        if depth > MAX_PARAMETER_DEPTH:
            raise ConnectionLimitError(f"connection parameters exceed nesting depth {MAX_PARAMETER_DEPTH}")
        children = value.values() if isinstance(value, dict) else value
        pending.extend((child, depth + 1) for child in children)
    result: dict[str, Any] = {}
    for key, value in params.items():
        key = PARAM_ALIASES.get(key, key)
        if scheme == "vmess" and key == "tls":
            key = "security"
        if key in result:
            previous = result[key] if isinstance(result[key], list) else [result[key]]
            result[key] = previous + (value if isinstance(value, list) else [value])
        else:
            result[key] = value
    for key, value in result.items():
        if (scheme != "vmess" or key == "alpn") and isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
            if key == "alpn" and (not value[0] or "," in value[0]):
                if scheme == "vmess":
                    continue
                raise ValueError("singleton ALPN cannot be represented by URI query")
            result[key] = value[0]
    return result


def health_observation_time(node: ProxyNode) -> Optional[datetime]:
    if node.healthcheck_kind not in ("socket", "xray") or not isinstance(node.healthchecked_at, str):
        return None
    try:
        timestamp = datetime.fromisoformat(node.healthchecked_at.replace("Z", "+00:00"))
        return timestamp if timestamp.utcoffset() == timedelta(0) else None
    except ValueError:
        return None


def country_code_to_flag(code: str) -> str:
    """Convert a 2-letter ISO country code to Unicode flag emoji."""
    if not code or len(code) != 2 or not code.isalpha() or code.upper() == "XX":
        return "🌐"
    c1 = code[0].upper()
    c2 = code[1].upper()
    return chr(0x1F1E6 + ord(c1) - ord("A")) + chr(0x1F1E6 + ord(c2) - ord("A"))


def flag_to_country_code(flag: str) -> Optional[str]:
    """Extract a 2-letter ISO country code from regional indicator emoji."""
    if not flag:
        return None
    for i in range(len(flag) - 1):
        c1 = ord(flag[i])
        c2 = ord(flag[i + 1])
        if 0x1F1E6 <= c1 <= 0x1F1FF and 0x1F1E6 <= c2 <= 0x1F1FF:
            return chr(ord("A") + c1 - 0x1F1E6) + chr(ord("A") + c2 - 0x1F1E6)
    return None


@dataclass
class SourceConfig:
    id: str
    name: str
    url: str
    verified_upstream: bool = False
    format: str = "auto"
    tags: list[str] = field(default_factory=list)
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceConfig":
        if not isinstance(data, dict):
            raise ValueError("source must be an object")
        for key in ("id", "url"):
            if not isinstance(data.get(key), str) or not data[key] or data[key] != data[key].strip():
                raise ValueError(f"source {key} must be a nonempty string without surrounding whitespace")
        for key in ("name", "format"):
            if key in data and (not isinstance(data[key], str) or not data[key]):
                raise ValueError(f"source {key} must be a nonempty string")
        for key in ("enabled", "verified_upstream"):
            if key in data and not isinstance(data[key], bool):
                raise ValueError(f"source {key} must be boolean")
        tags = data.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag for tag in tags):
            raise ValueError("source tags must be a list of nonempty strings")
        if data["url"] != "data/discovered_ocr_configs.txt":
            try:
                url = urllib.parse.urlsplit(data["url"])
                if url.scheme not in ("http", "https") or not url.hostname or url.username is not None or url.password is not None:
                    raise ValueError("source URL must be unauthenticated HTTP(S)")
                if url.port is not None and not 1 <= url.port <= 65535:
                    raise ValueError("invalid source URL port")
            except ValueError as exc:
                raise ValueError("invalid source URL") from exc
        if any(c.isspace() or ord(c) < 32 for c in data["url"]):
            raise ValueError("source URL contains whitespace/control characters")
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            url=data["url"],
            verified_upstream=data.get("verified_upstream", False),
            format=data.get("format", "auto"),
            tags=list(data.get("tags", [])),
            enabled=data.get("enabled", True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "verified_upstream": self.verified_upstream,
            "format": self.format,
            "tags": self.tags,
            "enabled": self.enabled,
        }


@dataclass
class SourceStats:
    source_id: str
    name: str
    url: str
    success: bool = False
    status_code: Optional[int] = None
    elapsed_ms: float = 0.0
    raw_count: int = 0
    unique_count: int = 0
    verified_count: int = 0
    error: Optional[str] = None


@dataclass
class ProxyNode:
    scheme: str  # vless, vmess, trojan, ss, hysteria2, tuic, wireguard
    address: str
    port: int
    credential: str  # UUID, password, or method:password
    params: dict[str, Any] = field(default_factory=dict)
    raw_uri: str = ""
    remark: str = ""
    country_code: str = "XX"
    source_ids: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)
    is_verified_source: bool = False
    passed_healthcheck: bool = False
    latency_ms: Optional[float] = None
    healthcheck_kind: str = "untested"
    healthchecked_at: Optional[str] = None

    def __post_init__(self) -> None:
        self.params = canonical_params(self.params, self.scheme)

    def semantic_fingerprint(self) -> str:
        """Versioned identity of the complete connection, never its source/evidence labels."""
        params = canonical_params(self.params, self.scheme)
        for key in ("sni", "host"):
            if isinstance(params.get(key), str):
                params[key] = params[key].lower()
        canonical_tuple = (FINGERPRINT_VERSION, self.scheme.lower(), self.address.lower().strip("[]"),
                           self.port, self.credential, params)
        canonical_str = json.dumps(canonical_tuple, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    def has_special_parameters(self) -> bool:
        if any(k in self.params for k in ("fm", "dialMode", "dial_mode", "cs")):
            return True
        return self.params.get("fp") == "unsafe"

    def is_reality(self) -> bool:
        """True if params.get("security") == "reality" or bool(params.get("pbk"))."""
        return self.params.get("security") == "reality" or bool(self.params.get("pbk"))

    def has_web_transport(self) -> bool:
        transport = canonical_params(self.params, self.scheme).get("type", "")
        return isinstance(transport, str) and transport.lower() in ("ws", "grpc", "httpupgrade", "xhttp", "splithttp")

    def is_iran_related(self) -> bool:
        return self.country_code == "IR" or bool(self.tags.intersection({"iran", "whitelist"}))

    def format_remark(self) -> str:
        """Label source claims separately from timestamped socket/end-to-end observations."""
        parts = []
        if self.is_verified_source:
            parts.append("[source-claimed]")
        observed = health_observation_time(self) is not None
        if observed:
            kind = "socket" if self.healthcheck_kind == "socket" else "end-to-end"
            outcome = "passed" if self.passed_healthcheck else "failed"
            parts.append(f"[{kind}:{outcome}]")

        country = self.country_code.upper() if self.country_code else "XX"
        flag = country_code_to_flag(country)
        parts.append(f"[{flag} {country}]")

        scheme = self.scheme.upper()
        if scheme == "VMESS":
            scheme_label = "VMess"
        elif scheme == "VLESS":
            scheme_label = "VLESS"
        elif scheme == "TROJAN":
            scheme_label = "Trojan"
        elif scheme == "SS":
            scheme_label = "SS"
        elif scheme in ("HYSTERIA2", "HY2"):
            scheme_label = "Hysteria2"
        elif scheme == "TUIC":
            scheme_label = "TUIC"
        elif scheme in ("WIREGUARD", "WG"):
            scheme_label = "WireGuard"
        else:
            scheme_label = scheme.capitalize()

        if self.is_reality():
            proto_label = f"{scheme_label}-Reality"
        else:
            transport = self.params.get("type", self.params.get("net", self.params.get("network", "")))
            if transport:
                proto_label = f"{scheme_label}-{transport}"
            else:
                proto_label = scheme_label
        parts.append(proto_label)

        src_part = ""
        if self.source_ids:
            first_src = sorted(self.source_ids)[0]
            src_part = f"| src:{first_src}"

        latency_part = ""
        if observed and self.passed_healthcheck and self.latency_ms is not None:
            latency_part = f"| {int(round(self.latency_ms))}ms"

        main_prefix = " ".join(parts)
        tail = " ".join(p for p in (src_part, latency_part) if p)
        if tail:
            return f"{main_prefix} {tail}"
        return main_prefix

    def to_uri(self, include_formatted_remark: bool = True) -> str:
        """Serialize canonical fields. Reject shapes a scheme cannot represent losslessly."""
        target_remark = self.format_remark() if include_formatted_remark else self.remark
        params = canonical_params(self.params, self.scheme)
        if self.scheme == "vmess":
            scalar_fields = {"v", "type", "security", "headerType", "host", "path", "sni", "fp", "scy", "serviceName"}
            if any(key in scalar_fields and not isinstance(value, str) for key, value in params.items()):
                raise ValueError("VMess scalar setting cannot be represented")
            if "aid" in params and not (isinstance(params["aid"], str) and params["aid"].isascii() and params["aid"].isdigit()):
                raise ValueError("invalid VMess alterId")
            reverse = {canonical: original for original, canonical in VMESS_FIELDS.items()}
            v_dict = {}
            for key, value in params.items():
                output_key = reverse.get(key, key)
                if output_key in v_dict:
                    raise ValueError("conflicting VMess output settings")
                v_dict[output_key] = value
            if set(v_dict).intersection({"add", "port", "id", "ps"}):
                raise ValueError("VMess parameters conflict with canonical fields")
            v_dict.update(ps=target_remark, add=self.address, port=self.port, id=self.credential)
            b64 = base64.b64encode(json.dumps(v_dict, ensure_ascii=False, allow_nan=False).encode("utf-8")).decode("utf-8")
            return bounded_uri(f"vmess://{b64}")

        if self.scheme not in ("vless", "trojan", "ss", "hysteria2", "tuic", "wireguard"):
            raise ValueError("unsupported URI scheme")
        for value in params.values():
            if not isinstance(value, str) and not (isinstance(value, list) and value and all(isinstance(v, str) for v in value)):
                raise ValueError("parameter cannot be represented by URI query")
        query = urllib.parse.urlencode(params, doseq=True)
        if self.scheme == "ss":
            if ":" not in self.credential:
                raise ValueError("Shadowsocks requires method:password")
            method, _, password = self.credential.partition(":")
            if method.startswith("2022-"):
                cred = urllib.parse.quote(method, safe="") + ":" + urllib.parse.quote(password, safe="")
            else:
                cred = base64.urlsafe_b64encode(self.credential.encode("utf-8")).decode("utf-8").rstrip("=")
        elif self.scheme == "tuic":
            username, separator, password = self.credential.partition(":")
            if not separator or not username or not password:
                raise ValueError("TUIC requires username:password")
            cred = urllib.parse.quote(username, safe="") + ":" + urllib.parse.quote(password, safe="")
        else:
            cred = urllib.parse.quote(self.credential, safe="")
        addr = f"[{self.address}]" if ":" in self.address and not self.address.startswith("[") else self.address
        q_str = f"?{query}" if query else ""
        fragment = "#" + urllib.parse.quote(target_remark, safe="") if target_remark else ""
        return bounded_uri(f"{self.scheme}://{cred}@{addr}:{self.port}{q_str}{fragment}")
