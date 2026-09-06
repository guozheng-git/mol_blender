"""Bounded HTTPS downloads without credentials, redirects to private networks, or DNS rebinding."""
from __future__ import annotations
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit, urljoin
from .jobs import JobError


def resolve_public(host):
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(item[4][0] for item in answers))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise JobError("File URL must resolve only to public network addresses.")
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=30, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        connection = socket.create_connection((self.address, 443), timeout=self.timeout)
        self.sock = self._context.wrap_socket(connection, server_hostname=self.host)


def download_file(url, *, max_bytes=50 * 1024**2):
    for _ in range(4):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443} or parsed.fragment:
            raise JobError("File URL must be HTTPS on port 443 without credentials or a fragment.")
        address = resolve_public(parsed.hostname)[0]
        connection = PinnedHTTPS(parsed.hostname, address)
        try:
            connection.request("GET", (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""), headers={"User-Agent": "BlenderRender/0.2", "Accept-Encoding":"identity"})
            response = connection.getresponse()
            if response.status in {301,302,303,307,308}:
                url = urljoin(url, response.getheader("Location", ""))
                continue
            if response.status != 200:
                raise JobError("The attached file could not be downloaded. Attach it again.", "file_unavailable", 422)
            length = response.getheader("Content-Length")
            if length and int(length) > max_bytes:
                raise JobError("File exceeds 50 MiB.", "too_large", 413)
            chunks, count = [], 0
            while chunk := response.read(65536):
                count += len(chunk)
                if count > max_bytes:
                    raise JobError("File exceeds 50 MiB.", "too_large", 413)
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            connection.close()
    raise JobError("Too many file download redirects.")
