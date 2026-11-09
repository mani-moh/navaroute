from __future__ import annotations

import base64
import html
import json
import re
import urllib.parse
from typing import Optional

from src.models import (
    ProxyNode,
    SourceConfig,
    country_code_to_flag,
    flag_to_country_code,
)

# Common country name mapping to 2-letter ISO codes
COUNTRY_NAMES_TO_ISO = {
    "GERMANY": "DE",
    "DEUTSCHLAND": "DE",
    "UNITED STATES": "US",
    "USA": "US",
    "AMERICA": "US",
    "NETHERLANDS": "NL",
    "HOLLAND": "NL",
    "IRAN": "IR",
    "FINLAND": "FI",
    "FRANCE": "FR",
    "UNITED KINGDOM": "GB",
    "GREAT BRITAIN": "GB",
    "UK": "GB",
    "ENGLAND": "GB",
    "TURKEY": "TR",
    "TURKIYE": "TR",
    "RUSSIA": "RU",
    "CANADA": "CA",
    "SINGAPORE": "SG",
    "JAPAN": "JP",
    "KOREA": "KR",
    "SOUTH KOREA": "KR",
    "SWITZERLAND": "CH",
    "SWEDEN": "SE",
    "AUSTRIA": "AT",
    "POLAND": "PL",
    "ITALY": "IT",
    "SPAIN": "ES",
    "AUSTRALIA": "AU",
    "UKRAINE": "UA",
    "NORWAY": "NO",
    "DENMARK": "DK",
    "BELGIUM": "BE",
    "BRAZIL": "BR",
    "INDIA": "IN",
    "HONG KONG": "HK",
    "TAIWAN": "TW",
    "UNITED ARAB EMIRATES": "AE",
    "UAE": "AE",
}

COMMON_STOP_WORDS = {
    "IN", "TO", "BY", "AT", "IS", "IT", "NO", "SO", "DO", "ME",
    "AS", "BE", "OR", "ON", "OF", "UP", "GO", "MY", "WE", "HE",
    "HI", "OK", "ID", "CF", "AM", "PM", "AN", "IF"
}

KNOWN_ISO_CODES = {
    "AD", "AE", "AF", "AG", "AI", "AL", "AM", "AO", "AQ", "AR", "AS", "AT", "AU", "AW", "AX", "AZ",
    "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BL", "BM", "BN", "BO", "BQ", "BR", "BS",
    "BT", "BV", "BW", "BY", "BZ", "CA", "CC", "CD", "CF", "CG", "CH", "CI", "CK", "CL", "CM", "CN",
    "CO", "CR", "CU", "CV", "CW", "CX", "CY", "CZ", "DE", "DJ", "DK", "DM", "DO", "DZ", "EC", "EE",
    "EG", "EH", "ER", "ES", "ET", "FI", "FJ", "FK", "FM", "FO", "FR", "GA", "GB", "GD", "GE", "GF",
    "GG", "GH", "GI", "GL", "GM", "GN", "GP", "GQ", "GR", "GS", "GT", "GU", "GW", "GY", "HK", "HM",
    "HN", "HR", "HT", "HU", "ID", "IE", "IL", "IM", "IN", "IO", "IQ", "IR", "IS", "IT", "JE", "JM",
    "JO", "JP", "KE", "KG", "KH", "KI", "KM", "KN", "KP", "KR", "KW", "KY", "KZ", "LA", "LB", "LC",
    "LI", "LK", "LR", "LS", "LT", "LU", "LV", "LY", "MA", "MC", "MD", "ME", "MF", "MG", "MH", "MK",
    "ML", "MM", "MN", "MO", "MP", "MQ", "MR", "MS", "MT", "MU", "MV", "MW", "MX", "MY", "MZ", "NA",
    "NC", "NE", "NF", "NG", "NI", "NL", "NO", "NP", "NR", "NU", "NZ", "OM", "PA", "PE", "PF", "PG",
    "PH", "PK", "PL", "PM", "PN", "PR", "PS", "PT", "PW", "PY", "QA", "RE", "RO", "RS", "RU", "RW",
    "SA", "SB", "SC", "SD", "SE", "SG", "SH", "SI", "SJ", "SK", "SL", "SM", "SN", "SO", "SR", "SS",
    "ST", "SV", "SX", "SY", "SZ", "TC", "TD", "TF", "TG", "TH", "TJ", "TK", "TL", "TM", "TN", "TO",
    "TR", "TT", "TV", "TW", "TZ", "UA", "UG", "UM", "US", "UY", "UZ", "VA", "VC", "VE", "VG", "VI",
    "VN", "VU", "WF", "WS", "YE", "YT", "ZA", "ZM", "ZW"
}

