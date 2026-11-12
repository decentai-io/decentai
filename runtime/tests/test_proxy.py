"""The proxy, on loopback: an HTTP proxy's tunnel and plain request, SOCKS5 over TCP and UDP, each request decided by
the caller before anything is looked up, and only the addresses checked reached (proxy.py)."""

import asyncio
import socket

from runtime.controllers.proxy import Proxy


class Local(Proxy):
    """The proxy with this machine counted as public, so a test's hosts on loopback can be reached."""

    looked: list[str] = []

    @classmethod
    def public(cls, address: str) -> bool:
        return True

    @staticmethod
    async def lookup(host: str, port: int) -> list[str]:
        Local.looked.append(host)
        return ["127.0.0.1"]


async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """A host that says back what it is sent: for a plain request, the request as it came."""
    while data := await reader.read(4096):
        writer.write(data)
        await writer.drain()
    writer.close()


async def opened(proxy: Proxy) -> tuple[int, int]:
    """The proxy served on loopback, for the source "p1", and a host that says back what it is sent: their ports."""
    server = await asyncio.start_server(lambda r, w: proxy.serve(r, w, "p1"), "127.0.0.1", 0)
    host = await asyncio.start_server(echo, "127.0.0.1", 0)
    return server.sockets[0].getsockname()[1], host.sockets[0].getsockname()[1]


async def allowed(source, protocol, host, port):
    return None


async def test_a_tunnel_is_decided_then_carried_both_ways():
    asked = []

    async def decide(source, protocol, host, port):
        asked.append((source, protocol, host, port))
        return None

    port, host = await opened(Local(decide))
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"CONNECT Example.COM.:{host} HTTP/1.1\r\n\r\n".encode())
    assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
    writer.write(b"ping")
    assert await reader.read(4) == b"ping"
    assert asked == [("p1", "tcp", "example.com", host)]  # names compared without case or a final dot
    writer.close()


async def test_a_refused_name_is_answered_with_why_and_never_looked_up():
    async def decide(source, protocol, host, port):
        return "secret.example.com is not a host this agent declared."

    Local.looked.clear()
    port, _ = await opened(Local(decide))
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"CONNECT secret.example.com:443 HTTP/1.1\r\n\r\n")
    assert (await reader.readline()).startswith(b"HTTP/1.1 403 secret.example.com is not a host")
    assert Local.looked == []
    writer.close()


async def test_only_public_addresses_are_reached_whatever_the_caller_says():
    class Inside(Proxy):
        @staticmethod
        async def lookup(host: str, port: int) -> list[str]:
            return ["10.0.0.5", "8.8.8.8"]

    port, _ = await opened(Inside(allowed))
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"CONNECT metadata.example.com:80 HTTP/1.1\r\n\r\n")
    assert b"403 metadata.example.com is not a public address" in await reader.readline()
    for address, public in (("169.254.169.254", False), ("::ffff:10.0.0.1", False), ("64:ff9b::a00:1", False),
                            ("127.0.0.1", False), ("8.8.8.8", True), ("64:ff9b::808:808", True)):
        assert Proxy.public(address) is public, address
    writer.close()


async def test_a_plain_request_goes_with_its_host_checked_and_closes_after():
    port, host = await opened(Local(allowed))
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET http://example.com:{host}/a?b=1 HTTP/1.1\r\nHost: elsewhere.com\r\n"
                 f"Proxy-Connection: keep-alive\r\nAccept: */*\r\n\r\n".encode())
    sent = await reader.read(4096)
    assert sent.startswith(b"GET /a?b=1 HTTP/1.1\r\n")
    assert f"Host: example.com:{host}\r\n".encode() in sent and b"Connection: close\r\n" in sent
    assert b"elsewhere" not in sent and b"Proxy-Connection" not in sent and b"Accept: */*" in sent
    writer.close()


