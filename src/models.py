from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Optional


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
    is_private: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceConfig":
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            url=data["url"],
            verified_upstream=data.get("verified_upstream", False),
            format=data.get("format", "auto"),
            tags=list(data.get("tags", [])),
            enabled=data.get("enabled", True),
            is_private=data.get("is_private", False),
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
            "is_private": self.is_private,
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
    is_private: bool = False


@dataclass
class ProxyNode:
    scheme: str  # vless, vmess, trojan, ss, hysteria2, tuic, wireguard
    address: str
    port: int
    credential: str  # UUID, password, or method:password
    params: dict[str, str] = field(default_factory=dict)
    raw_uri: str = ""
    remark: str = ""
    country_code: str = "XX"
    source_ids: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)
    is_verified_source: bool = False
    passed_healthcheck: bool = False
    latency_ms: Optional[float] = None
    is_private: bool = False

    def semantic_fingerprint(self) -> str:
        """SHA-256 hex digest of canonical tuple:

        (scheme, address.lower().strip("[]"), port, credential,
         transport_type, security, sni.lower(), host.lower(),
         path, service_name, pbk, sid, flow, fm, dial_mode, cs)
        """
        canonical_tuple = (
            self.scheme.lower().strip(),
            self.address.lower().strip().strip("[]"),
            int(self.port),
            self.credential.strip(),
            self.params.get("type", self.params.get("net", self.params.get("network", ""))).lower().strip(),
            self.params.get("security", "").lower().strip(),
            self.params.get("sni", "").lower().strip(),
            self.params.get("host", "").lower().strip(),
            self.params.get("path", "").strip(),
            self.params.get("serviceName", self.params.get("service_name", self.params.get("servicename", ""))).strip(),
            self.params.get("pbk", "").strip(),
            self.params.get("sid", "").strip(),
            self.params.get("flow", "").lower().strip(),
            self.params.get("fm", "").strip(),
            self.params.get("dialMode", self.params.get("dial_mode", "")).strip(),
            self.params.get("cs", "").strip(),
        )
        canonical_str = json.dumps(canonical_tuple, ensure_ascii=False)
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    def is_pattn_special(self) -> bool:
        """True if params contains fm, dialMode, cs, fp == "unsafe", or "pattn" in tags."""
        if any(k in self.params for k in ("fm", "dialMode", "dial_mode", "cs")):
            return True
        if self.params.get("fp", "").lower() == "unsafe":
            return True
        if "pattn" in self.tags:
            return True
        return False

    def is_reality(self) -> bool:
        """True if params.get("security") == "reality" or bool(params.get("pbk"))."""
        return self.params.get("security", "").lower() == "reality" or bool(self.params.get("pbk"))

    def is_cdn_fronted(self) -> bool:
        """True if transport_type in ("ws", "grpc", "httpupgrade", "xhttp", "splithttp")

        or any tag in ("cdn", "whitedns").
        """
        transport = self.params.get("type", self.params.get("net", self.params.get("network", ""))).lower()
        if transport in ("ws", "grpc", "httpupgrade", "xhttp", "splithttp"):
            return True
        return any(t in self.tags for t in ("cdn", "whitedns"))

    def is_iran_optimized(self) -> bool:
        """True if "iran" or "whitelist" in tags or is_pattn_special()."""
        return bool(self.tags.intersection({"iran", "whitelist"})) or self.is_pattn_special()

    def format_remark(self) -> str:
        """Generate formatted remark like: [✅] [🇩🇪 DE] VLESS-Reality | src:alirzza9 | 65ms"""
        parts = []
        if self.is_verified_source or self.passed_healthcheck:
            parts.append("[✅]")

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

        # Preserve PattN bypass variant labels in formatted remark
        if "[PattN F&F]" in self.remark or (self.address == "188.114.98.0" and "pattn_variant" in self.tags):
            parts.append("[PattN F&F]")
        elif "[PattN IPv6-OOB]" in self.remark or ("oob.0" in self.params.get("dialMode", "") and "pattn_variant" in self.tags):
            parts.append("[PattN IPv6-OOB]")
        elif "[PattN SNI-Spoof]" in self.remark or (self.address == "127.0.0.1" and "pattn_variant" in self.tags):
            parts.append("[PattN SNI-Spoof]")

        src_part = ""
        if self.source_ids:
            first_src = sorted(self.source_ids)[0]
            src_part = f"| src:{first_src}"

        latency_part = ""
        if self.latency_ms is not None:
            latency_part = f"| {int(round(self.latency_ms))}ms"

        main_prefix = " ".join(parts)
        tail = " ".join(p for p in (src_part, latency_part) if p)
        if tail:
            return f"{main_prefix} {tail}"
        return main_prefix

    def to_uri(self, include_formatted_remark: bool = True) -> str:
        """Rebuilds or updates the #remark on raw_uri (or updates ps inside vmess:// base64 JSON!)

        with clean formatted remark.
        """
        target_remark = self.format_remark() if include_formatted_remark else self.remark
        if not target_remark:
            target_remark = self.remark or self.address

        # Case 1: VMess base64 JSON
        if self.raw_uri and self.raw_uri.startswith("vmess://") and "@" not in self.raw_uri:
            raw_b64 = self.raw_uri[len("vmess://"):].split("#")[0].strip()
            raw_b64 += "=" * ((4 - len(raw_b64) % 4) % 4)
            try:
                decoded = base64.b64decode(raw_b64).decode("utf-8", errors="replace")
                json_obj = json.loads(decoded)
                json_obj["ps"] = target_remark
                new_b64 = base64.b64encode(json.dumps(json_obj, ensure_ascii=False).encode("utf-8")).decode("utf-8")
                return f"vmess://{new_b64}"
            except Exception:
                pass

        # Case 2: raw_uri exists
        if self.raw_uri:
            base = self.raw_uri.split("#")[0]
            return f"{base}#{target_remark}"

        # Case 3: Reconstruct from fields
        if self.scheme == "vmess":
            v_dict = {
                "v": "2",
                "ps": target_remark,
                "add": self.address,
                "port": self.port,
                "id": self.credential,
                "aid": self.params.get("aid", "0"),
                "scy": self.params.get("scy", "auto"),
                "net": self.params.get("type", self.params.get("net", "tcp")),
                "type": self.params.get("headerType", "none"),
                "host": self.params.get("host", ""),
                "path": self.params.get("path", ""),
                "tls": self.params.get("security", ""),
                "sni": self.params.get("sni", ""),
                "alpn": self.params.get("alpn", ""),
                "fp": self.params.get("fp", ""),
            }
            for k, val in self.params.items():
                if k not in v_dict and val:
                    v_dict[k] = val
            b64 = base64.b64encode(json.dumps(v_dict, ensure_ascii=False).encode("utf-8")).decode("utf-8")
            return f"vmess://{b64}"

        query = urllib.parse.urlencode(self.params)
        if self.scheme == "ss" and ":" in self.credential:
            cred_b64 = base64.urlsafe_b64encode(self.credential.encode("utf-8")).decode("utf-8").rstrip("=")
            cred = f"{cred_b64}@"
        else:
            cred = f"{self.credential}@" if self.credential else ""
        addr = f"[{self.address}]" if ":" in self.address and not self.address.startswith("[") else self.address
        q_str = f"?{query}" if query else ""
        return f"{self.scheme}://{cred}{addr}:{self.port}{q_str}#{target_remark}"
