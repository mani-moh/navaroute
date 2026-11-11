from __future__ import annotations

import base64
import ipaddress
import io
import json
import re
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Optional

from src import models
from src.models import PARAM_ALIASES, VMESS_FIELDS, ProxyNode, SourceConfig, flag_to_country_code

COUNTRY_NAMES_TO_ISO = {
    "GERMANY": "DE", "DEUTSCHLAND": "DE", "UNITED STATES": "US", "USA": "US",
    "NETHERLANDS": "NL", "HOLLAND": "NL", "IRAN": "IR", "FINLAND": "FI",
    "FRANCE": "FR", "UNITED KINGDOM": "GB", "GREAT BRITAIN": "GB", "UK": "GB",
    "ENGLAND": "GB", "TURKEY": "TR", "TURKIYE": "TR", "RUSSIA": "RU", "CANADA": "CA",
    "SINGAPORE": "SG", "JAPAN": "JP", "NORTH KOREA": "KP", "SOUTH KOREA": "KR",
    "SWITZERLAND": "CH", "SWEDEN": "SE", "AUSTRIA": "AT", "POLAND": "PL", "ITALY": "IT",
    "SPAIN": "ES", "AUSTRALIA": "AU", "UKRAINE": "UA", "NORWAY": "NO", "DENMARK": "DK",
    "BELGIUM": "BE", "BRAZIL": "BR", "INDIA": "IN", "HONG KONG": "HK", "TAIWAN": "TW",
    "UNITED ARAB EMIRATES": "AE", "UAE": "AE",
}
COMMON_STOP_WORDS = set("IN TO BY AT IS IT NO SO DO ME AS BE OR ON OF UP GO MY WE HE HI OK ID CF AM PM AN IF".split())
KNOWN_ISO_CODES = set("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM
JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP
MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY
QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO
TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())
URI_REGEX = re.compile(r'((?:vless|vmess|trojan|ss|hy2|hysteria2|tuic|wg|wireguard)://[^\s<>"\'`]+)', re.IGNORECASE)
SUPPORTED_SCHEMES = {"vless", "vmess", "trojan", "ss", "hysteria2", "tuic", "wireguard"}
MAX_CONTENT_NODES = 100000


def _check_candidate_count(count: int) -> None:
    if count > MAX_CONTENT_NODES:
        raise ValueError(f"source exceeds {MAX_CONTENT_NODES} connection/config candidates")


def detect_country(remark: str, host: str = "", sni: str = "") -> str:
    """Return an unambiguous source claim; host/SNI never establish geography."""
    formatted = re.findall(r"\[(?:🌐|[\U0001f1e6-\U0001f1ff]{2}) ([A-Z]{2})\]", remark)
    if formatted:
        claims = set(formatted)
        return next(iter(claims)) if len(claims) == 1 and claims <= KNOWN_ISO_CODES | {"XX"} else "XX"
    claims = set()
    for pair in re.findall(r"[\U0001f1e6-\U0001f1ff]{2}", remark):
        code = flag_to_country_code(pair)
        if code in KNOWN_ISO_CODES:
            claims.add(code)
    upper = remark.upper()
    for name, code in COUNTRY_NAMES_TO_ISO.items():
        if re.search(r"\b" + re.escape(name) + r"\b", upper):
            claims.add(code)
    explicit = re.findall(r"[\[(]\s*([A-Za-z]{2})\s*[\])]", remark)
    explicit += re.findall(r"(?:\||:|_|-)\s*([A-Za-z]{2})(?=\s*(?:\||:|_|-|$))", remark)
    claims.update(code.upper() for code in explicit if code.upper() in KNOWN_ISO_CODES)
    if not claims:
        for code in re.findall(r"(?<!\S)([A-Z]{2})(?!\S)", remark):
            if code in KNOWN_ISO_CODES and code not in COMMON_STOP_WORDS:
                claims.add(code)
    return next(iter(claims)) if len(claims) == 1 else "XX"


def repair_base64_padding(s: str) -> str:
    s = s.strip()
    return s + "=" * ((4 - len(s) % 4) % 4)


