"""One bounded, verified HTTP transport for untrusted public destinations."""
from __future__ import annotations

import base64
import http.client
import ipaddress
import math
import os
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit


class NetworkError(OSError):
    """Transport failure; messages deliberately contain no request/proxy material."""


# getaddrinfo cannot be cancelled; cap outstanding daemon workers as well as callers.
_DNS_SLOTS = threading.BoundedSemaphore(32)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise NetworkError("Network deadline exceeded")
    return remaining


def _end(timeout: float, deadline: float | None = None) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Invalid network timeout")
    if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline)):
        raise ValueError("Invalid network deadline")
    return min(time.monotonic() + timeout, deadline) if deadline is not None else time.monotonic() + timeout


def _hostname(host: str) -> str:
    if not isinstance(host, str) or not host or re.search(r"[\s\x00-\x20\x7f-\x9f%\\]", host):
        raise ValueError("Invalid destination hostname")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        host = host.removesuffix(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("Invalid destination hostname") from None
    if (len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                               for label in host.split("."))
            or re.fullmatch(r"[0-9.]+", host) or re.fullmatch(r"0x[0-9a-f]+", host)):
        raise ValueError("Invalid destination hostname")
    return host


def _public_ip(value: str):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("Invalid resolved address") from None
    if ("%" in value or not ip.is_global or ip.is_multicast or ip.is_reserved
            or isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None)):
        raise ValueError("Non-public destination rejected")
    return ip


def _url(url: str):
    if not isinstance(url, str) or not url or re.search(r"[\s\x00-\x20\x7f-\x9f\\]", url) or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", url, re.I):
        raise ValueError("Invalid public URL")
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.netloc.endswith(":") or (parsed.netloc.startswith("[") and not re.fullmatch(r"\[[^\]]+\](?::[0-9]+)?", parsed.netloc)):
            raise ValueError
        host = _hostname(parsed.hostname)
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Invalid public URL") from None
    return parsed, host, port


def approved_public_url(url: str) -> str:
    """Offline public-input policy; transport also validates/pins every DNS answer.

    Query-bearing subscriptions may carry credentials. Only Telegram numeric
    pagination is allowed; never strip a secret into an apparently public URL.
    """
    try:
        if url == "data/discovered_ocr_configs.txt":
            return url
        parsed, host, _ = _url(url)
        if len(url.encode("utf-8")) > 16 * 1024 or parsed.fragment:
            raise ValueError
        if parsed.query and not (host == "t.me" and re.fullmatch(r"/s/[A-Za-z][A-Za-z0-9_]{2,31}", parsed.path) and re.fullmatch(r"before=[1-9][0-9]{0,19}", parsed.query)):
            raise ValueError
        path = parsed.path or "/"
        if host in ("t.me", "www.t.me"):
            channel = re.fullmatch(r"/(?:s/)?([A-Za-z][A-Za-z0-9_]{2,31})(/[0-9]+)?/?", path)
            if channel:
                host = "t.me"
                path = "/s/" + channel[1].lower() + (channel[2] or "")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith((".localhost", ".local", ".internal", ".home", ".lan", ".test", ".invalid")):
                raise ValueError
        else:
            _public_ip(host)
        authority = f"[{host}]" if ":" in host else host
        if parsed.port is not None and parsed.port != (443 if parsed.scheme == "https" else 80):
            authority += f":{parsed.port}"
        return urlunsplit((parsed.scheme, authority, path, parsed.query, ""))
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise ValueError("Unapproved discovery URL") from None


def _resolve(host: str, port: int, deadline: float, *, public: bool = True):
    host = _hostname(host)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("Invalid destination port")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if public:
            _public_ip(host)
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (host, port))]
    if not _DNS_SLOTS.acquire(timeout=_remaining(deadline)):
        raise NetworkError("DNS capacity exceeded")
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception:
            result.put(None)
        finally:
            _DNS_SLOTS.release()

    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses = result.get(timeout=_remaining(deadline))
    except queue.Empty:
        raise NetworkError("DNS deadline exceeded") from None
    if not addresses:
        raise NetworkError("DNS resolution failed")
    for family, socktype, protocol, _, address in addresses:
        if family not in (socket.AF_INET, socket.AF_INET6) or socktype != socket.SOCK_STREAM:
            raise ValueError("Invalid resolved address")
        if public:
            _public_ip(address[0])
        elif "%" in address[0]:
            raise ValueError("Unsupported proxy address")
    return addresses


