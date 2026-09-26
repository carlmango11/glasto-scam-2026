import json
import socket
import subprocess
import time
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from .render import ASSETS


class DashboardHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, manifest, ports, **kwargs):
        self.manifest = manifest
        self.ports = ports
        super().__init__(*args, **kwargs)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if self.headers.get("Host") not in {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }:
            self.send_error(403)
            return
        if path == "/fleet.json":
            self.respond(json.dumps(self.manifest).encode(), "application/json")
        elif path == "/health":
            status = {}
            opener = build_opener(ProxyHandler({}))
            for worker, port in self.ports.items():
                try:
                    with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                        status[str(worker)] = response.status == 200
                except (OSError, URLError):
                    status[str(worker)] = False
            self.respond(json.dumps(status).encode(), "application/json")
        elif path in {"/", "/app.js", "/style.css"}:
            name = "index.html" if path == "/" else path[1:]
            mime = {
                "index.html": "text/html",
                "app.js": "text/javascript",
                "style.css": "text/css",
            }[name]
            self.respond((ASSETS / "web" / name).read_bytes(), mime)
        else:
            self.send_error(404)

    def respond(self, body, mime):
        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; frame-src http://127.0.0.1:*; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(directory: Path, workers: list[dict], manifest: dict, port: int, open_browser: bool):
    from .cli import ssh_arguments

    if not 1024 <= port <= 65535:
        raise ValueError("Dashboard port must be between 1024 and 65535")
    if any(w["state"] != "running" or not w["public_ip"] for w in workers):
        raise ValueError("All workers must be running; use start, then check status")
    processes = []
    logs = []
    ports = {}
    try:
        for worker in workers:
            local_port = available_port()
            ports[worker["index"]] = local_port
            arguments = ssh_arguments(directory, worker["public_ip"])
            log = (directory / f"tunnel-{worker['index']:03d}.log").open("w")
            logs.append(log)
            process = subprocess.Popen(
                [
                    *arguments[:-1],
                    "-N",
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-o",
                    "ServerAliveInterval=15",
                    "-o",
                    "ServerAliveCountMax=3",
                    "-L",
                    f"127.0.0.1:{local_port}:127.0.0.1:8080",
                    arguments[-1],
                ],
                stdout=subprocess.DEVNULL,
                stderr=log,
            )
            processes.append(process)
        for browser in manifest["browsers"]:
            browser["url"] = (
                f"http://127.0.0.1:{ports[browser['worker']]}{browser['viewer_path']}/vnc.html"
                f"?autoconnect=true&resize=remote&reconnect=true&path={browser['viewer_path'].lstrip('/')}/websockify"
            )
        handler = partial(DashboardHandler, manifest=manifest, ports=ports)
        with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
            server.timeout = 1
            time.sleep(1)
            if any(process.poll() is not None for process in processes):
                raise ValueError(f"An SSH tunnel failed; inspect {directory}/tunnel-*.log")
            url = f"http://127.0.0.1:{port}"
            print(
                f"Desktop switcher: {url}\nKeep this command running. Ctrl+C disconnects viewers; remote browsers keep running."
            )
            if open_browser:
                webbrowser.open(url)
            while True:
                server.handle_request()
                if any(process.poll() is not None for process in processes):
                    raise ValueError(
                        f"An SSH tunnel disconnected. Run open again; inspect {directory}/tunnel-*.log"
                    )
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()