def safe_b64decode(s: str) -> Optional[str]:
    try:
        return base64.b64decode(repair_base64_padding(s), altchars=b"-_", validate=True).decode("utf-8")
    except (ValueError, UnicodeError):
        return None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("conflicting repeated JSON keys")
        result[key] = value
    return result


def _load_json(content: str) -> Any:
    try:
        return json.loads(content, object_pairs_hook=_unique_object)
    except RecursionError:
        raise models.ConnectionLimitError("JSON nesting exceeds decoder safety limit") from None


def _port(value: Any) -> Optional[int]:
    if type(value) is int:
        port = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,5}", value):
        port = int(value)
    else:
        return None
    return port if 1 <= port <= 65535 else None


def _address(value: Any) -> bool:
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    if any(c in value for c in "[]/@?#\\"):
        return False
    if ":" in value:
        try:
            ipaddress.IPv6Address(value)
        except ValueError:
            return False
    elif "%" in value:
        return False
    return True


def _unquote(value: str) -> str:
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise ValueError("malformed percent escape")
    return urllib.parse.unquote(value, errors="strict")


def _query_params(query: str) -> dict[str, Any]:
    _unquote(query)
    params: dict[str, Any] = {}
    for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True, errors="strict"):
        key = PARAM_ALIASES.get(key, key)
        if key in params:
            previous = params[key] if isinstance(params[key], list) else [params[key]]
            params[key] = previous + [value]
        else:
            params[key] = value
    return params


def _node(scheme: str, address: str, port: Any, credential: Any, params: dict,
          remark: str, source: Optional[SourceConfig], raw_uri: str = "") -> Optional[ProxyNode]:
    port = _port(port)
    if scheme not in SUPPORTED_SCHEMES or not _address(address) or port is None:
        return None
    if not isinstance(credential, str) or not credential or not isinstance(remark, str):
        return None
    if scheme in ("ss", "tuic"):
        username, separator, password = credential.partition(":")
        if not separator or not username or (scheme == "tuic" and not password):
            return None
    try:
        node = ProxyNode(scheme, address, port, credential, params=params, raw_uri=raw_uri,
                         remark=remark, country_code=detect_country(remark),
                         source_ids={source.id} if source else set(), tags=set(source.tags) if source else set(),
                         is_verified_source=source.verified_upstream if source else False)
        node.to_uri(False)
    except models.ConnectionLimitError:
        raise
    except (ValueError, TypeError, UnicodeError):
        return None
    return node


def _endpoint(hostport: str, default_port: int) -> tuple[str, Any, str]:
    hostport, separator, path = hostport.partition("/")
    path = _unquote("/" + path) if separator else ""
    if hostport.startswith("["):
        close = hostport.find("]")
        if close < 0:
            raise ValueError("invalid IPv6 authority")
        address, tail = hostport[1:close], hostport[close + 1:]
        ipaddress.IPv6Address(address)
        if tail and not tail.startswith(":"):
            raise ValueError("invalid authority")
        port = tail[1:] if tail else default_port
    elif hostport.count(":") == 1:
        address, port = hostport.rsplit(":", 1)
    elif ":" in hostport:
        raise ValueError("IPv6 authority must be bracketed")
    else:
        address, port = hostport, default_port
    return address, port, path


def parse_proxy_uri(uri: str, source: Optional[SourceConfig] = None) -> Optional[ProxyNode]:
    if not isinstance(uri, str):
        return None
    try:
        models.bounded_uri(uri)
    except UnicodeError:
        return None
    uri = uri.strip()
    if "://" not in uri:
        return None
    scheme, rest = uri.split("://", 1)
    scheme = {"hy2": "hysteria2", "wg": "wireguard"}.get(scheme.lower(), scheme.lower())
    if scheme not in SUPPORTED_SCHEMES:
        return None
    try:
        if scheme == "vmess" and "@" not in rest.split("#", 1)[0]:
            return _parse_vmess_json(uri, rest, source)
        if scheme == "ss":
            return _parse_shadowsocks(uri, rest, source)
        return _parse_standard_uri(scheme, uri, rest, source)
    except models.ConnectionLimitError:
        raise
    except (ValueError, TypeError, UnicodeError):
        return None