URI_REGEX = re.compile(r'((?:vless|vmess|trojan|ss|hy2|hysteria2|tuic|wg|wireguard)://[^\s<>"\'`]+)', re.IGNORECASE)


def detect_country(remark: str, host: str = "", sni: str = "") -> str:
    """Detect 2-letter ISO country code from remark flag emoji, text keywords, or host/sni TLD."""
    # 1. Unicode regional indicator flag emoji
    if flag_code := flag_to_country_code(remark):
        return flag_code.upper()

    # 2. Check full country names BEFORE 2-letter codes to avoid false positives (e.g. "Server in Germany")
    remark_upper = remark.upper()
    for name, code in COUNTRY_NAMES_TO_ISO.items():
        if re.search(r'\b' + re.escape(name) + r'\b', remark_upper):
            return code

    # 3. Check for bracketed/delimited 2-letter codes: [DE], (US), | NL |, - FR -, :TR:
    bracket_matches = re.findall(r'[\[\(\-_|:]([A-Za-z]{2})[\]\)\-_|:]', remark)
    for bm in bracket_matches:
        code_upper = bm.upper()
        if code_upper in KNOWN_ISO_CODES and code_upper not in COMMON_STOP_WORDS:
            return code_upper

    # Delimited by whitespace: require uppercase [A-Z]{2} in original remark and not a stop word
    ws_matches = re.findall(r'(?:\s|^)([A-Z]{2})(?:\s|$)', remark)
    for wm in ws_matches:
        if wm in KNOWN_ISO_CODES and wm not in COMMON_STOP_WORDS:
            return wm

    # 4. Check domain TLD from host / sni
    for target in (sni, host):
        if target:
            target_clean = target.lower().strip("[]").split(":")[0]
            if "." in target_clean:
                tld = target_clean.split(".")[-1].upper()
                if tld in KNOWN_ISO_CODES and tld not in COMMON_STOP_WORDS:
                    return tld

    return "XX"


def repair_base64_padding(s: str) -> str:
    """Ensure base64 string length is a multiple of 4."""
    s = s.strip()
    return s + "=" * ((4 - len(s) % 4) % 4)


def safe_b64decode(s: str) -> Optional[str]:
    """Safely decode base64 string handling padding and URL-safe variants."""
    try:
        s_padded = repair_base64_padding(s)
        try:
            return base64.b64decode(s_padded).decode("utf-8")
        except Exception:
            return base64.urlsafe_b64decode(s_padded).decode("utf-8")
    except Exception:
        return None


def parse_proxy_uri(uri: str, source: Optional[SourceConfig] = None) -> Optional[ProxyNode]:
    """Parse a single proxy URI into a ProxyNode instance."""
    uri = uri.strip().rstrip(".,;!?'\"")
    if "://" not in uri:
        return None

    raw_scheme, rest = uri.split("://", 1)
    scheme = raw_scheme.lower()

    # Normalize scheme
    if scheme == "hy2":
        scheme = "hysteria2"
    elif scheme == "wg":
        scheme = "wireguard"

    if scheme not in ("vless", "vmess", "trojan", "ss", "hysteria2", "tuic", "wireguard"):
        return None

    # Handle VMess Base64 JSON
    if scheme == "vmess" and "@" not in rest:
        return _parse_vmess_json(uri, rest, source)

    # Handle Shadowsocks
    if scheme == "ss":
        return _parse_shadowsocks(uri, rest, source)

    # General URI parsing for vless, trojan, hysteria2, tuic, wireguard, and query-style vmess
    return _parse_standard_uri(scheme, uri, rest, source)