def _connect(addresses, deadline, source_address=None, *, public=True):
    for family, socktype, protocol, _, address in addresses:
        _remaining(deadline)
        sock = None
        try:
            sock = socket.socket(family, socktype, protocol)
            sock.settimeout(_remaining(deadline))
            if source_address is not None:
                sock.bind(source_address)
            sock.connect(address)
            peer = sock.getpeername()
            if public:
                _public_ip(peer[0])
            if ipaddress.ip_address(peer[0]) != ipaddress.ip_address(address[0]) or peer[1] != address[1]:
                raise ValueError("Connected destination changed")
            sock.settimeout(_remaining(deadline))
            return sock
        except ValueError:
            if sock is not None:
                sock.close()
            raise
        except OSError:
            if sock is not None:
                sock.close()
    raise NetworkError("Connection failed") from None


def connect_public(address: tuple[str, int], timeout: float | None = None, source_address=None) -> socket.socket:
    """Resolve once, validate every answer, connect numerically and verify the peer."""
    if not isinstance(address, tuple) or len(address) != 2:
        raise ValueError("Invalid destination address")
    deadline = _end(20.0 if timeout is None else timeout)
    return _connect(_resolve(address[0], address[1], deadline), deadline, source_address)


def validate_public_url(url: str) -> None:
    _, host, port = _url(url)
    _resolve(host, port, _end(20.0))


def _proxy(scheme):
    # Only explicit environment settings; no OS registry or implicit client lookup.
    value = os.environ.get(f"{scheme}_proxy") or os.environ.get(f"{scheme.upper()}_PROXY") or os.environ.get("all_proxy") or os.environ.get("ALL_PROXY")
    if not value:
        return None
    try:
        if re.search(r"[\s\x00-\x20\x7f-\x9f\\]", value):
            raise ValueError
        parsed = urlsplit(value)
        if parsed.scheme != "http" or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError
        host = _hostname(parsed.hostname)
        port = parsed.port if parsed.port is not None else 80
        if not 1 <= port <= 65535:
            raise ValueError
        authorization = None
        if parsed.username is not None:
            credentials = f"{unquote(parsed.username)}:{unquote(parsed.password or '')}"
            authorization = "Basic " + base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        return host, port, authorization
    except (ValueError, TypeError):
        raise ValueError("Unsupported or invalid explicit proxy (only HTTP proxies supported)") from None


def _authority(host, port):
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


class _Response(http.client.HTTPResponse):
    """Stdlib chunk decoding tolerates invalid separators and trailer EOF."""

    framing_bytes = 0
    MAX_FRAMING_BYTES = 65536

    def _count_framing(self, size):
        self.framing_bytes += size
        if self.framing_bytes > self.MAX_FRAMING_BYTES:
            raise NetworkError("Response chunk framing exceeds byte limit")

    def _read_next_chunk_size(self):
        line = self.fp.readline(min(http.client._MAXLINE, self.MAX_FRAMING_BYTES - self.framing_bytes) + 1)
        self._count_framing(len(line))
        if not line.endswith(b"\r\n"):
            raise NetworkError("Invalid chunk framing")
        size = line.split(b";", 1)[0].strip()
        if not re.fullmatch(b"[0-9a-fA-F]+", size):
            raise NetworkError("Invalid chunk size")
        return int(size, 16)

    def _get_chunk_left(self):
        if self.chunk_left == 0:
            self._count_framing(2)
            if self._safe_read(2) != b"\r\n":
                raise NetworkError("Invalid chunk framing")
            self.chunk_left = None
        return super()._get_chunk_left()

    def _read_and_discard_trailer(self):
        while True:
            line = self.fp.readline(min(http.client._MAXLINE, self.MAX_FRAMING_BYTES - self.framing_bytes) + 1)
            self._count_framing(len(line))
            if len(line) > http.client._MAXLINE or not line.endswith(b"\r\n"):
                raise NetworkError("Incomplete or invalid chunk terminator")
            if line == b"\r\n":
                return


class _Connection(http.client.HTTPConnection):
    response_class = _Response

    def __init__(self, host, port, secure, deadline, proxy):
        super().__init__(host, port, timeout=_remaining(deadline))
        self.secure = secure
        self.deadline = deadline
        self.proxy = proxy
        self.timer = None
        self.watched_socket = None
        self.pinned_host = None

    def connect(self):
        try:
            targets = _resolve(self.host, self.port, self.deadline)
            self.pinned_host = targets[0][4][0]
            if self.proxy:
                proxy_host, proxy_port, authorization = self.proxy
                if self.secure:
                    headers = {"Proxy-Authorization": authorization} if authorization else {}
                    self.set_tunnel(self.pinned_host, self.port, headers)
                self.sock = _connect(_resolve(proxy_host, proxy_port, self.deadline, public=False), self.deadline, public=False)
            else:
                self.sock = _connect(targets, self.deadline)
            self.watched_socket = self.sock
            self.timer = threading.Timer(_remaining(self.deadline), self._expire)
            self.timer.daemon = True
            self.timer.start()
            if self.proxy and self.secure:
                self._tunnel()
            if self.secure:
                self.sock.settimeout(_remaining(self.deadline))
                self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)
                self.watched_socket = self.sock
            self.sock.settimeout(_remaining(self.deadline))
        except (OSError, http.client.HTTPException) as exc:
            self.close()
            raise NetworkError("HTTP connection failed") from exc
        except ValueError:
            self.close()
            raise

    def _expire(self):
        sock = self.watched_socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError):
                pass
            sock.close()