def _parse_vmess_json(raw_uri: str, rest: str, source: Optional[SourceConfig]) -> Optional[ProxyNode]:
    encoded, separator, fragment = rest.partition("#")
    decoded = safe_b64decode(encoded)
    if decoded is None:
        return None
    data = _load_json(decoded)
    if not isinstance(data, dict):
        return None
    params = {}
    string_fields = {"v", "net", "tls", "type", "host", "path", "sni", "fp", "scy", "serviceName"}
    for key, value in data.items():
        if key in ("add", "port", "id", "ps"):
            continue
        if key in string_fields and not isinstance(value, str):
            return None
        if key == "aid":
            if type(value) is int and value >= 0:
                value = str(value)
            elif not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
                return None
        if key == "alpn" and not (isinstance(value, str) or isinstance(value, list) and value and all(isinstance(v, str) for v in value)):
            return None
        canonical = VMESS_FIELDS.get(key, PARAM_ALIASES.get(key, key))
        if canonical in params:
            return None
        params[canonical] = value
    remark = _unquote(fragment) if separator else data.get("ps", "")
    return _node("vmess", data.get("add"), data.get("port"), data.get("id"), params, remark, source, raw_uri)


def _parse_shadowsocks(raw_uri: str, rest: str, source: Optional[SourceConfig]) -> Optional[ProxyNode]:
    rest, _, fragment = rest.partition("#")
    rest, _, query = rest.partition("?")
    if "@" in rest:
        userinfo, hostport = rest.rsplit("@", 1)
        credential = _unquote(userinfo) if ":" in userinfo else safe_b64decode(_unquote(userinfo))
    else:
        decoded = safe_b64decode(rest)
        if not decoded or "@" not in decoded:
            return None
        credential, hostport = decoded.rsplit("@", 1)
    address, port, path = _endpoint(hostport, 8388)
    if path not in ("", "/"):
        return None
    return _node("ss", address, port, credential, _query_params(query), _unquote(fragment), source, raw_uri)


def _parse_standard_uri(scheme: str, raw_uri: str, rest: str, source: Optional[SourceConfig]) -> Optional[ProxyNode]:
    rest, _, fragment = rest.partition("#")
    rest, _, query = rest.partition("?")
    if "@" not in rest:
        return None
    userinfo, hostport = rest.rsplit("@", 1)
    if scheme == "tuic":
        username, separator, password = userinfo.partition(":")
        username, password = _unquote(username), _unquote(password)
        if not separator or ":" in username:
            return None
        credential = username + ":" + password
    else:
        credential = _unquote(userinfo)
    address, port, path = _endpoint(hostport, 443)
    params = _query_params(query)
    if path:
        if "path" in params and params["path"] != path:
            return None
        params["path"] = path
    return _node(scheme, address, port, credential, params, _unquote(fragment), source, raw_uri)