def _parse_vmess_json(raw_uri: str, rest: str, source: Optional[SourceConfig]) -> Optional[ProxyNode]:
    """Parse vmess://<base64_json>."""
    b64_part = rest.split("#")[0].split("?")[0].strip()
    decoded = safe_b64decode(b64_part)
    if not decoded:
        return None

    try:
        data = json.loads(decoded)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    address = str(data.get("add", "")).strip()
    if not address:
        return None

    try:
        port = int(data.get("port", 443))
        if not (1 <= port <= 65535):
            return None
    except (ValueError, TypeError):
        return None

    credential = str(data.get("id", "")).strip()
    remark = str(data.get("ps", "")).strip()

    params: dict[str, str] = {}
    net = str(data.get("net", "tcp")).strip()
    params["type"] = net
    if sec := data.get("tls"):
        params["security"] = str(sec).strip()
    if host := data.get("host"):
        params["host"] = str(host).strip()
    if path := data.get("path"):
        params["path"] = str(path).strip()
    if sni := data.get("sni"):
        params["sni"] = str(sni).strip()
    if alpn := data.get("alpn"):
        params["alpn"] = str(alpn).strip()
    if fp := data.get("fp"):
        params["fp"] = str(fp).strip()
    if aid := data.get("aid"):
        params["aid"] = str(aid).strip()
    if scy := data.get("scy"):
        params["scy"] = str(scy).strip()
    if h_type := data.get("type"):
        if h_type != "none":
            params["headerType"] = str(h_type).strip()

    country = detect_country(remark, address, params.get("sni", ""))

    source_ids = {source.id} if source else set()
    tags = set(source.tags) if source else set()

    return ProxyNode(
        scheme="vmess",
        address=address,
        port=port,
        credential=credential,
        params=params,
        raw_uri=raw_uri,
        remark=remark,
        country_code=country,
        source_ids=source_ids,
        tags=tags,
        is_verified_source=source.verified_upstream if source else False,
        is_private=source.is_private if source else False,
    )


def _parse_shadowsocks(raw_uri: str, rest: str, source: Optional[SourceConfig]) -> Optional[ProxyNode]:
    """Parse Shadowsocks URI (SIP002 or legacy)."""
    remark = ""
    if "#" in rest:
        rest, fragment = rest.split("#", 1)
        remark = urllib.parse.unquote(fragment).strip()

    query = ""
    if "?" in rest:
        rest, query = rest.split("?", 1)

    credential = ""
    address = ""
    port = 8388

    # SIP002 format: ss://base64(method:password)@host:port
    if "@" in rest:
        userinfo, hostport = rest.rsplit("@", 1)
        decoded_userinfo = safe_b64decode(userinfo)
        credential = decoded_userinfo if decoded_userinfo else urllib.parse.unquote(userinfo)
    else:
        # Legacy format: ss://base64(method:password@host:port)
        decoded = safe_b64decode(rest)
        if not decoded or "@" not in decoded:
            return None
        credential, hostport = decoded.rsplit("@", 1)

    hostport = hostport.strip().rstrip("/")
    if "/" in hostport:
        hostport, _ = hostport.split("/", 1)

    if hostport.startswith("["):
        idx = hostport.find("]")
        if idx == -1:
            return None
        address = hostport[1:idx]
        port_str = hostport[idx + 1:].lstrip(":")
    elif ":" in hostport:
        address, port_str = hostport.rsplit(":", 1)
    else:
        address = hostport
        port_str = "8388"

    try:
        port = int(port_str)
        if not (1 <= port <= 65535):
            return None
    except ValueError:
        return None

    if not address:
        return None

    params: dict[str, str] = {}
    if query:
        for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True):
            params[k] = v

    country = detect_country(remark, address, params.get("sni", ""))
    source_ids = {source.id} if source else set()
    tags = set(source.tags) if source else set()

    return ProxyNode(
        scheme="ss",
        address=address,
        port=port,
        credential=credential,
        params=params,
        raw_uri=raw_uri,
        remark=remark,
        country_code=country,
        source_ids=source_ids,
        tags=tags,
        is_verified_source=source.verified_upstream if source else False,
        is_private=source.is_private if source else False,
    )


