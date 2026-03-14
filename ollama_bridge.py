#!/usr/bin/env python3
"""
Minimal TCP bridge for Ollama.

Purpose:
- ngrok resolves `localhost` to IPv6 (`::1`) on this machine
- the fast local Ollama instance is reachable on IPv4 (`127.0.0.1`)
- this bridge lets ngrok connect to `localhost:<bridge_port>` while traffic is forwarded to `127.0.0.1:11434`
"""

from __future__ import annotations

import argparse
import asyncio


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target_host: str,
    target_port: int,
) -> None:
    upstream_reader, upstream_writer = await asyncio.open_connection(target_host, target_port)
    await asyncio.gather(
        pipe(client_reader, upstream_writer),
        pipe(upstream_reader, client_writer),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Bridge localhost IPv6 traffic to the IPv4 Ollama server")
    parser.add_argument("--listen-host", default="::1")
    parser.add_argument("--listen-port", type=int, default=11435)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, default=11434)
    args = parser.parse_args()

    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, args.target_host, args.target_port),
        host=args.listen_host,
        port=args.listen_port,
    )
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"Ollama bridge listening on {sockets} -> {args.target_host}:{args.target_port}")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())