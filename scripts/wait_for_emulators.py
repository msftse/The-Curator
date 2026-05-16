#!/usr/bin/env python
"""Poll the docker-compose emulator stack until each service is reachable.

Used by CI and `make up` so the next command (pytest, seed, etc.) doesn't
race the emulators on cold boot.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import time
from urllib.parse import urlparse

import urllib3

TIMEOUT_SECONDS = 180


def _check_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _check_cosmos(endpoint: str) -> bool:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        urllib.request.urlopen(endpoint, timeout=2, context=ctx)
    except urllib.error.HTTPError:
        # HTTP error responses still mean the port is alive.
        return True
    except Exception:
        return False
    return True


async def _check_redis(url: str) -> bool:
    try:
        from redis.asyncio import Redis

        r = Redis.from_url(url)
        try:
            return bool(await r.ping())
        finally:
            await r.aclose()
    except Exception:
        return False


async def main() -> int:
    cosmos_endpoint = os.getenv("COSMOS_ENDPOINT", "https://localhost:8081")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    azurite_host = os.getenv("AZURITE_HOST", "localhost")
    azurite_port = int(os.getenv("AZURITE_BLOB_PORT", "10000"))

    cosmos_parsed = urlparse(cosmos_endpoint)
    cosmos_host = cosmos_parsed.hostname or "localhost"
    cosmos_port = cosmos_parsed.port or 8081

    deadline = time.monotonic() + TIMEOUT_SECONDS
    status: dict[str, bool] = {"cosmos": False, "azurite": False, "redis": False}

    while time.monotonic() < deadline:
        if not status["cosmos"]:
            status["cosmos"] = _check_tcp(cosmos_host, cosmos_port) and _check_cosmos(
                cosmos_endpoint
            )
        if not status["azurite"]:
            status["azurite"] = _check_tcp(azurite_host, azurite_port)
        if not status["redis"]:
            status["redis"] = await _check_redis(redis_url)

        if all(status.values()):
            print("emulators ready:", status)
            return 0
        print("waiting:", status)
        await asyncio.sleep(2)

    print("timed out waiting for emulators:", status, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
