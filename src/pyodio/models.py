"""Typed models mirroring the odio-api JSON payloads.

Every model parses tolerantly with :meth:`from_dict`: unknown keys are
ignored and missing keys fall back to defaults, so newer or older servers
never break parsing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

MPRIS_BUS_PREFIX = "org.mpris.MediaPlayer2."


class PlaybackStatus(StrEnum):
    """MPRIS playback status values."""

    PLAYING = "Playing"
    PAUSED = "Paused"
    STOPPED = "Stopped"


class LoopStatus(StrEnum):
    """MPRIS loop status values."""

    NONE = "None"
    TRACK = "Track"
    PLAYLIST = "Playlist"


class ServiceScope(StrEnum):
    """systemd unit scopes exposed by odio-api."""

    SYSTEM = "system"
    USER = "user"


class UpgradeRunStateValue(StrEnum):
    """States of an upgrade run."""

    IDLE = "idle"
    RUNNING = "running"
    FAILED = "failed"


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an RFC3339 timestamp, returning None on absence or garbage."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _millis_to_datetime(value: Any) -> datetime | None:
    """Convert unix milliseconds (as emitted by SSE envelopes) to datetime."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


@dataclass(slots=True)
class Backends:
    """Which odio-api backends are enabled on the server."""

    bluetooth: bool = False
    mpris: bool = False
    power: bool = False
    pulseaudio: bool = False
    systemd: bool = False
    upgrade: bool = False
    zeroconf: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Backends:
        return cls(
            bluetooth=bool(data.get("bluetooth", False)),
            mpris=bool(data.get("mpris", False)),
            power=bool(data.get("power", False)),
            pulseaudio=bool(data.get("pulseaudio", False)),
            systemd=bool(data.get("systemd", False)),
            upgrade=bool(data.get("upgrade", False)),
            zeroconf=bool(data.get("zeroconf", False)),
        )


@dataclass(slots=True)
class ServerInfo:
    """Response of ``GET /server``."""

    hostname: str = ""
    os_platform: str = ""
    os_version: str = ""
    api_sw: str = ""
    api_version: str = ""
    backends: Backends = field(default_factory=Backends)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServerInfo:
        return cls(
            hostname=data.get("hostname", ""),
            os_platform=data.get("os_platform", ""),
            os_version=data.get("os_version", ""),
            api_sw=data.get("api_sw", ""),
            api_version=data.get("api_version", ""),
            backends=Backends.from_dict(data.get("backends") or {}),
        )


@dataclass(slots=True)
class PowerCapabilities:
    """Response of ``GET /power``."""

    reboot: bool = False
    power_off: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PowerCapabilities:
        return cls(
            reboot=bool(data.get("reboot", False)),
            power_off=bool(data.get("power_off", False)),
        )


@dataclass(slots=True)
class PlayerCapabilities:
    """MPRIS capabilities of a player."""

    can_play: bool = False
    can_pause: bool = False
    can_go_next: bool = False
    can_go_previous: bool = False
    can_seek: bool = False
    can_control: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlayerCapabilities:
        return cls(
            can_play=bool(data.get("can_play", False)),
            can_pause=bool(data.get("can_pause", False)),
            can_go_next=bool(data.get("can_go_next", False)),
            can_go_previous=bool(data.get("can_go_previous", False)),
            can_seek=bool(data.get("can_seek", False)),
            can_control=bool(data.get("can_control", False)),
        )


