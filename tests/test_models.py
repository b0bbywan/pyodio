"""Model parsing tests."""

from datetime import UTC, datetime

from pyodio.models import (
    BluetoothState,
    PlayerState,
    ServerInfo,
    ServiceState,
    TracklistState,
    UpgradeStatus,
)

from conftest import BLUETOOTH_STATE, PLAYER_SPOTIFY, SERVICE_MPD, TRACKLIST_SPOTIFY, UPGRADE_STATUS


def test_server_info():
    info = ServerInfo.from_dict(
        {
            "hostname": "box",
            "os_platform": "linux/arm64",
            "api_version": "1.0.0",
            "backends": {"mpris": True, "pulseaudio": True},
        }
    )
    assert info.hostname == "box"
    assert info.backends.mpris is True
    assert info.backends.bluetooth is False


def test_player_state_full():
    player = PlayerState.from_dict(PLAYER_SPOTIFY)
    assert player.bus_name == "org.mpris.MediaPlayer2.spotify"
    assert player.app_name == "spotify"
    assert player.is_playing
    assert player.title == "Song One"
    assert player.artist == "Some Artist"
    assert player.album == "Some Album"
    assert player.art_url == "https://example.org/cover.jpg"
    assert player.track_id == "/org/mpris/track/1"
    assert player.duration == 180_000_000
    assert player.position == 30_000_000
    assert player.position_updated_at == datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    assert player.capabilities.can_seek is True


def test_player_state_minimal():
    player = PlayerState.from_dict({"bus_name": "org.mpris.MediaPlayer2.vlc"})
    assert player.app_name == "vlc"
    assert player.playback_status == "Stopped"
    assert not player.is_playing
    assert player.title is None
    assert player.duration is None
    assert player.volume is None
    assert player.position_updated_at is None
    assert player.capabilities.can_play is False


def test_player_state_bad_length_metadata():
    player = PlayerState.from_dict({"bus_name": "x", "metadata": {"mpris:length": "not-a-number"}})
    assert player.duration is None


def test_player_state_tracklist_supported():
    assert PlayerState.from_dict(PLAYER_SPOTIFY).tracklist_supported is True
    assert PlayerState.from_dict({"bus_name": "x"}).tracklist_supported is False


def test_tracklist_state():
    tracklist = TracklistState.from_dict(TRACKLIST_SPOTIFY)
    assert tracklist.can_edit_tracks is True
    assert [t.track_id for t in tracklist.tracks] == ["/org/mpris/track/1", "/org/mpris/track/2"]
    first = tracklist.tracks[0]
    assert first.title == "Song One"
    assert first.artist == "Some Artist"
    assert first.duration == 180_000_000
    assert tracklist.tracks[1].duration is None


def test_tracklist_state_minimal():
    tracklist = TracklistState.from_dict({})
    assert tracklist.can_edit_tracks is False
    assert tracklist.tracks == []


def test_service_state_key():
    service = ServiceState.from_dict(SERVICE_MPD)
    assert service.key == "user/mpd.service"
    assert service.running is True


def test_bluetooth_state():
    state = BluetoothState.from_dict(BLUETOOTH_STATE)
    assert state.powered is True
    assert state.pairing_until is None
    assert state.known_devices[0].address == "AA:BB:CC:DD:EE:FF"
    assert state.known_devices[0].paired is True


def test_bluetooth_pairing_until():
    state = BluetoothState.from_dict({"pairing_active": True, "pairing_until": "2026-07-11T10:05:00Z"})
    assert state.pairing_active is True
    assert state.pairing_until == datetime(2026, 7, 11, 10, 5, tzinfo=UTC)


def test_upgrade_status():
    status = UpgradeStatus.from_dict(UPGRADE_STATUS)
    assert status.upgrade_available is True
    assert status.current == "1.0.0"
    assert status.latest == "1.1.0"
    assert status.run.state == "idle"
    assert not status.run.running
    assert status.can_upgrade is True


def test_upgrade_status_running():
    status = UpgradeStatus.from_dict(
        {"current": "1.0.0", "run": {"state": "running", "percent": 40, "step": "download"}}
    )
    assert status.run.running
    assert status.run.percent == 40
    assert status.run.step == "download"
