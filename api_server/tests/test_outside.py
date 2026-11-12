"""Addresses people gave, reached through Outside: public ones alone, each redirect checked again, a credential kept to
its own host, and every answer held to a size and a deadline, and decoded once (api-server.md §9)."""

from __future__ import annotations

import contextlib
import gzip
import json
import time

import pytest

from conftest import Answering, serving
from api_server.utils import Outside, OutsideRefused

BODY = json.dumps({"schema": 3, "agents": [{"id": f"a{n}"} for n in range(2000)]}).encode()


class Gzipped(Answering):
    """As raw.githubusercontent.com answers: gzip, with the encoded length."""

    def do_GET(self) -> None:
        self.answer(200, gzip.compress(BODY), {"Content-Encoding": "gzip"})


class Far(Answering):
    """Sends people on, answers large or slowly, and keeps what each request carried."""

    seen: list[tuple[str, str]] = []

    def do_GET(self) -> None:
        self.seen.append((self.path, self.headers.get("Authorization", "")))
        path, _, to = self.path.partition("?")
        if path == "/to":
            self.answer(302, headers={"Location": to})
        elif path == "/loop":
            self.answer(302, headers={"Location": "/loop"})
        elif path == "/big":
            self.answer(200, "x" * 100_000)
        elif path == "/drip":
            self.send_response(200)
            self.end_headers()
            with contextlib.suppress(OSError):
                for _ in range(20):
                    self.wfile.write(b"x")
                    time.sleep(0.1)
        else:
            self.answer(200, {})


def test_a_gzip_answer_is_read_decoded_and_not_decoded_again():
    with serving(Gzipped) as here:
        answer = Outside(10, 4 * len(BODY)).fetch("GET", f"{here}/catalog.json")
    assert answer.status_code == 200
    assert answer.content == BODY
    assert json.loads(answer.content)["agents"][-1] == {"id": "a1999"}
    assert "content-encoding" not in answer.headers


def test_only_a_public_address_is_reached_over_https_unless_the_operator_names_its_host():
    for inside in ("https://10.0.0.1/", "https://169.254.169.254/", "https://[::ffff:192.168.0.1]/",
                   "https://[64:ff9b::a00:1]/"):
        assert "inside this network" in Outside.refusal(inside), inside
    assert "over https" in Outside.refusal("http://93.184.215.14/")
    assert "no name or password" in Outside.refusal("https://ana:pw@93.184.215.14/")
    assert Outside.refusal("https://93.184.215.14/") == Outside.refusal("http://127.0.0.1:9/") == ""


def test_each_redirect_is_checked_again_and_a_credential_goes_to_its_own_host_alone():
    Far.seen = []
    with serving(Far) as here, serving(Far) as there:
        Outside(5, credential={"Authorization": "Bearer s"}).fetch("GET", f"{here}/to?{there}/seen")
        assert [(path.partition("?")[0], sent) for path, sent in Far.seen] == [("/to", "Bearer s"), ("/seen", "")]
        with pytest.raises(OutsideRefused, match="inside this network"):
            Outside(5).fetch("GET", f"{here}/to?https://10.0.0.1/")
        with pytest.raises(OutsideRefused, match="too many"):
            Outside(5).fetch("GET", f"{here}/loop")
    assert [path for path, _ in Far.seen].count("/loop") == Outside.REDIRECTS + 1


def test_an_answer_is_held_to_its_size_and_the_whole_call_to_its_deadline():
    with serving(Far) as here:
        with pytest.raises(OutsideRefused, match="more than 64 KiB"):
            Outside(5, 64 * 1024).fetch("GET", f"{here}/big")
        began = time.monotonic()
        with pytest.raises(OutsideRefused, match="too long"):
            Outside(0.5).fetch("GET", f"{here}/drip")
        assert time.monotonic() - began < 1.5
