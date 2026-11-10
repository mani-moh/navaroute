"""Socket-free wire fixtures: policy/HTTP/TLS code stays real; DNS and I/O do not."""
import contextlib
import io
import os
import socket
import ssl
import threading
import sys
import time
import unittest
from unittest.mock import patch

from src import network


class WireSocket:
    def __init__(self, response, peer=None, clock=None):
        self.response = response
        self.peer = peer
        self.clock = clock
        self.connected = None
        self.sent = bytearray()
        self.closed = False
        self.timeout = None
        self.read_sizes = []

    def settimeout(self, timeout):
        self.timeout = timeout

    def bind(self, address):
        self.bound = address

    def connect(self, address):
        self.connected = address

    def getpeername(self):
        return self.peer or self.connected

    def sendall(self, data):
        self.sent.extend(data)

    def makefile(self, mode):
        owner = self

        class Stream(io.BytesIO):
            def read(self, size=-1):
                owner.read_sizes.append(size)
                if owner.clock:
                    owner.clock[0] += 0.4
                return super().read(size)

            def read1(self, size=-1):
                return self.read(size)

        return Stream(self.response)

    def close(self):
        self.closed = True


def response(body=b"ok", status=200, headers=b""):
    return (f"HTTP/1.1 {status} Fixture\r\n".encode() + headers
            + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)


class WireFixture(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        system = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
        self.stack.enter_context(patch.dict(os.environ, system, clear=True))
        self.sockets = []
        self.responses = [response()]
        self.resolutions = []
        self.answers = {"public.example": ["8.8.8.8"], "other.example": ["1.1.1.1"],
                        "private.example": ["127.0.0.1"], "mixed.example": ["8.8.8.8", "10.0.0.1"]}

        def resolver(host, port, *args, **kwargs):
            self.resolutions.append((host, port))
            addresses = self.answers.get(host, [host])
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET,
                     socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port)) for ip in addresses]

        def transport(*args, **kwargs):
            stream = WireSocket(self.responses.pop(0))
            self.sockets.append(stream)
            return stream

        self.stack.enter_context(patch("socket.getaddrinfo", resolver))
        self.stack.enter_context(patch("socket.socket", transport))

    def fetch(self, url="http://public.example/feed", **kwargs):
        return network.fetch_bytes(url, max_bytes=kwargs.pop("max_bytes", 16), **kwargs)


