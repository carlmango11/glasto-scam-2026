"""Controlled SOCKS5/CONNECT endpoint. Only DNS DoH goes to the real internet."""

import base64
import json
import os
import select
import socket
import socketserver
import ssl
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

events = []
lock = threading.Lock()


def read_exact(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise EOFError()
        data += chunk
    return data


class HTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            json.dumps(events).encode() if self.path == "/events" else b"controlled destination\n"
        )
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(15)
        try:
            first = read_exact(self.request, 1)
            if first == b"\x05":
                read_exact(self.request, read_exact(self.request, 1)[0])
                self.request.sendall(b"\x05\x02")
                read_exact(self.request, 1)
                username = read_exact(self.request, read_exact(self.request, 1)[0]).decode()
                password = read_exact(self.request, read_exact(self.request, 1)[0]).decode()
                if password != "test-password":
                    self.request.sendall(b"\x01\x01")
                    return
                self.request.sendall(b"\x01\x00")
                version, command, _, address_type = read_exact(self.request, 4)
                if command != 1:
                    return
                if address_type == 1:
                    host = socket.inet_ntoa(read_exact(self.request, 4))
                elif address_type == 3:
                    host = read_exact(self.request, read_exact(self.request, 1)[0]).decode()
                else:
                    host = socket.inet_ntop(socket.AF_INET6, read_exact(self.request, 16))
                port = struct.unpack("!H", read_exact(self.request, 2))[0]
                response = b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"
            else:
                request = first
                while not request.endswith(b"\r\n\r\n"):
                    request += read_exact(self.request, 1)
                lines = request.decode().split("\r\n")
                method, target, _ = lines[0].split()
                if method != "CONNECT":
                    return
                host, port_text = target.rsplit(":", 1)
                port = int(port_text)
                auth = next(
                    line.split(": ", 1)[1]
                    for line in lines
                    if line.lower().startswith("proxy-authorization:")
                )
                username, password = base64.b64decode(auth.split()[1]).decode().split(":", 1)
                if password != "test-password":
                    return
                response = b"HTTP/1.1 200 Connection established\r\n\r\n"
            with lock:
                events.append({"username": username, "host": host, "port": port})
            destination = (host, port) if (host, port) == ("1.1.1.1", 443) else ("127.0.0.1", 8000)
            with socket.create_connection(destination, timeout=10) as remote:
                self.request.sendall(response)
                while True:
                    readable, _, _ = select.select([remote, self.request], [], [], 15)
                    if not readable:
                        return
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        (self.request if source is remote else remote).sendall(data)
        except (OSError, EOFError, ValueError, StopIteration):
            return


class ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


threading.Thread(
    target=ThreadingHTTPServer(("0.0.0.0", 8000), HTTPHandler).serve_forever, daemon=True
).start()
server = ProxyServer(("0.0.0.0", 1080), ProxyHandler)
if os.environ.get("TEST_TLS"):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/test/cert.pem", "/test/key.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
