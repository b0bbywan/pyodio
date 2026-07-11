"""Low-level REST client tests against the fake server."""

import pytest

from pyodio import LoopStatus, OdioApiError, OdioClient, OdioConnectionError

from conftest import eventually  # noqa: F401  (fixture module import)


async def test_get_server_info(fake):
    async with OdioClient(fake.url) as client:
        info = await client.get_server_info()
    assert info.hostname == "odio-server"
    assert info.api_version == "1.2.3"
    assert info.backends.mpris is True


async def test_get_players(fake):
    async with OdioClient(fake.url) as client:
        players = await client.get_players()
    assert len(players) == 1
    assert players[0].title == "Song One"


async def test_player_commands_and_encoding(fake):
    async with OdioClient(fake.url) as client:
        await client.player_play("org.mpris.MediaPlayer2.spotify")
        await client.player_seek("org.mpris.MediaPlayer2.spotify", -5_000_000)
        await client.player_set_position("org.mpris.MediaPlayer2.spotify", 10, track_id="/t/1")
        await client.player_set_loop("org.mpris.MediaPlayer2.spotify", LoopStatus.TRACK)

    assert fake.requests[0] == ("POST", "/players/org.mpris.MediaPlayer2.spotify/play", None)
    assert fake.requests[1][2] == {"offset": -5_000_000}
    assert fake.requests[2][2] == {"position": 10, "track_id": "/t/1"}
    assert fake.requests[3][2] == {"loop": "Track"}


async def test_client_name_url_encoding(fake):
    async with OdioClient(fake.url) as client:
        await client.set_client_volume("Playback/Stream #1", 0.4)
    method, path, body = fake.requests[0]
    assert path == "/audio/clients/Playback%2FStream%20%231/volume"
    assert body == {"volume": 0.4}


async def test_mute_is_a_toggle_without_body(fake):
    async with OdioClient(fake.url) as client:
        await client.toggle_master_mute()
        await client.toggle_client_mute("Playback Stream")
        await client.toggle_output_mute("alsa_output.usb-DAC.analog-stereo")
    assert all(body is None for _, _, body in fake.requests)


async def test_volume_validation(fake):
    async with OdioClient(fake.url) as client:
        with pytest.raises(ValueError):
            await client.set_master_volume(1.5)
        with pytest.raises(ValueError):
            await client.set_output_volume("out", -0.1)
    assert fake.requests == []


async def test_api_error_mapping(fake):
    fake.fail["/power/reboot"] = (403, "reboot capability disabled")
    async with OdioClient(fake.url) as client:
        with pytest.raises(OdioApiError) as exc_info:
            await client.reboot()
    assert exc_info.value.status == 403
    assert "disabled" in exc_info.value.message


async def test_connection_error():
    async with OdioClient("http://127.0.0.1:1") as client:
        with pytest.raises(OdioConnectionError):
            await client.get_server_info()


async def test_get_audio_unified(fake):
    async with OdioClient(fake.url) as client:
        snapshot = await client.get_audio()
    assert snapshot.kind == "pipewire"
    assert snapshot.clients[0].name == "Playback Stream"
    assert len(snapshot.outputs) == 2


async def test_get_audio_legacy_fallback(fake):
    fake.legacy_audio = True
    async with OdioClient(fake.url) as client:
        snapshot = await client.get_audio()
    assert snapshot.kind == "pipewire"
    assert snapshot.clients[0].app == "Music Player Daemon"
    assert snapshot.outputs[0].default is True


async def test_get_upgrade_none(fake):
    fake.upgrade = None
    async with OdioClient(fake.url) as client:
        assert await client.get_upgrade() is None


async def test_service_action_validation(fake):
    async with OdioClient(fake.url) as client:
        with pytest.raises(ValueError):
            await client.service_action("user", "mpd.service", "explode")
        await client.service_restart("user", "mpd.service")
    assert fake.requests[0][1] == "/services/user/mpd.service/restart"


async def test_cover_url(fake):
    client = OdioClient(fake.url)
    url = client.player_cover_url("org.mpris.MediaPlayer2.spotify")
    assert url == f"{fake.url}/players/org.mpris.MediaPlayer2.spotify/cover"
    await client.close()
