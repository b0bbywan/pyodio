"""High-level OdioHub tests: live state, events, commands."""

from datetime import UTC, datetime, timedelta

import pyodio
from pyodio import OdioHub

from conftest import PLAYER_SPOTIFY, eventually


async def test_connect_populates_state(fake):
    async with pyodio.connect(fake.url) as odio:
        assert odio.server.hostname == "odio-server"
        assert odio.backends.mpris is True

        assert set(odio.players) == {"org.mpris.MediaPlayer2.spotify"}
        player = odio.players.find("spotify")
        assert player is not None
        assert player.title == "Song One"
        assert player.is_playing
        assert odio.players.playing == [player]

        assert odio.audio.kind == "pipewire"
        assert odio.audio.clients["Playback Stream"].app == "Music Player Daemon"
        assert odio.audio.default_output.name.startswith("alsa_output.pci")
        assert odio.audio.volume == 0.5

        assert odio.services.find("mpd.service").running is True
        assert odio.bluetooth.powered is True
        assert odio.bluetooth.devices["AA:BB:CC:DD:EE:FF"].paired is True
        assert odio.power.can_reboot and not odio.power.can_power_off
        assert odio.upgrade.available is True
        assert odio.upgrade.latest_version == "1.1.0"


async def test_await_style_connect(fake):
    odio = await pyodio.connect(fake.url)
    assert odio.server.hostname == "odio-server"
    await odio.close()


async def test_player_commands_and_cover(fake):
    async with pyodio.connect(fake.url) as odio:
        player = odio.players.find("Spotify")  # by identity, case-insensitive
        await player.play_pause()
        await player.set_position(5_000_000)  # track_id auto-filled
        assert player.cover_url == (
            f"{fake.url}/players/org.mpris.MediaPlayer2.spotify/cover"
            "?t=%2Forg%2Fmpris%2Ftrack%2F1&a=https%3A%2F%2Fexample.org%2Fcover.jpg"
        )

    assert ("POST", "/players/org.mpris.MediaPlayer2.spotify/play_pause", None) in fake.requests
    assert ("POST", "/players/org.mpris.MediaPlayer2.spotify/position",
            {"position": 5_000_000, "track_id": "/org/mpris/track/1"}) in fake.requests


async def test_player_events_update_state(fake):
    async with pyodio.connect(fake.url) as odio:
        await eventually(lambda: odio.connected)
        changes = []
        odio.players.on_change(lambda change, p: changes.append((change, p.bus_name)))

        paused = dict(PLAYER_SPOTIFY, playback_status="Paused")
        fake.push("player.updated", {"data": paused, "emitted_at": 1770000000000})
        await eventually(lambda: not odio.players.find("spotify").is_playing)

        fake.push("player.added", {"data": {"bus_name": "org.mpris.MediaPlayer2.vlc", "identity": "VLC"},
                                   "emitted_at": 1770000000000})
        await eventually(lambda: "org.mpris.MediaPlayer2.vlc" in odio.players)

        vlc = odio.players["org.mpris.MediaPlayer2.vlc"]
        fake.push("player.removed", {"bus_name": "org.mpris.MediaPlayer2.vlc"})
        await eventually(lambda: "org.mpris.MediaPlayer2.vlc" not in odio.players)
        assert vlc.available is False

        assert ("updated", "org.mpris.MediaPlayer2.spotify") in changes
        assert ("added", "org.mpris.MediaPlayer2.vlc") in changes
        assert ("removed", "org.mpris.MediaPlayer2.vlc") in changes


async def test_player_position_beacon(fake):
    async with pyodio.connect(fake.url) as odio:
        await eventually(lambda: odio.connected)
        emitted_ms = int(datetime.now(UTC).timestamp() * 1000)
        fake.push("player.position", [
            {"bus_name": "org.mpris.MediaPlayer2.spotify", "track_id": "/org/mpris/track/1",
             "position": 60_000_000, "emitted_at": emitted_ms},
        ])
        player = odio.players.find("spotify")
        await eventually(lambda: player.state.position == 60_000_000)
        # Extrapolated position is at or after the beacon, but within the track.
        assert 60_000_000 <= player.position <= player.duration


async def test_position_extrapolation_while_playing(fake):
    async with pyodio.connect(fake.url) as odio:
        player = odio.players.find("spotify")
        player.state.position = 30_000_000
        player.state.position_updated_at = datetime.now(UTC) - timedelta(seconds=10)
        assert 39_000_000 <= player.position <= 41_000_000

        player.state.playback_status = "Paused"
        assert player.position == 30_000_000


async def test_audio_events_and_set_muted(fake):
    async with pyodio.connect(fake.url) as odio:
        await eventually(lambda: odio.connected)
        client = odio.audio.clients["Playback Stream"]

        # set_muted is a no-op when already in the desired state
        await client.set_muted(False)
        assert not any(path.endswith("/mute") for _, path, _ in fake.requests)
        await client.set_muted(True)
        assert ("POST", "/audio/clients/Playback%20Stream/mute", None) in fake.requests

        fake.push("audio.updated", {"id": 7, "name": "Playback Stream", "muted": True, "volume": 0.6})
        await eventually(lambda: client.muted)

        fake.push("audio.output.updated", {"id": 2, "name": "alsa_output.usb-DAC.analog-stereo",
                                           "volume": 0.9, "default": True})
        fake.push("audio.output.updated", {"id": 1, "name": "alsa_output.pci-0000_00_1f.3.analog-stereo",
                                           "volume": 0.5, "default": False})
        await eventually(lambda: odio.audio.default_output.name == "alsa_output.usb-DAC.analog-stereo")
        assert odio.audio.volume == 0.9

        fake.push("audio.removed", {"name": "Playback Stream"})
        await eventually(lambda: "Playback Stream" not in odio.audio.clients)