class NetworkTests(WireFixture):
    def test_bad_url_and_ip_variants_never_create_socket(self):
        for url in ["http://127.0.0.1/", "http://10.1.2.3/", "http://169.254.169.254/",
                    "http://[::1]/", "http://[fc00::1]/", "http://[::ffff:127.0.0.1]/",
                    "http://[::ffff:8.8.8.8]/", "http://[fe80::1%25eth0]/", "http://[2001:db8::1]/",
                    "http://127.1/", "http://2130706433/", "http://0177.0.0.1/", "http://0x7f000001/",
                    "http://public.example\n/private", "http://public.example/%0d%0aX:1",
                    "http://bad_host.example/", "http://-bad.example/", "http://host..example/",
                    "http://public.example../", "http://[2606:4700:4700::1111]suffix/", "http://public.example:/",
                    "http://%31%32%37.0.0.1/", "http://user:secret@public.example/", "file:///etc/passwd",
                    "http://public.example:0/", "http://public.example:65536/", "http://public.example:bad/"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.responses = [response()]
                self.fetch(url)
        self.assertEqual(self.sockets, [])

    def test_private_or_mixed_dns_never_connects(self):
        for host in ("private.example", "mixed.example"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                self.fetch(f"http://{host}/")
        self.assertEqual(self.sockets, [])

    def test_dns_is_pinned_and_host_header_retained(self):
        def rebinding(host, port, *args, **kwargs):
            self.resolutions.append((host, port))
            ip = "8.8.8.8" if len(self.resolutions) == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
        with patch("socket.getaddrinfo", rebinding):
            self.assertEqual(self.fetch(), (200, b"ok"))
        self.assertEqual(len(self.resolutions), 1)
        self.assertEqual(self.sockets[0].connected, ("8.8.8.8", 80))
        self.assertIn(b"Host: public.example\r\n", self.sockets[0].sent)

    def test_rebound_connected_peer_is_closed_before_http(self):
        sock = WireSocket(response(), peer=("127.0.0.1", 80))
        with patch("socket.socket", return_value=sock), self.assertRaises(ValueError):
            self.fetch()
        self.assertTrue(sock.closed)
        self.assertEqual(sock.sent, b"")

    def test_public_connection_keeps_source_address(self):
        connected = network.connect_public(("public.example", 443), timeout=1, source_address=("0.0.0.0", 0))
        self.assertEqual(connected.connected, ("8.8.8.8", 443))
        self.assertEqual(connected.bound, ("0.0.0.0", 0))

    def test_validate_resolves_private_dns(self):
        with self.assertRaises(ValueError):
            network.validate_public_url("http://private.example/")
        self.assertEqual(self.sockets, [])

    def test_redirect_private_dns_never_gets_request(self):
        self.responses = [response(status=302, headers=b"Location: http://private.example/secret\r\n")]
        with self.assertRaises(ValueError):
            self.fetch()
        self.assertEqual(len(self.sockets), 1)
        self.assertNotIn(b"secret", self.sockets[0].sent)
        self.assertTrue(self.sockets[0].closed)

    def test_public_redirect_and_non_success_status_are_preserved(self):
        self.responses = [response(status=302, headers=b"Location: http://other.example/new\r\n"),
                          response(body=b"missing", status=404)]
        self.assertEqual(self.fetch(), (404, b"missing"))
        self.assertEqual(self.sockets[1].connected, ("1.1.1.1", 80))
        self.assertIn(b"GET /new HTTP/1.1", self.sockets[1].sent)

    def test_authenticated_redirects_never_forward_credentials(self):
        for target in ("http://other.example/token", "/same-origin", "http://public.example/down"):
            self.responses = [response(status=302, headers=f"Location: {target}\r\n".encode())]
            before = len(self.sockets)
            with self.subTest(target=target), self.assertRaises(network.NetworkError):
                self.fetch(headers={"Authorization": "Bearer synthetic-secret"}, authenticated=True)
            self.assertEqual(len(self.sockets), before + 1)

    def test_authorization_header_alone_also_rejects_redirect(self):
        self.responses = [response(status=302, headers=b"Location: /same\r\n")]
        with self.assertRaises(network.NetworkError):
            self.fetch(headers={"authorization": "Bearer synthetic-secret"})
        self.assertEqual(len(self.sockets), 1)

    def test_tls_verification_and_original_sni_survive_ip_pinning(self):
        contexts = []
        original = ssl.create_default_context
        def context_factory():
            ctx = original()
            contexts.append(ctx)
            def verify(sock, *, server_hostname):
                self.assertEqual(server_hostname, "public.example")
                self.assertTrue(ctx.check_hostname)
                self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
                self.assertEqual(sock.connected, ("8.8.8.8", 443))
                raise ssl.SSLCertVerificationError("synthetic bad certificate")
            ctx.wrap_socket = verify
            return ctx
        with patch("ssl.create_default_context", context_factory), self.assertRaises(network.NetworkError):
            self.fetch("https://public.example/?token=synthetic-secret")
        self.assertEqual(len(contexts), 1)
        self.assertEqual(self.sockets[0].sent, b"")
        self.assertTrue(self.sockets[0].closed)

    def test_https_downgrade_redirect_is_rejected(self):
        self.responses = [response(status=302, headers=b"Location: http://public.example/down\r\n")]
        ctx = ssl.create_default_context()
        ctx.wrap_socket = lambda sock, **kwargs: sock
        with patch("ssl.create_default_context", return_value=ctx), self.assertRaises(ValueError):
            self.fetch("https://public.example/")
        self.assertEqual(len(self.sockets), 1)

    def test_explicit_proxy_failure_never_retries_direct(self):
        for key in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy"):
            attempts = []
            def fail(address):
                attempts.append(address)
                raise OSError("http://user:synthetic-secret@127.0.0.1:9999/?token=secret")
            stream = WireSocket(response())
            stream.connect = fail
            scheme = "https" if "https" in key.lower() else "http"
            with self.subTest(key=key), patch.dict(os.environ, {key: "http://127.0.0.1:9999"}), \
                    patch("socket.socket", return_value=stream), self.assertRaises(network.NetworkError) as error:
                self.fetch(f"{scheme}://public.example/feed")
            self.assertEqual(attempts, [("127.0.0.1", 9999)])
            self.assertNotIn("synthetic-secret", str(error.exception))
        self.assertEqual(self.sockets, [])

    def test_unsupported_proxy_is_explicitly_rejected(self):
        for value in ("socks5://127.0.0.1:9999", "https://127.0.0.1:9999", "garbage"):
            with self.subTest(value=value), patch.dict(os.environ, {"all_proxy": value}), self.assertRaises(ValueError):
                self.fetch()
        self.assertEqual(self.sockets, [])

    def test_proxy_uses_pinned_destination_and_original_host(self):
        proxy = WireSocket(response())
        with patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:9999"}), \
                patch("socket.socket", return_value=proxy):
            self.assertEqual(self.fetch(), (200, b"ok"))
        self.assertIn(b"GET http://8.8.8.8:80/feed HTTP/1.1\r\n", proxy.sent)
        self.assertIn(b"Host: public.example\r\n", proxy.sent)

    def test_https_proxy_connect_pins_target_and_preserves_sni(self):
        proxy = WireSocket(response())
        wires = iter([b"HTTP/1.0 200 Connected\r\n\r\n", response()])
        proxy.makefile = lambda mode: io.BytesIO(next(wires))
        ctx = ssl.create_default_context()
        def tls(sock, *, server_hostname):
            self.assertEqual(server_hostname, "public.example")
            return sock
        ctx.wrap_socket = tls
        with patch.dict(os.environ, {"https_proxy": "http://user:pass@127.0.0.1:9999"}), \
                patch("socket.socket", return_value=proxy), \
                patch("ssl.create_default_context", return_value=ctx):
            self.assertEqual(self.fetch("https://public.example/feed"), (200, b"ok"))
        tunnel, destination = bytes(proxy.sent).split(b"\r\n\r\n", 1)
        self.assertIn(b"CONNECT 8.8.8.8:443", tunnel)
        self.assertIn(b"Proxy-Authorization: Basic dXNlcjpwYXNz", tunnel)
        self.assertNotIn(b"Proxy-Authorization", destination)
        self.assertIn(b"Host: public.example", destination)

    def test_size_cap_never_returns_truncated_success(self):
        for wire in (response(body=b"12345"),
                     b"HTTP/1.1 200 OK\r\n\r\n12345",
                     b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\n12345\r\n0\r\n\r\n"):
            self.responses = [wire]
            with self.subTest(wire=wire), self.assertRaises(network.NetworkError):
                self.fetch(max_bytes=4)
            self.assertTrue(self.sockets[-1].closed)
        self.responses = [response(body=b"1234")]
        self.assertEqual(self.fetch(max_bytes=4), (200, b"1234"))

    def test_trickling_body_and_expired_deadline_are_bounded(self):
        clock = [100.0]
        stream = WireSocket(b"HTTP/1.1 200 OK\r\n\r\n" + b"x" * 100, clock=clock)
        original_file = stream.makefile
        def trickle(mode):
            result = original_file(mode)
            original_read = result.read
            result.read = lambda size=-1: original_read(min(size, 1))
            return result
        stream.makefile = trickle
        with patch("time.monotonic", lambda: clock[0]), patch("socket.socket", return_value=stream):
            with self.assertRaises(network.NetworkError):
                self.fetch(max_bytes=100, timeout=1.0)
            self.assertLessEqual(len(stream.read_sizes), 3)
            self.assertTrue(stream.closed)
            with self.assertRaises(network.NetworkError):
                self.fetch(deadline=99.0)

    def test_blocked_resolver_does_not_hold_caller_past_budget(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def stalled(*args, **kwargs):
            release.wait(1)
            return []
        started = time.monotonic()
        with patch("socket.getaddrinfo", stalled), self.assertRaises(network.NetworkError):
            self.fetch(timeout=0.02)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(self.sockets, [])

    def test_invalid_request_inputs_never_send(self):
        for kwargs in ({"max_bytes": 0}, {"max_bytes": True}, {"timeout": 0}, {"timeout": float("inf")},
                       {"deadline": float("nan")}, {"method": "GET\r\nX:1"},
                       {"headers": {"X": "ok\r\nAuthorization: secret"}},
                       {"headers": {"Host": "private.example"}}, {"headers": {"Proxy-Authorization": "secret"}},
                       {"data": "not bytes"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.fetch(**kwargs)
        self.assertEqual(self.sockets, [])

    def test_transport_error_never_exposes_query_or_credentials(self):
        def fail(*args, **kwargs):
            raise OSError("https://user:synthetic-secret@public.example/?access_token=secret")
        with patch("socket.socket", fail), self.assertRaises(network.NetworkError) as error:
            self.fetch("http://public.example/?access_token=secret")
        self.assertNotIn("secret", str(error.exception))
        self.assertNotIn("public.example", str(error.exception))

    def test_environment_proxy_policy_never_reads_registry_or_bypasses(self):
        for proxy in (None, "http://127.0.0.1:9999"):
            self.responses = [response()]
            values = {"NO_PROXY": "*", "no_proxy": "*"}
            if proxy:
                values["http_proxy"] = proxy
            with self.subTest(proxy=proxy), contextlib.ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ, values))
                system = stack.enter_context(patch("urllib.request.getproxies", side_effect=AssertionError("system proxies forbidden")))
                registry = None
                if sys.platform == "win32":
                    registry = stack.enter_context(patch("winreg.OpenKey", side_effect=AssertionError("registry is not a fixture")))
                self.assertEqual(self.fetch(), (200, b"ok"))
                self.assertEqual(self.sockets[-1].connected, ("127.0.0.1", 9999) if proxy else ("8.8.8.8", 80))
                system.assert_not_called()
                if registry:
                    registry.assert_not_called()

    def test_socket_deadline_interrupts_header_or_detached_body_read(self):
        for phase in ("headers", "body"):
            released = threading.Event()
            self.addCleanup(released.set)
            stream = WireSocket(b"HTTP/1.0 200 OK\r\n\r\n")
            stream.shutdown = lambda how: released.set()
            original = stream.makefile
            def makefile(mode):
                file = original(mode)
                def blocked(*args):
                    if not released.wait(0.5):
                        raise AssertionError("deadline did not interrupt transport")
                    raise OSError("deadline closed transport")
                if phase == "headers":
                    file.readline = blocked
                else:
                    file.read1 = blocked
                return file
            stream.makefile = makefile
            started = time.monotonic()
            with self.subTest(phase=phase), patch("socket.socket", return_value=stream), self.assertRaises(network.NetworkError):
                self.fetch(timeout=0.03)
            self.assertLess(time.monotonic() - started, 0.4)
            self.assertTrue(stream.closed)

    def test_redirect_controls_are_rejected_before_normalization(self):
        for location in (b"\x00http://other.example/new", b"http://other.example/ne\tw", b"http://other.example/%00new"):
            self.responses = [response(status=302, headers=b"Location: " + location + b"\r\n"), response()]
            before = len(self.sockets)
            with self.subTest(location=location), self.assertRaises(ValueError):
                self.fetch()
            self.assertEqual(len(self.sockets), before + 1)

    def test_incomplete_or_compressed_response_is_not_valid_bytes(self):
        for wire in (b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n12",
                     b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\n12",
                     response(body=b"not a decoded feed", headers=b"Content-Encoding: gzip\r\n")):
            self.responses = [wire]
            with self.subTest(wire=wire), self.assertRaises(network.NetworkError):
                self.fetch(max_bytes=32)

    def test_valid_chunked_response_preserves_body_and_trailers(self):
        for body in (b"2\r\nok\r\n0\r\n\r\n", b"1;fixture=yes\r\no\r\n1\r\nk\r\n0\r\nX-Fixture: value\r\n\r\n"):
            self.responses = [b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + body]
            with self.subTest(body=body):
                self.assertEqual(self.fetch(max_bytes=2), (200, b"ok"))
                self.assertTrue(self.sockets[-1].closed)

    def test_invalid_chunk_separators_and_incomplete_terminators_fail(self):
        for body in (b"2\r\nokXX0\r\n\r\n", b"2\r\nok\r\n0\r\n",
                     b"2\r\nok\r\n0\r\n\n", b"2\r\nok\r\n0\r\nX-Fixture: synthetic-secret\r\n"):
            self.responses = [b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + body]
            before = len(self.sockets)
            with self.subTest(body=body):
                with self.assertRaises(network.NetworkError) as error:
                    self.fetch("http://public.example/?token=synthetic-secret", max_bytes=2)
                self.assertTrue(self.sockets[-1].closed)
                self.assertEqual(len(self.sockets), before + 1)
                self.assertNotIn("synthetic-secret", str(error.exception))
                self.assertNotIn("public.example", str(error.exception))

    def test_reserved_ipv6_and_public_peer_swap_are_not_allowed(self):
        for ip in ("::127.0.0.1", "2002:7f00:1::", "ff02::1"):
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                self.fetch(f"http://[{ip}]/")
        stream = WireSocket(response(), peer=("1.1.1.1", 80))
        with patch("socket.socket", return_value=stream), self.assertRaises(ValueError):
            self.fetch()
        self.assertEqual(stream.sent, b"")

    def test_private_destination_is_rejected_before_using_trusted_proxy(self):
        with patch.dict(os.environ, {"all_proxy": "http://127.0.0.1:9999"}), self.assertRaises(ValueError):
            self.fetch("http://private.example/")
        self.assertEqual(self.sockets, [])

    def test_post_bytes_and_public_ipv6_connection_are_preserved(self):
        self.assertEqual(self.fetch("http://[2606:4700:4700::1111]:8080/api?q=test", data=b'{"input":1}',
                                    headers={"Content-Type": "application/json"}, authenticated=True), (200, b"ok"))
        self.assertEqual(self.sockets[0].connected, ("2606:4700:4700::1111", 8080))
        self.assertIn(b"POST /api?q=test HTTP/1.1\r\n", self.sockets[0].sent)
        self.assertIn(b"Host: [2606:4700:4700::1111]:8080\r\n", self.sockets[0].sent)
        self.assertTrue(self.sockets[0].sent.endswith(b'\r\n\r\n{"input":1}'))

    def test_caller_caps_allow_manual_snapshot_but_reject_large_api_or_image(self):
        for cap in (2097152,):
            self.responses = [b"HTTP/1.1 200 OK\r\nContent-Length: 2097153\r\n\r\n"]
            with self.assertRaises(network.NetworkError):
                self.fetch(max_bytes=cap)
            self.assertEqual(self.sockets[-1].read_sizes, [])
        self.responses = [b"HTTP/1.1 200 OK\r\nContent-Length: 67108864\r\n\r\nshort"]
        with self.assertRaises(network.NetworkError):
            self.fetch(max_bytes=67108864)
        # The manual cap was accepted; the synthetic incomplete body was rejected.
        self.assertGreater(len(self.sockets[-1].read_sizes), 0)

    def test_ambiguous_or_unsupported_response_framing_is_rejected(self):
        for headers in (b"Transfer-Encoding: gzip\r\n", b"Transfer-Encoding: chunked\r\nContent-Length: 2\r\n"):
            self.responses = [b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n2\r\nok\r\n0\r\n\r\n"]
            with self.subTest(headers=headers), self.assertRaises(network.NetworkError):
                self.fetch()

    def test_expired_connect_budget_does_not_start_another_candidate(self):
        self.answers["public.example"] = ["8.8.8.8", "1.1.1.1"]
        clock = [100.0]
        created = []
        def socket_factory(*args):
            stream = WireSocket(response())
            def fail(address):
                clock[0] += 2
                raise OSError("synthetic connection timeout")
            stream.connect = fail
            created.append(stream)
            return stream
        with patch("time.monotonic", lambda: clock[0]), patch("socket.socket", socket_factory), self.assertRaises(network.NetworkError):
            network.connect_public(("public.example", 80), timeout=1.0)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)


if __name__ == "__main__":
    unittest.main()