def fetch_bytes(url: str, *, timeout: float = 20.0, max_bytes: int,
                deadline: float | None = None, headers: dict[str, str] | None = None,
                data: bytes | None = None, method: str | None = None,
                authenticated: bool = False) -> tuple[int, bytes]:
    """Fetch complete bytes, rejecting unsafe destinations and oversized responses."""
    end = _end(timeout, deadline)
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("Invalid response byte limit")
    if data is not None and not isinstance(data, bytes):
        raise ValueError("Request data must be bytes")
    method = method if method is not None else ("POST" if data is not None else "GET")
    if not isinstance(method, str) or not re.fullmatch(r"[A-Z]+", method):
        raise ValueError("Invalid HTTP method")
    if headers is not None and not isinstance(headers, dict):
        raise ValueError("Invalid HTTP headers")
    request_headers = {"Accept-Encoding": "identity", "Connection": "close"}
    for key, value in (headers or {}).items():
        if (not isinstance(key, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
                or not isinstance(value, str) or re.search(r"[\x00-\x1f\x7f-\x9f]", value)
                or key.lower() in ("host", "proxy-authorization", "transfer-encoding", "content-length")):
            raise ValueError("Invalid HTTP headers")
        try:
            value.encode("latin-1")
        except UnicodeError:
            raise ValueError("Invalid HTTP headers") from None
        request_headers[key] = value
        if key.lower() in ("authorization", "cookie"):
            authenticated = True
    for redirect in range(6):
        parsed, host, port = _url(url)
        connection = _Connection(host, port, parsed.scheme == "https", end, _proxy(parsed.scheme))
        result = None
        try:
            connection.connect()
            path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parsed.query:
                path += "?" + quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
            wire_headers = dict(request_headers)
            wire_headers["Host"] = _authority(host, port) if port != (443 if connection.secure else 80) else (f"[{host}]" if ":" in host else host)
            if connection.proxy and not connection.secure:
                path = f"http://{_authority(connection.pinned_host, port)}{path}"
                if connection.proxy[2]:
                    wire_headers["Proxy-Authorization"] = connection.proxy[2]
            connection.sock.settimeout(_remaining(end))
            connection.request(method, path, body=data, headers=wire_headers)
            connection.sock.settimeout(_remaining(end))
            # Retain the socket even if getresponse detaches it for Connection: close.
            transport = connection.sock
            result = connection.getresponse()
            _remaining(end)
            if result.status in (301, 302, 303, 307, 308):
                if authenticated:
                    raise NetworkError("Authenticated redirects are rejected")
                location = result.getheader("Location")
                if not location or redirect == 5 or method not in ("GET", "HEAD"):
                    raise NetworkError("Unsupported HTTP redirect")
                if re.search(r"[\s\x00-\x20\x7f-\x9f\\]", location):
                    raise ValueError("Invalid redirect target")
                target = urljoin(url, location)
                target_parts, _, _ = _url(target)
                if parsed.scheme == "https" and target_parts.scheme != "https":
                    raise ValueError("HTTPS downgrade rejected")
                url = target
                continue
            length = result.getheader("Content-Length")
            transfer = result.getheader("Transfer-Encoding")
            if transfer is not None and (transfer.lower() != "chunked" or length is not None):
                raise NetworkError("Unsupported or ambiguous HTTP framing")
            if length is not None and (not re.fullmatch(r"[0-9]{1,20}", length) or int(length) > max_bytes):
                raise NetworkError("Response exceeds byte limit or has invalid length")
            encoding = result.getheader("Content-Encoding", "identity").lower()
            if encoding != "identity":
                raise NetworkError("Unsupported response content encoding")
            body = bytearray()
            while True:
                transport.settimeout(_remaining(end))
                chunk = result.read1(min(65536, max_bytes + 1 - len(body)))
                _remaining(end)
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise NetworkError("Response exceeds byte limit")
            if method != "HEAD" and length is not None and len(body) != int(length):
                raise NetworkError("Incomplete HTTP response")
            return result.status, bytes(body)
        except (OSError, http.client.HTTPException) as exc:
            if isinstance(exc, NetworkError):
                raise
            raise NetworkError("HTTP transport failed") from exc
        except ValueError:
            raise
        finally:
            if connection.timer is not None:
                connection.timer.cancel()
            if result is not None:
                result.close()
            connection.close()
            connection.watched_socket = None
    raise NetworkError("Redirect limit exceeded")