@dataclass(slots=True)
class PlayerState:
    """State snapshot of an MPRIS player.

    Positions and durations are in microseconds, matching MPRIS.
    ``metadata`` values are always strings (the server flattens them).
    """

    bus_name: str
    identity: str = ""
    playback_status: str = PlaybackStatus.STOPPED
    loop_status: str | None = None
    shuffle: bool | None = None
    volume: float | None = None
    position: int | None = None
    position_updated_at: datetime | None = None
    rate: float | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    capabilities: PlayerCapabilities = field(default_factory=PlayerCapabilities)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlayerState:
        return cls(
            bus_name=data.get("bus_name", ""),
            identity=data.get("identity", ""),
            playback_status=data.get("playback_status", PlaybackStatus.STOPPED),
            loop_status=data.get("loop_status"),
            shuffle=data.get("shuffle"),
            volume=data.get("volume"),
            position=data.get("position"),
            position_updated_at=_parse_datetime(data.get("position_updated_at")),
            rate=data.get("rate"),
            metadata=dict(data.get("metadata") or {}),
            capabilities=PlayerCapabilities.from_dict(data.get("capabilities") or {}),
        )

    @property
    def app_name(self) -> str:
        """Short application name derived from the MPRIS bus name."""
        if self.bus_name.startswith(MPRIS_BUS_PREFIX):
            return self.bus_name[len(MPRIS_BUS_PREFIX):]
        return self.bus_name

    @property
    def is_playing(self) -> bool:
        return self.playback_status == PlaybackStatus.PLAYING

    @property
    def title(self) -> str | None:
        return self.metadata.get("xesam:title")

    @property
    def artist(self) -> str | None:
        return self.metadata.get("xesam:artist")

    @property
    def album(self) -> str | None:
        return self.metadata.get("xesam:album")

    @property
    def art_url(self) -> str | None:
        return self.metadata.get("mpris:artUrl")

    @property
    def track_id(self) -> str | None:
        return self.metadata.get("mpris:trackid")

    @property
    def duration(self) -> int | None:
        """Track length in microseconds, from ``mpris:length`` metadata."""
        raw = self.metadata.get("mpris:length")
        if raw is None:
            return None
        try:
            return int(float(raw))
        except ValueError:
            return None


@dataclass(slots=True)
class AudioServerState:
    """Response of ``GET /audio/server`` — the master volume/mute state."""

    kind: str = ""
    default_sink: str = ""
    volume: float = 0.0
    muted: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AudioServerState:
        return cls(
            kind=data.get("kind", ""),
            default_sink=data.get("default_sink", ""),
            volume=float(data.get("volume", 0.0)),
            muted=bool(data.get("muted", False)),
        )


@dataclass(slots=True)
class AudioClientState:
    """A per-application audio stream (PulseAudio/PipeWire sink input)."""

    id: int = 0
    name: str = ""
    app: str = ""
    muted: bool = False
    volume: float = 0.0
    corked: bool = False
    backend: str = ""
    binary: str = ""
    user: str = ""
    host: str = ""
    props: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AudioClientState:
        return cls(
            id=int(data.get("id", 0)),
            name=data.get("name", ""),
            app=data.get("app", ""),
            muted=bool(data.get("muted", False)),
            volume=float(data.get("volume", 0.0)),
            corked=bool(data.get("corked", False)),
            backend=data.get("backend", ""),
            binary=data.get("binary", ""),
            user=data.get("user", ""),
            host=data.get("host", ""),
            props=dict(data.get("props") or {}),
        )


@dataclass(slots=True)
class AudioOutputState:
    """An audio output device (PulseAudio/PipeWire sink)."""

    id: int = 0
    name: str = ""
    description: str = ""
    nick: str = ""
    muted: bool = False
    volume: float = 0.0
    state: str = ""
    default: bool = False
    driver: str = ""
    active_port: str = ""
    is_network: bool = False
    props: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AudioOutputState:
        return cls(
            id=int(data.get("id", 0)),
            name=data.get("name", ""),
            description=data.get("description", ""),
            nick=data.get("nick", ""),
            muted=bool(data.get("muted", False)),
            volume=float(data.get("volume", 0.0)),
            state=data.get("state", ""),
            default=bool(data.get("default", False)),
            driver=data.get("driver", ""),
            active_port=data.get("active_port", ""),
            is_network=bool(data.get("is_network", False)),
            props=dict(data.get("props") or {}),
        )


