"""DNS services for browsing in the app.

The admin keeps the list on the "DNS services" page (admin_dns.py); the app reads it from `catalog`
(GET /app/v1/dns, no sign-in) the same way as the home shortcuts. Every phone lists them all, turned off.
Which ones a person turns on stays on their phone: nothing here knows or records it.
"""
import ipaddress
import re
import socket
import ssl
import struct
import time

import requests
from django.views.decorators.http import require_http_methods

from .home_shortcuts import versioned_json
from .models import DnsService

HTTPS, TLS, PLAIN = "https", "tls", "plain"
_HTTPS = re.compile(r"^https://[^\s/]+\.[^\s/]+/\S*$", re.I)
_HOST = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$", re.I)
_TEST_NAME = "example.com"
_QUERY_ID = 0x5359


def kind_of(address):
    """https, tls or plain, worked out from what was typed; None when it isn't a DNS address."""
    a = (address or "").strip()
    if _HTTPS.match(a):
        return HTTPS
    try:
        ipaddress.ip_address(a)
        return PLAIN
    except ValueError:
        pass
    return TLS if _HOST.match(a) and not re.fullmatch(r"[\d.]+", a) else None


def catalog_dict():
    return {"services": [
        {"id": s.id, "name": s.name, "address": s.address, "description": s.description}
        for s in DnsService.objects.filter(is_active=True)
    ]}


@require_http_methods(["GET"])
def catalog(request):
    return versioned_json(request, catalog_dict())


def _query():
    name = b"".join(bytes([len(p)]) + p.encode() for p in _TEST_NAME.split(".")) + b"\0"
    return struct.pack("!HHHHHH", _QUERY_ID, 0x0100, 1, 0, 0, 0) + name + struct.pack("!HH", 1, 1)


def _read_exact(stream, count):
    data = b""
    while len(data) < count:
        chunk = stream.recv(count - len(data))
        if not chunk:
            raise OSError("closed early")
        data += chunk
    return data


def _ask_https(address, query, timeout):
    resp = requests.post(address, data=query, timeout=timeout, headers={"Content-Type": "application/dns-message", "Accept": "application/dns-message"})
    resp.raise_for_status()
    return resp.content


def _ask_tls(address, query, timeout):
    context = ssl.create_default_context()
    with socket.create_connection((address, 853), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=address) as s:
            s.sendall(struct.pack("!H", len(query)) + query)
            size = struct.unpack("!H", _read_exact(s, 2))[0]
            return _read_exact(s, size)


def _ask_plain(address, query, timeout):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(query, (address, 53))
        return s.recv(4096)


def _ask(kind, address, query, timeout):
    return {HTTPS: _ask_https, TLS: _ask_tls, PLAIN: _ask_plain}[kind](address, query, timeout)


def is_answer(data):
    """A real DNS reply to our question. "No such name" counts: the service answered."""
    if len(data) < 12:
        return False
    answer_id, flags = struct.unpack("!HH", data[:4])
    return answer_id == _QUERY_ID and bool(flags & 0x8000) and (flags & 0x000F) in (0, 3)


def test_lookup(address, timeout=5):
    """Ask `address` about a known name. Returns (milliseconds, None), or (None, what went wrong)."""
    kind = kind_of(address)
    if not kind:
        return None, "That isn't a DNS address."
    started = time.perf_counter()
    try:
        data = _ask(kind, address.strip(), _query(), timeout)
    except requests.HTTPError as e:
        return None, f"It answered with an error (HTTP {e.response.status_code})."
    except (OSError, ssl.SSLError, ValueError) as e:
        return None, f"It didn't answer ({type(e).__name__})."
    if not is_answer(data):
        return None, "It answered, but not with a DNS reply."
    return round((time.perf_counter() - started) * 1000), None