async def test_master_set_muted_toggles(fake):
    async with pyodio.connect(fake.url) as odio:
        await odio.audio.set_muted(False)  # already unmuted → no request
        assert not any(path == "/audio/server/mute" for _, path, _ in fake.requests)
        await odio.audio.set_muted(True)
        assert ("POST", "/audio/server/mute", None) in fake.requests


async def test_service_events_and_commands(fake):
    async with pyodio.connect(fake.url) as odio:
        await eventually(lambda: odio.connected)
        service = odio.services.find("mpd.service")
        await service.restart()
        assert ("POST", "/services/user/mpd.service/restart", None) in fake.requests

        fake.push("service.updated", {"name": "mpd.service", "scope": "user", "running": False,
                                      "enabled": True, "exists": True})
        await eventually(lambda: not service.running)


async def test_bluetooth_events_and_commands(fake):
    async with pyodio.connect(fake.url) as odio:
        await eventually(lambda: odio.connected)
        fake.push("bluetooth.discovered", {"address": "11:22:33:44:55:66", "name": "New Speaker"})
        await eventually(lambda: "11:22:33:44:55:66" in odio.bluetooth.devices)

        device = odio.bluetooth.devices["11:22:33:44:55:66"]
        await device.connect()
        assert ("POST", "/bluetooth/connect", {"address": "11:22:33:44:55:66"}) in fake.requests

        fake.push("bluetooth.updated", dict(fake.bluetooth, scanning=True))
        await eventually(lambda: odio.bluetooth.scanning)


async def test_upgrade_progress_events(fake):
    async with pyodio.connect(fake.url) as odio:
        await eventually(lambda: odio.connected)
        progress = []
        odio.upgrade.on_change(lambda change, u: progress.append((change, u.progress_percent)))

        fake.push("upgrade.progress", {"event": "begin", "total": 5})
        await eventually(lambda: odio.upgrade.in_progress)
        fake.push("upgrade.progress", {"event": "progress", "percent": 60, "step": "install"})
        await eventually(lambda: odio.upgrade.progress_percent == 60)
        # Raw script end is not authoritative: the run stays in progress...
        fake.push("upgrade.progress", {"event": "end", "success": True})
        await eventually(lambda: odio.upgrade.progress_percent == 100)
        assert odio.upgrade.in_progress
        # ...until the lifecycle snapshot lands; terminal "idle" means success.
        fake.push("upgrade.info", {"state": "idle", "origin": "systemd",
                                   "finished_at": "2026-07-11T09:05:00Z"})
        await eventually(lambda: not odio.upgrade.in_progress)
        assert odio.upgrade.status.run.state == "idle"
        assert odio.upgrade.available is False
        assert odio.upgrade.current_version == "1.1.0"

        fake.push("upgrade.info", {"current": "1.1.0", "latest": "1.1.0", "upgrade_available": False})
        await eventually(lambda: odio.upgrade.status.checked_at is None)
        assert odio.upgrade.status.can_upgrade is True  # preserved from snapshot


async def test_upgrade_lifecycle_failure_keeps_availability(fake):
    async with pyodio.connect(fake.url) as odio:
        await eventually(lambda: odio.connected)
        log = []
        odio.upgrade.on_change(lambda change, u: log.append((change, u.progress_percent, u.status.run.state)))

        # A script run outside systemd is adopted from its socket stream:
        # same events, origin "socket".
        fake.push("upgrade.progress", {"event": "progress", "percent": 40, "step": "download"})
        await eventually(lambda: odio.upgrade.progress_percent == 40)
        # Lifecycle "running" keeps script-reported progress.
        fake.push("upgrade.info", {"state": "running", "origin": "socket"})
        fake.push("upgrade.info", {"state": "failed", "origin": "socket",
                                   "finished_at": "2026-07-11T09:05:00Z"})
        await eventually(lambda: not odio.upgrade.in_progress)
        assert ("updated", 40, "running") in log
        assert odio.upgrade.status.run.state == "failed"
        assert odio.upgrade.progress_percent is None
        assert odio.upgrade.available is True  # failed run changes nothing
        assert odio.upgrade.current_version == "1.0.0"


async def test_start_defers_sync_to_first_connect(fake):
    hub = OdioHub(fake.url)
    seen = []
    hub.on_connection_change(lambda c: seen.append((c, hub._server is not None)))
    await hub.start()
    await eventually(lambda: hub.connected)
    # Resync completed before the connection was reported.
    assert seen[0] == (True, True)
    assert hub.server.hostname == "odio-server"
    assert len(hub.players) == 1
    await hub.close()


async def test_start_survives_unreachable_server():
    hub = OdioHub("http://127.0.0.1:1")
    await hub.start()
    assert not hub.connected
    await hub.close()


async def test_backend_gating(fake):
    fake.server_info["backends"] = {"mpris": True, "pulseaudio": False, "systemd": False,
                                    "bluetooth": False, "power": False, "upgrade": False, "zeroconf": False}
    async with pyodio.connect(fake.url) as odio:
        assert len(odio.players) == 1
        assert odio.audio.server is None
        assert len(odio.services) == 0
        assert odio.bluetooth.state is None
        assert odio.power.capabilities is None
        assert odio.upgrade.status is None


async def test_hub_default_url_is_localhost():
    hub = OdioHub()
    assert hub.client.base_url == "http://localhost:8018"
    await hub.close()