def _parse_standard_uri(
    scheme: str, raw_uri: str, rest: str, source: Optional[SourceConfig]
) -> Optional[ProxyNode]:
    """Parse standard proxy URI: <scheme>://<credential>@<host>:<port>[/path]?<query>#<remark>."""
    remark = ""
    if "#" in rest:
        rest, fragment = rest.split("#", 1)
        remark = urllib.parse.unquote(fragment).strip()

    query = ""
    if "?" in rest:
        rest, query = rest.split("?", 1)

    credential = ""
    if "@" in rest:
        credential, hostport = rest.rsplit("@", 1)
        credential = urllib.parse.unquote(credential)
    else:
        hostport = rest

    hostport = hostport.strip()
    path_from_uri = ""
    if "/" in hostport:
        hostport, path_part = hostport.split("/", 1)
        path_part = path_part.strip().rstrip("/")
        if path_part:
            path_from_uri = "/" + path_part

    if hostport.startswith("["):
        idx = hostport.find("]")
        if idx == -1:
            return None
        address = hostport[1:idx]
        port_str = hostport[idx + 1:].lstrip(":")
    elif ":" in hostport:
        address, port_str = hostport.rsplit(":", 1)
    else:
        address = hostport
        port_str = "443"

    try:
        port = int(port_str)
        if not (1 <= port <= 65535):
            return None
    except ValueError:
        return None

    if not address:
        return None

    params: dict[str, str] = {}
    if path_from_uri:
        params["path"] = path_from_uri

    if query:
        for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True):
            key = k.strip()
            val = v.strip()
            # Normalize parameter names
            if key == "net" and "type" not in params:
                params["type"] = val
            elif key == "network" and "type" not in params:
                params["type"] = val
            elif key in ("service_name", "servicename"):
                params["serviceName"] = val
            elif key == "dial_mode":
                params["dialMode"] = val
            elif key == "header_type":
                params["headerType"] = val
            else:
                params[key] = val

    # If type missing, check if net was set
    if "type" not in params and "net" in params:
        params["type"] = params["net"]

    country = detect_country(remark, address, params.get("sni", ""))
    source_ids = {source.id} if source else set()
    tags = set(source.tags) if source else set()

    return ProxyNode(
        scheme=scheme,
        address=address,
        port=port,
        credential=credential,
        params=params,
        raw_uri=raw_uri,
        remark=remark,
        country_code=country,
        source_ids=source_ids,
        tags=tags,
        is_verified_source=source.verified_upstream if source else False,
        is_private=source.is_private if source else False,
    )


def extract_nodes_from_content(
    content: str, source: SourceConfig
) -> tuple[list[ProxyNode], list[dict]]:
    """Extract proxy nodes and xray JSON configurations from raw fetched content."""
    content = content.strip()
    if not content:
        return [], []

    # 1. Handle Xray JSON configs
    is_json_format = source.format == "xray_json"
    looks_like_json = content.startswith(("{", "[")) and (
        '"outbounds"' in content or '"inbounds"' in content or '"routing"' in content
    )

    if is_json_format or looks_like_json:
        parsed_json = None
        try:
            parsed_json = json.loads(content)
        except Exception:
            pass  # Fall through to text parsing if JSON decoding fails

        if parsed_json is not None:
            json_configs = parsed_json if isinstance(parsed_json, list) else [parsed_json]
            try:
                extracted_nodes = _extract_nodes_from_xray_json(json_configs, source)
            except Exception:
                extracted_nodes = []
            return extracted_nodes, json_configs

    # 2. Handle HTML / Telegram Web
    if source.format in ("telegram_web", "html") or "<html" in content.lower() or "class=\"tgme_" in content:
        text = html.unescape(content)
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</?(?:div|p|tr|li|code|pre)[^>]*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
    else:
        text = content

    # 3. Check for Base64 subscription
    if source.format == "base64" or "://" not in text:
        decoded_sub = safe_b64decode("".join(text.split()))
        if decoded_sub and "://" in decoded_sub:
            text = decoded_sub

    # 4. Extract URIs
    extracted_nodes: list[ProxyNode] = []
    found_uris = URI_REGEX.findall(text)

    # Also try line-by-line base64 decoding for lines without ://
    if not found_uris:
        for line in text.splitlines():
            line = line.strip()
            if line and "://" not in line:
                decoded_line = safe_b64decode(line)
                if decoded_line and "://" in decoded_line:
                    found_uris.extend(URI_REGEX.findall(decoded_line))

    for raw_u in found_uris:
        node = parse_proxy_uri(raw_u, source=source)
        if node:
            extracted_nodes.append(node)

    return extracted_nodes, []


