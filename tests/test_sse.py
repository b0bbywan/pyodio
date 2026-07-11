"""SSE parsing and EventStream supervision tests."""

import asyncio

import pyodio.sse
from pyodio import EventStream, OdioClient, stream_events

from conftest import eventually


async def test_stream_events_parses_wire_format(fake):
    async with OdioClient(fake.url) as client:
        events = []

        async def consume():
            async for event in stream_events(client, keepalive=30):
                events.append(event)
                if len(events) >= 3:
                    return

        task = asyncio.create_task(consume())
        await eventually(lambda: fake.sse_client_count == 1)
        fake.push("player.removed", {"bus_name": "org.mpris.MediaPlayer2.vlc"})
        fake.push("service.updated", {"name": "mpd.service", "scope": "user", "running": False})
        await asyncio.wait_for(task, timeout=2)

    assert events[0].type == "server.info"
    assert events[0].data == "connected"
    assert events[1].type == "player.removed"
    assert events[1].data == {"bus_name": "org.mpris.MediaPlayer2.vlc"}
    assert events[2].data["running"] is False


async def test_event_stream_dispatch_and_connection(fake):
    async with OdioClient(fake.url) as client:
        stream = EventStream(client)
        received = []
        connections = []
        stream.add_event_listener(received.append)
        stream.add_connection_listener(connections.append)

        await stream.start()
        await eventually(lambda: stream.connected)
        fake.push("power.action", {"action": "reboot"})
        await eventually(lambda: any(e.type == "power.action" for e in received))
        await stream.stop()

    assert connections == [True, False]


async def test_event_stream_reconnects(fake, monkeypatch):
    monkeypatch.setattr(pyodio.sse, "RECONNECT_MIN_INTERVAL", 0.05)
    resyncs = []

    async def on_connected():
        resyncs.append(True)

    async with OdioClient(fake.url) as client:
        stream = EventStream(client, on_connected=on_connected)
        await stream.start()
        await eventually(lambda: stream.connected)

        fake.drop_sse()
        await eventually(lambda: not stream.connected)
        await eventually(lambda: stream.connected, timeout=3)

        fake.push("power.action", {"action": "reboot"})
        received = []
        stream.add_event_listener(received.append)
        await eventually(lambda: len(received) > 0)
        await stream.stop()

    assert len(resyncs) == 2