class _SubscriptionHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.candidates = 0

    def _append(self, text: str) -> None:
        for _ in URI_REGEX.finditer(text):
            self.candidates += 1
            _check_candidate_count(self.candidates)
        self.parts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        for key, value in attrs:
            if key == "href" and value:
                self._append("\n" + value + "\n")
        if tag in ("br", "div", "p", "tr", "li", "code", "pre"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._append(data)


def extract_nodes_from_content(content: str, source: SourceConfig) -> tuple[list[ProxyNode], list[dict]]:
    content = content.strip()
    if not content:
        return [], []
    json_start = re.match(r'^(?:\{\s*(?:"|})|\[\s*(?:\{|\[|"|\]|-?\d|true\b|false\b|null\b))', content)
    json_member = content.startswith(("{", "[")) and re.search(r'\{\s*"(?:[^"\\]|\\.)*"\s*:', content)
    if source.format == "xray_json" or json_start or json_member:
        try:
            data = _load_json(content)
        except models.ConnectionLimitError:
            raise
        except (ValueError, TypeError):
            return [], []
        configs = data if isinstance(data, list) else [data]
        # Only independently representable connections leave this boundary, never executable configs.
        return _extract_nodes_from_xray_json(configs, source), []
    text = content
    if source.format in ("telegram_web", "html") or re.search(r"<\w+[\s>]", content):
        parser = _SubscriptionHTML()
        parser.feed(content)
        text = "".join(parser.parts)
    if "://" not in text:
        decoded = safe_b64decode("".join(text.split()))
        if decoded and "://" in decoded:
            text = decoded
    nodes, candidates = [], 0
    for line in io.StringIO(text):
        candidate_text = line
        if "://" not in line:
            decoded = safe_b64decode(line.strip())
            if decoded:
                candidate_text = decoded
        for match in URI_REGEX.finditer(candidate_text):
            candidates += 1
            _check_candidate_count(candidates)
            if match.end() - match.start() > models.MAX_URI_BYTES:
                raise models.ConnectionLimitError(f"connection exceeds {models.MAX_URI_BYTES} UTF-8 bytes")
            node = parse_proxy_uri(match.group(0), source)
            if node is not None:
                nodes.append(node)
    return nodes, []


def _mapped_settings(settings: Any, mapping: dict[str, tuple[str, type]]) -> dict:
    if not isinstance(settings, dict) or set(settings) - mapping.keys():
        raise ValueError("unsupported settings")
    result = {}
    for key, value in settings.items():
        canonical, expected = mapping[key]
        if type(value) is not expected:
            raise ValueError("invalid setting type")
        if expected is list and (not value or any(not isinstance(v, str) for v in value)):
            raise ValueError("invalid list setting")
        result[canonical] = ("true" if value else "false") if expected is bool else value
    return result


def _stream_params(stream: Any) -> dict:
    if not isinstance(stream, dict):
        raise ValueError("invalid stream")
    network, security = stream.get("network", "tcp"), stream.get("security", "none")
    if not isinstance(network, str) or network not in ("tcp", "ws", "grpc", "httpupgrade", "xhttp", "splithttp"):
        raise ValueError("unsupported transport")
    if not isinstance(security, str) or security not in ("none", "tls", "reality"):
        raise ValueError("unsupported security")
    transport_key = {"tcp": "tcpSettings", "ws": "wsSettings", "grpc": "grpcSettings",
                     "httpupgrade": "httpupgradeSettings", "xhttp": "xhttpSettings", "splithttp": "splithttpSettings"}[network]
    security_key = {"tls": "tlsSettings", "reality": "realitySettings"}.get(security)
    allowed = {"network", "security", transport_key}
    if security_key:
        allowed.add(security_key)
    if set(stream) - allowed:
        raise ValueError("unsupported stream dependency/settings")
    params = {PARAM_ALIASES.get(key, key): stream[key] for key in ("network", "security") if key in stream}
    if security_key in stream:
        mapping = {"serverName": ("sni", str), "fingerprint": ("fp", str)}
        if security == "tls":
            mapping.update(alpn=("alpn", list), allowInsecure=("allowInsecure", bool), disableSNI=("disableSNI", bool))
        else:
            mapping.update(publicKey=("pbk", str), shortId=("sid", str), spiderX=("spx", str))
        params.update(_mapped_settings(stream[security_key], mapping))
    if transport_key in stream:
        settings = stream[transport_key]
        if network == "tcp":
            if not isinstance(settings, dict) or set(settings) - {"header"}:
                raise ValueError("unsupported TCP settings")
            if "header" in settings:
                header = _mapped_settings(settings["header"], {"type": ("headerType", str)})
                if header.get("headerType") != "none":
                    raise ValueError("unsupported TCP headers")
                params.update(header)
        elif network == "ws":
            if not isinstance(settings, dict) or set(settings) - {"path", "headers"}:
                raise ValueError("unsupported WebSocket settings")
            params.update(_mapped_settings({k: v for k, v in settings.items() if k != "headers"}, {"path": ("path", str)}))
            if "headers" in settings:
                params.update(_mapped_settings(settings["headers"], {"Host": ("host", str)}))
        elif network == "grpc":
            params.update(_mapped_settings(settings, {"serviceName": ("serviceName", str), "multiMode": ("multiMode", bool), "authority": ("authority", str)}))
        else:
            mapping = {"path": ("path", str), "host": ("host", str)}
            if network in ("xhttp", "splithttp"):
                mapping["mode"] = ("mode", str)
            params.update(_mapped_settings(settings, mapping))
    return params


def _check_json_candidate_budget(json_configs: list[dict]) -> None:
    _check_candidate_count(len(json_configs))
    candidates = 0
    for config in json_configs:
        outbounds = config.get("outbounds") if isinstance(config, dict) else None
        if not isinstance(outbounds, list):
            continue
        for outbound in outbounds:
            settings = outbound.get("settings") if isinstance(outbound, dict) else None
            expanded = 0
            for key in ("vnext", "servers"):
                servers = settings.get(key) if isinstance(settings, dict) else None
                if not isinstance(servers, list):
                    continue
                for server in servers:
                    users = server.get("users") if isinstance(server, dict) else None
                    expanded += max(1, len(users)) if isinstance(users, list) else 1
                    _check_candidate_count(candidates + expanded)
            candidates += max(1, expanded)
            _check_candidate_count(candidates)


def _extract_nodes_from_xray_json(json_configs: list[dict], source: SourceConfig) -> list[ProxyNode]:
    _check_json_candidate_budget(json_configs)
    extracted = []
    for config in json_configs:
        if not isinstance(config, dict) or "transport" in config or not isinstance(config.get("outbounds"), list):
            continue
        for outbound in config["outbounds"]:
            if not isinstance(outbound, dict) or set(outbound) - {"protocol", "settings", "streamSettings", "tag"}:
                continue
            protocol = outbound.get("protocol")
            if protocol not in ("vless", "vmess", "trojan", "shadowsocks"):
                continue
            scheme = "ss" if protocol == "shadowsocks" else protocol
            settings, remark = outbound.get("settings"), outbound.get("tag", "")
            key = "vnext" if scheme in ("vless", "vmess") else "servers"
            if not isinstance(settings, dict) or set(settings) != {key} or not isinstance(settings[key], list) or not isinstance(remark, str):
                continue
            try:
                params = _stream_params(outbound.get("streamSettings", {}))
            except (ValueError, TypeError):
                continue
            for server in settings[key]:
                if not isinstance(server, dict) or not _address(server.get("address")) or _port(server.get("port")) is None:
                    continue
                if scheme in ("vless", "vmess"):
                    if set(server) - {"address", "port", "users"} or not isinstance(server.get("users"), list):
                        continue
                    for user in server["users"]:
                        if not isinstance(user, dict) or not isinstance(user.get("id"), str) or not user["id"]:
                            continue
                        try:
                            user_settings = {k: v for k, v in user.items() if k != "id"}
                            if scheme == "vless":
                                user_params = _mapped_settings(user_settings, {"encryption": ("encryption", str), "flow": ("flow", str)})
                            else:
                                user_params = _mapped_settings(user_settings, {"alterId": ("aid", int), "security": ("scy", str)})
                                if "aid" in user_params:
                                    if user_params["aid"] < 0:
                                        continue
                                    user_params["aid"] = str(user_params["aid"])
                        except (ValueError, TypeError):
                            continue
                        node = _node(scheme, server["address"], server["port"], user["id"], params | user_params, remark, source)
                        if node:
                            extracted.append(node)
                else:
                    allowed = {"address", "port", "password"}
                    if scheme == "ss":
                        allowed.update({"method", "plugin", "pluginOpts"})
                    if set(server) - allowed or not isinstance(server.get("password"), str):
                        continue
                    credential, server_params = server["password"], dict(params)
                    if scheme == "trojan":
                        server_params.setdefault("security", "none")
                    if scheme == "ss":
                        method = server.get("method")
                        if not isinstance(method, str) or not method or ":" in method:
                            continue
                        credential = method + ":" + credential
                        if "plugin" in server or "pluginOpts" in server:
                            plugin, options = server.get("plugin"), server.get("pluginOpts", "")
                            if not isinstance(plugin, str) or not plugin or ";" in plugin or not isinstance(options, str):
                                continue
                            server_params["plugin"] = plugin + (";" + options if options else "")
                    node = _node(scheme, server["address"], server["port"], credential, server_params, remark, source)
                    if node:
                        extracted.append(node)
    return extracted