async def test_socks5_carries_a_tcp_connection():
    port, host = await opened(Local(allowed))
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"\x05\x01\x00")
    assert await reader.readexactly(2) == b"\x05\x00"
    name = b"example.com"
    writer.write(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + host.to_bytes(2, "big"))
    assert (await reader.readexactly(10))[:2] == b"\x05\x00"
    writer.write(b"ping")
    assert await reader.read(4) == b"ping"
    writer.close()


async def test_socks5_relays_udp_to_a_host_and_its_answers_back():
    proxy = Local(allowed)
    port, _ = await opened(proxy)
    loop = asyncio.get_running_loop()
    far = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    far.bind(("127.0.0.1", 0))
    far.setblocking(False)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"\x05\x01\x00")
    await reader.readexactly(2)
    writer.write(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")
    reply = await reader.readexactly(10)
    relay = ("127.0.0.1", int.from_bytes(reply[8:10], "big"))
    near = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    near.bind(("127.0.0.1", 0))
    near.setblocking(False)
    name = b"example.com"
    header = b"\x00\x00\x00\x03" + bytes([len(name)]) + name + far.getsockname()[1].to_bytes(2, "big")
    await loop.sock_sendto(near, header + b"ping", relay)
    data, came = await asyncio.wait_for(loop.sock_recvfrom(far, 1024), 5)
    assert data == b"ping"
    await loop.sock_sendto(far, b"pong", came)
    back, _ = await asyncio.wait_for(loop.sock_recvfrom(near, 1024), 5)
    assert back.endswith(b"pong") and back[:4] == b"\x00\x00\x00\x01"
    (row,) = proxy.taken("p1")
    assert (row["host"], row["connections"], row["bytes_out"], row["bytes_in"]) == ("example.com", 1, 4, 4)
    writer.close()


async def test_what_a_source_reached_is_counted_by_host_and_port_until_taken():
    async def decide(source, protocol, host, port):
        return "Not declared." if host == "secret.example.com" else None

    proxy = Local(decide)
    port, host = await opened(proxy)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"CONNECT example.com:{host} HTTP/1.1\r\n\r\n".encode())
    await reader.readuntil(b"\r\n\r\n")
    writer.write(b"ping!")
    assert await reader.readexactly(5) == b"ping!"
    writer.close()
    for _ in range(2):
        refused_reader, refused = await asyncio.open_connection("127.0.0.1", port)
        refused.write(b"CONNECT secret.example.com:443 HTTP/1.1\r\n\r\n")
        await refused_reader.readline()
        refused.close()
    rows = {(row["host"], row["port"]): row for row in proxy.taken("p1")}
    reached, secret = rows[("example.com", host)], rows[("secret.example.com", 443)]
    assert (reached["connections"], reached["refused"], reached["bytes_out"], reached["bytes_in"]) == (1, 0, 5, 5)
    assert (secret["connections"], secret["refused"], secret["bytes_out"]) == (0, 2, 0)
    assert reached["first_at"] <= reached["last_at"] and proxy.taken("p1") == []


async def test_a_connection_allowed_until_a_future_closes_when_it_is_done():
    until = asyncio.get_running_loop().create_future()

    async def decide(source, protocol, host, port):
        return until

    port, host = await opened(Local(decide))
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"CONNECT example.com:{host} HTTP/1.1\r\n\r\n".encode())
    await reader.readuntil(b"\r\n\r\n")
    until.set_result(True)
    assert await asyncio.wait_for(reader.read(), 5) == b""
    writer.close()


async def test_a_source_has_at_most_its_connections_open():
    port, host = await opened(Local(allowed, most=1))
    first_reader, first = await asyncio.open_connection("127.0.0.1", port)
    first.write(f"CONNECT example.com:{host} HTTP/1.1\r\n\r\n".encode())
    await first_reader.readuntil(b"\r\n\r\n")
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"CONNECT example.com:{host} HTTP/1.1\r\n\r\n".encode())
    assert b"403 This source has as many connections open as it may." in await reader.readline()
    first.close()
    writer.close()