def _extract_nodes_from_xray_json(
    json_configs: list[dict], source: SourceConfig
) -> list[ProxyNode]:
    """Extract ProxyNode items from xray json outbound settings."""
    extracted = []
    for cfg in json_configs:
        if not isinstance(cfg, dict):
            continue
        outbounds = cfg.get("outbounds") or []
        for ob in outbounds:
            if not isinstance(ob, dict):
                continue
            try:
                protocol = (ob.get("protocol") or "").lower()
                settings = ob.get("settings") or {}
                stream = ob.get("streamSettings") or {}
                net = stream.get("network") or "tcp"
                sec = stream.get("security") or "none"
                tag = ob.get("tag") or ""

                # VLESS / VMess
                vnext = settings.get("vnext") or []
                for vn in vnext:
                    if not isinstance(vn, dict):
                        continue
                    addr = vn.get("address") or ""
                    try:
                        port = int(vn.get("port", 443))
                    except (ValueError, TypeError):
                        continue
                    users = vn.get("users") or []
                    user = users[0] if (users and isinstance(users[0], dict)) else {}
                    uid = str(user.get("id") or "")
                    flow = str(user.get("flow") or "")

                    params: dict[str, str] = {"type": net, "security": sec}
                    if flow:
                        params["flow"] = flow
                    if sec == "reality":
                        r_settings = stream.get("realitySettings") or {}
                        if r_settings.get("serverName"):
                            params["sni"] = r_settings["serverName"]
                        if r_settings.get("publicKey"):
                            params["pbk"] = r_settings["publicKey"]
                        if r_settings.get("shortId"):
                            params["sid"] = r_settings["shortId"]
                        if r_settings.get("spiderX"):
                            params["spx"] = r_settings["spiderX"]
                    elif sec == "tls":
                        tls_settings = stream.get("tlsSettings") or {}
                        if tls_settings.get("serverName"):
                            params["sni"] = tls_settings["serverName"]

                    if net == "ws":
                        ws_settings = stream.get("wsSettings") or {}
                        if ws_settings.get("path"):
                            params["path"] = ws_settings["path"]
                        headers = ws_settings.get("headers") or {}
                        if headers.get("Host"):
                            params["host"] = headers["Host"]
                    elif net == "grpc":
                        grpc_settings = stream.get("grpcSettings") or {}
                        if grpc_settings.get("serviceName"):
                            params["serviceName"] = grpc_settings["serviceName"]

                    if addr and uid:
                        node = ProxyNode(
                            scheme=protocol or "vless",
                            address=addr,
                            port=port,
                            credential=uid,
                            params=params,
                            remark=tag,
                            country_code=detect_country(tag, addr, params.get("sni", "")),
                            source_ids={source.id},
                            tags=set(source.tags),
                            is_verified_source=source.verified_upstream,
                            is_private=source.is_private,
                        )
                        extracted.append(node)

                # Trojan / Shadowsocks
                servers = settings.get("servers") or []
                for srv in servers:
                    if not isinstance(srv, dict):
                        continue
                    addr = srv.get("address") or ""
                    try:
                        port = int(srv.get("port", 443))
                    except (ValueError, TypeError):
                        continue
                    pwd = str(srv.get("password") or "")
                    method = str(srv.get("method") or "")
                    cred = f"{method}:{pwd}" if method else pwd
                    params = {"type": net, "security": sec}
                    if addr and cred:
                        node = ProxyNode(
                            scheme=protocol or "trojan",
                            address=addr,
                            port=port,
                            credential=cred,
                            params=params,
                            remark=tag,
                            country_code=detect_country(tag, addr, params.get("sni", "")),
                            source_ids={source.id},
                            tags=set(source.tags),
                            is_verified_source=source.verified_upstream,
                            is_private=source.is_private,
                        )
                        extracted.append(node)
            except Exception:
                continue

    return extracted
