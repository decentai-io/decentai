"""Addresses people gave, reached through Outside: an answer as the server encoded it comes back decoded, once."""

from __future__ import annotations

import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from api_server.utils import Outside

BODY = json.dumps({"schema": 3, "agents": [{"id": f"a{n}"} for n in range(2000)]}).encode()


class Gzipped(BaseHTTPRequestHandler):
    """As raw.githubusercontent.com answers: gzip, with the encoded length."""

    def do_GET(self) -> None:
        packed = gzip.compress(BODY)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(packed)))
        self.end_headers()
        self.wfile.write(packed)

    def log_message(self, *_: object) -> None:
        pass


def test_a_gzip_answer_is_read_decoded_and_not_decoded_again():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Gzipped)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        answer = Outside(10, 4 * len(BODY)).fetch("GET", f"http://127.0.0.1:{server.server_port}/catalog.json")
    finally:
        server.shutdown()
    assert answer.status_code == 200
    assert answer.content == BODY
    assert json.loads(answer.content)["agents"][-1] == {"id": "a1999"}
    assert "content-encoding" not in answer.headers
