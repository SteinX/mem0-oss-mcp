import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from mem0_oss_mcp import server


@contextmanager
def redirect_pair():
    observed = {"target_requests": 0}
    target_url = ""

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed["target_requests"] += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            return

    class SourceHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target_url)
            self.end_headers()

        def log_message(self, format, *args):
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    target_url = f"http://127.0.0.1:{target.server_port}/capture"
    source = ThreadingHTTPServer(("127.0.0.1", 0), SourceHandler)
    threads = [
        threading.Thread(target=httpd.serve_forever, daemon=True)
        for httpd in (target, source)
    ]
    for thread in threads:
        thread.start()
    try:
        yield (
            f"http://127.0.0.1:{source.server_port}",
            observed,
        )
    finally:
        for httpd in (source, target):
            httpd.shutdown()
        for thread in threads:
            thread.join(timeout=5)
        for httpd in (source, target):
            httpd.server_close()


@pytest.mark.parametrize("target", ["core", "sidecar"])
def test_data_plane_credentials_never_follow_redirects(target):
    with redirect_pair() as (source_url, observed):
        if target == "core":
            patches = (
                patch.object(server.Config, "base_url", source_url),
                patch.object(
                    server.Config,
                    "api_key",
                    "private-core-operator-key",
                ),
            )

            def call():
                return server._backend("GET", "/redirect")
        else:
            patches = (
                patch.object(
                    server.Config,
                    "sidecar_base_url",
                    source_url,
                ),
                patch.object(
                    server.Config,
                    "sidecar_api_key",
                    "private-sidecar-operator-key",
                ),
            )

            def call():
                return server._sidecar_backend("GET", "/redirect")

        with patches[0], patches[1], pytest.raises(server.BackendError) as captured:
            call()

    assert captured.value.status == 302
    assert observed["target_requests"] == 0