@dataclass(slots=True)
class AudioSnapshot:
    """Response of ``GET /audio`` — the unified audio state."""

    kind: str = ""
    clients: list[AudioClientState] = field(default_factory=list)
    outputs: list[AudioOutputState] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AudioSnapshot:
        return cls(
            kind=data.get("kind", ""),
            clients=[AudioClientState.from_dict(c) for c in data.get("clients") or []],
            outputs=[AudioOutputState.from_dict(o) for o in data.get("outputs") or []],
        )


@dataclass(slots=True)
class ServiceState:
    """A systemd unit exposed by odio-api."""

    name: str = ""
    scope: str = ServiceScope.USER
    active_state: str = ""
    running: bool = False
    enabled: bool = False
    exists: bool = False
    description: str = ""
    url: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServiceState:
        return cls(
            name=data.get("name", ""),
            scope=data.get("scope", ServiceScope.USER),
            active_state=data.get("active_state", ""),
            running=bool(data.get("running", False)),
            enabled=bool(data.get("enabled", False)),
            exists=bool(data.get("exists", False)),
            description=data.get("description", ""),
            url=data.get("url", ""),
        )

    @property
    def key(self) -> str:
        """Unique key for a service: ``scope/name``."""
        return f"{self.scope}/{self.name}"


@dataclass(slots=True)
class BluetoothDeviceState:
    """A Bluetooth device known to or discovered by the adapter."""

    address: str = ""
    name: str = ""
    paired: bool = False
    bonded: bool = False
    trusted: bool = False
    connected: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BluetoothDeviceState:
        return cls(
            address=data.get("address", ""),
            name=data.get("name", ""),
            paired=bool(data.get("paired", False)),
            bonded=bool(data.get("bonded", False)),
            trusted=bool(data.get("trusted", False)),
            connected=bool(data.get("connected", False)),
        )


@dataclass(slots=True)
class BluetoothState:
    """Response of ``GET /bluetooth`` — adapter state plus known devices."""

    powered: bool = False
    discoverable: bool = False
    pairable: bool = False
    pairing_active: bool = False
    pairing_until: datetime | None = None
    scanning: bool = False
    known_devices: list[BluetoothDeviceState] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BluetoothState:
        return cls(
            powered=bool(data.get("powered", False)),
            discoverable=bool(data.get("discoverable", False)),
            pairable=bool(data.get("pairable", False)),
            pairing_active=bool(data.get("pairing_active", False)),
            pairing_until=_parse_datetime(data.get("pairing_until")),
            scanning=bool(data.get("scanning", False)),
            known_devices=[BluetoothDeviceState.from_dict(d) for d in data.get("known_devices") or []],
        )


@dataclass(slots=True)
class UpgradeRunState:
    """Live state of an upgrade run."""

    state: str = UpgradeRunStateValue.IDLE
    origin: str = ""
    percent: int | None = None
    step: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UpgradeRunState:
        return cls(
            state=data.get("state", UpgradeRunStateValue.IDLE),
            origin=data.get("origin", ""),
            percent=data.get("percent"),
            step=data.get("step"),
            started_at=_parse_datetime(data.get("started_at")),
            finished_at=_parse_datetime(data.get("finished_at")),
        )

    @property
    def running(self) -> bool:
        return self.state == UpgradeRunStateValue.RUNNING


@dataclass(slots=True)
class UpgradeStatus:
    """Response of ``GET /upgrade``."""

    current: str = ""
    latest: str = ""
    upgrade_available: bool = False
    checked_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    run: UpgradeRunState = field(default_factory=UpgradeRunState)
    can_check: bool = False
    can_upgrade: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UpgradeStatus:
        return cls(
            current=data.get("current", ""),
            latest=data.get("latest", ""),
            upgrade_available=bool(data.get("upgrade_available", False)),
            checked_at=_parse_datetime(data.get("checked_at")),
            extra=dict(data.get("extra") or {}),
            run=UpgradeRunState.from_dict(data.get("run") or {}),
            can_check=bool(data.get("can_check", False)),
            can_upgrade=bool(data.get("can_upgrade", False)),
        )
