"""Test fixtures: a fake odio-api server with REST and SSE endpoints."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

PLAYER_SPOTIFY = {
    "bus_name": "org.mpris.MediaPlayer2.spotify",
    "identity": "Spotify",
    "playback_status": "Playing",
    "loop_status": "None",
    "shuffle": False,
    "volume": 0.8,
    "position": 30_000_000,
    "position_updated_at": "2026-07-11T10:00:00Z",
    "rate": 1.0,
    "metadata": {
        "xesam:title": "Song One",
        "xesam:artist": "Some Artist",
        "xesam:album": "Some Album",
        "mpris:length": "180000000",
        "mpris:trackid": "/org/mpris/track/1",
        "mpris:artUrl": "https://example.org/cover.jpg",
    },
    "capabilities": {
        "can_play": True,
        "can_pause": True,
        "can_go_next": True,
        "can_go_previous": True,
        "can_seek": True,
        "can_control": True,
    },
}

AUDIO_CLIENT_MPD = {
    "id": 7,
    "name": "Playback Stream",
    "app": "Music Player Daemon",
    "muted": False,
    "volume": 0.6,
    "corked": False,
    "backend": "pipewire",
    "binary": "mpd",
    "user": "bobby",
    "host": "odio-server",
    "props": {"application.process.host": "odio-server"},
}

AUDIO_OUTPUT_ALSA = {
    "id": 1,
    "name": "alsa_output.pci-0000_00_1f.3.analog-stereo",
    "description": "Built-in Audio Analog Stereo",
    "muted": False,
    "volume": 0.5,
    "state": "RUNNING",
    "default": True,
    "props": {},
}

AUDIO_OUTPUT_USB = {
    "id": 2,
    "name": "alsa_output.usb-DAC.analog-stereo",
    "description": "USB DAC Analog Stereo",
    "muted": True,
    "volume": 0.9,
    "state": "IDLE",
    "default": False,
    "props": {},
}

SERVICE_MPD = {
    "name": "mpd.service",
    "scope": "user",
    "active_state": "active",
    "running": True,
    "enabled": True,
    "exists": True,
    "description": "Music Player Daemon",
}

BLUETOOTH_STATE = {
    "powered": True,
    "discoverable": False,
    "pairable": False,
    "pairing_active": False,
    "scanning": False,
    "known_devices": [
        {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "Big Speaker",
            "paired": True,
            "bonded": True,
            "trusted": True,
            "connected": False,
        }
    ],
}

UPGRADE_STATUS = {
    "current": "1.0.0",
    "latest": "1.1.0",
    "upgrade_available": True,
    "checked_at": "2026-07-11T09:00:00Z",
    "run": {"state": "idle"},
    "can_check": True,
    "can_upgrade": True,
}


class FakeOdio:
    """In-memory odio-api double: REST state, request recorder, SSE push."""

    def __init__(self) -> None:
        self.url = ""
        self.legacy_audio = False
        self.fail: dict[str, tuple[int, str]] = {}
        self.requests: list[tuple[str, str, Any]] = []
        self._sse_queues: list[asyncio.Queue[tuple[str, Any] | None]] = []

        self.server_info = {
            "hostname": "odio-server",
            "os_platform": "linux/amd64",
            "os_version": "Fedora Linux 44",
            "api_sw": "odio-api",
            "api_version": "1.2.3",
            "backends": {
                "bluetooth": True,
                "mpris": True,
                "power": True,
                "pulseaudio": True,
                "systemd": True,
                "upgrade": True,
                "zeroconf": True,
            },
        }
        self.power = {"reboot": True, "power_off": False}
        self.players = [dict(PLAYER_SPOTIFY)]
        self.audio_server = {"kind": "pipewire", "default_sink": AUDIO_OUTPUT_ALSA["name"], "volume": 0.5, "muted": False}
        self.audio_clients = [dict(AUDIO_CLIENT_MPD)]
        self.audio_outputs = [dict(AUDIO_OUTPUT_ALSA), dict(AUDIO_OUTPUT_USB)]
        self.services = [dict(SERVICE_MPD)]
        self.bluetooth = dict(BLUETOOTH_STATE)
        self.upgrade: dict[str, Any] | None = dict(UPGRADE_STATUS)

    # SSE ------------------------------------------------------------------
    def push(self, event_type: str, data: Any) -> None:
        for queue in self._sse_queues:
            queue.put_nowait((event_type, data))

    def drop_sse(self) -> None:
        """Close all live SSE connections (simulates a server restart)."""
        for queue in self._sse_queues:
            queue.put_nowait(None)

    @property
    def sse_client_count(self) -> int:
        return len(self._sse_queues)

    # App ------------------------------------------------------------------
    def make_app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get("/server", self._json(lambda: self.server_info))
        r.add_get("/power", self._json(lambda: self.power))
        r.add_get("/players", self._json(lambda: self.players))
        r.add_get("/audio", self._audio_handler)
        r.add_get("/audio/server", self._json(lambda: self.audio_server))
        r.add_get("/audio/clients", self._json(lambda: self.audio_clients))
        r.add_get("/audio/outputs", self._json(lambda: self.audio_outputs))
        r.add_get("/services", self._json(lambda: self.services))
        r.add_get("/bluetooth", self._json(lambda: self.bluetooth))
        r.add_get("/bluetooth/devices", self._json(lambda: self.bluetooth["known_devices"]))
        r.add_get("/upgrade", self._json(lambda: self.upgrade))
        r.add_get("/events", self._sse_handler)
        r.add_route("POST", "/{tail:.+}", self._post_handler)
        return app

    def _json(self, supplier):  # type: ignore[no-untyped-def]
        async def handler(request: web.Request) -> web.Response:
            return web.json_response(supplier())

        return handler

    async def _audio_handler(self, request: web.Request) -> web.Response:
        if self.legacy_audio:
            return web.Response(status=404, text="404 page not found")
        return web.json_response(
            {"kind": self.audio_server["kind"], "clients": self.audio_clients, "outputs": self.audio_outputs}
        )

    async def _post_handler(self, request: web.Request) -> web.Response:
        text = await request.text()
        body = json.loads(text) if text else None
        path = request.rel_url.raw_path
        self.requests.append(("POST", path, body))
        if path in self.fail:
            status, message = self.fail[path]
            return web.Response(status=status, text=message)
        return web.Response(status=202)

    async def _sse_handler(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await resp.prepare(request)
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
        self._sse_queues.append(queue)
        await resp.write(b'event: server.info\ndata: "connected"\n\n')
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event_type, data = item
                await resp.write(f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode())
        finally:
            self._sse_queues.remove(queue)
        return resp


@pytest.fixture
async def fake() -> FakeOdio:
    fake = FakeOdio()
    server = TestServer(fake.make_app())
    await server.start_server()
    fake.url = str(server.make_url("/")).rstrip("/")
    yield fake
    fake.drop_sse()
    await server.close()


async def eventually(predicate, timeout: float = 2.0) -> None:
    """Wait until predicate() is truthy, or fail."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met in time")
