"""HTTP server primitives for AdaMAST's localhost-only browser surfaces."""

from __future__ import annotations

from http.server import ThreadingHTTPServer
from socketserver import TCPServer


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    """Bind without HTTPServer's unnecessary reverse-DNS lookup.

    ``HTTPServer.server_bind`` calls ``socket.getfqdn`` after the socket is
    already bound. That lookup can stall on otherwise healthy macOS systems
    and is not useful for AdaMAST because every browser surface is loopback
    only.
    """

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)
