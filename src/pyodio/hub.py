"""High-level, stateful odio client.

:class:`OdioHub` connects once, snapshots the server state, then keeps it
live through the SSE event stream (with automatic reconnection and
resynchronization). State is exposed as entity objects — :class:`Player`,
:class:`AudioClient`, :class:`AudioOutput`, :class:`Service`,
:class:`BluetoothDevice` — that carry both the latest state and the
commands that act on it::

    async with pyodio.connect("http://odio.local:8018") as odio:
        player = odio.players.find("spotify")
        await player.play_pause()
        await odio.audio.set_volume(0.4)
        odio.players.on_change(lambda change, p: print(change, p.title))
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .exceptions import OdioApiError, OdioError
from .models import (
    AudioClientState,
    AudioOutputState,
    AudioServerState,
    AudioSnapshot,
    Backends,
    BluetoothDeviceState,
    BluetoothState,
    PlaybackStatus,
    PlayerCapabilities,
    PlayerState,
    PowerCapabilities,
    ServerInfo,
    ServiceState,
    UpgradeRunState,
    UpgradeRunStateValue,
    UpgradeStatus,
)
from .rest import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, OdioClient
from .sse import DEFAULT_KEEPALIVE, EventStream, OdioEvent

_LOGGER = logging.getLogger(__name__)

# SSE event types (see go-odio-api events/events.go)
EVENT_PLAYER_UPDATED = "player.updated"
EVENT_PLAYER_ADDED = "player.added"
EVENT_PLAYER_REMOVED = "player.removed"
EVENT_PLAYER_POSITION = "player.position"
EVENT_AUDIO_UPDATED = "audio.updated"
EVENT_AUDIO_REMOVED = "audio.removed"
EVENT_AUDIO_OUTPUT_UPDATED = "audio.output.updated"
EVENT_AUDIO_OUTPUT_REMOVED = "audio.output.removed"
EVENT_SERVICE_UPDATED = "service.updated"
EVENT_BLUETOOTH_UPDATED = "bluetooth.updated"
EVENT_BLUETOOTH_DISCOVERED = "bluetooth.discovered"
EVENT_POWER_ACTION = "power.action"
EVENT_UPGRADE_INFO = "upgrade.info"
EVENT_UPGRADE_PROGRESS = "upgrade.progress"

# Change kinds passed to on_change listeners
ADDED = "added"
UPDATED = "updated"
REMOVED = "removed"
POSITION = "position"
DISCOVERED = "discovered"
PROGRESS = "progress"

ChangeListener = Callable[[str, Any], None]


class _Notifier:
    """Tiny fan-out helper for per-domain change listeners."""

    __slots__ = ("_listeners",)

    def __init__(self) -> None:
        self._listeners: list[ChangeListener] = []

    def subscribe(self, listener: ChangeListener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def notify(self, change: str, obj: Any) -> None:
        for listener in list(self._listeners):
            try:
                listener(change, obj)
            except Exception:
                _LOGGER.exception("Change listener failed for %s", change)


def _as_list(data: Any) -> list[dict[str, Any]]:
    """SSE payloads may be a single object or a batch; normalize to a list."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _aware(moment: datetime | None) -> datetime | None:
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


class EntityMap[E](Mapping[str, E]):
    """Read-only mapping of live entities, keyed by their natural id."""

    def __init__(self) -> None:
        self._items: dict[str, E] = {}

    def __getitem__(self, key: str) -> E:
        return self._items[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


# --------------------------------------------------------------------- MPRIS


class Player:
    """A live MPRIS player: current state plus transport commands."""

    def __init__(self, hub: OdioHub, state: PlayerState) -> None:
        self._hub = hub
        self.state = state
        self.available = True

    def __repr__(self) -> str:
        return f"<Player {self.bus_name} {self.state.playback_status}>"

    # State shortcuts -----------------------------------------------------
    @property
    def bus_name(self) -> str:
        return self.state.bus_name

    @property
    def app_name(self) -> str:
        return self.state.app_name

    @property
    def identity(self) -> str:
        return self.state.identity

    @property
    def playback_status(self) -> str:
        return self.state.playback_status

    @property
    def is_playing(self) -> bool:
        return self.available and self.state.is_playing

    @property
    def title(self) -> str | None:
        return self.state.title

    @property
    def artist(self) -> str | None:
        return self.state.artist

    @property
    def album(self) -> str | None:
        return self.state.album

    @property
    def art_url(self) -> str | None:
        return self.state.art_url

    @property
    def duration(self) -> int | None:
        """Track length in microseconds."""
        return self.state.duration

    @property
    def volume(self) -> float | None:
        return self.state.volume

    @property
    def shuffle(self) -> bool | None:
        return self.state.shuffle

    @property
    def loop_status(self) -> str | None:
        return self.state.loop_status

    @property
    def capabilities(self) -> PlayerCapabilities:
        return self.state.capabilities

    @property
    def metadata(self) -> dict[str, str]:
        return self.state.metadata

    @property
    def cover_url(self) -> str:
        """URL of the server-side cover art proxy for this player."""
        return self._hub.client.player_cover_url(
            self.bus_name, art_url=self.state.art_url, track_id=self.state.track_id
        )

    @property
    def position(self) -> int | None:
        """Current position in microseconds, extrapolated while playing.

        The server pushes position beacons periodically; between beacons the
        position is projected from the last known value and the playback rate.
        """
        state = self.state
        if state.position is None:
            return None
        updated_at = _aware(state.position_updated_at)
        if not self.is_playing or updated_at is None:
            return state.position
        elapsed = (datetime.now(UTC) - updated_at).total_seconds()
        rate = state.rate if state.rate else 1.0
        position = state.position + int(elapsed * rate * 1_000_000)
        duration = state.duration
        if duration is not None:
            position = min(position, duration)
        return max(position, 0)

    # Commands ------------------------------------------------------------
    async def play(self) -> None:
        await self._hub.client.player_play(self.bus_name)

    async def pause(self) -> None:
        await self._hub.client.player_pause(self.bus_name)

    async def play_pause(self) -> None:
        await self._hub.client.player_play_pause(self.bus_name)

    async def stop(self) -> None:
        await self._hub.client.player_stop(self.bus_name)

    async def next(self) -> None:
        await self._hub.client.player_next(self.bus_name)

    async def previous(self) -> None:
        await self._hub.client.player_previous(self.bus_name)

    async def seek(self, offset: int) -> None:
        """Seek relative to the current position, in microseconds."""
        await self._hub.client.player_seek(self.bus_name, offset)

    async def set_position(self, position: int, track_id: str | None = None) -> None:
        """Jump to an absolute position in microseconds.

        ``track_id`` defaults to the current track's id so the command is
        ignored server-side if the track changed in the meantime.
        """
        if track_id is None:
            track_id = self.state.track_id
        await self._hub.client.player_set_position(self.bus_name, position, track_id)

    async def set_volume(self, volume: float) -> None:
        await self._hub.client.player_set_volume(self.bus_name, volume)

    async def set_loop(self, loop: str) -> None:
        await self._hub.client.player_set_loop(self.bus_name, loop)

    async def set_shuffle(self, shuffle: bool) -> None:
        await self._hub.client.player_set_shuffle(self.bus_name, shuffle)


class Players(EntityMap[Player]):
    """Live MPRIS players, keyed by bus name."""

    def __init__(self, hub: OdioHub) -> None:
        super().__init__()
        self._hub = hub
        self._notifier = _Notifier()

    def on_change(self, listener: ChangeListener) -> Callable[[], None]:
        """Subscribe to player changes: listener(change, player)."""
        return self._notifier.subscribe(listener)

    def find(self, name: str) -> Player | None:
        """Look up a player by bus name, app name or identity (case-insensitive)."""
        if name in self._items:
            return self._items[name]
        lowered = name.lower()
        for player in self._items.values():
            if player.app_name.lower() == lowered or player.identity.lower() == lowered:
                return player
        return None

    @property
    def playing(self) -> list[Player]:
        """Players currently playing."""
        return [p for p in self._items.values() if p.is_playing]

    def _set_snapshot(self, states: list[PlayerState]) -> None:
        seen = set()
        for state in states:
            seen.add(state.bus_name)
            self._upsert(state)
        for bus_name in list(self._items):
            if bus_name not in seen:
                self._remove(bus_name)

    def _upsert(self, state: PlayerState) -> Player:
        # Old servers omit position_updated_at; stamp receipt so extrapolation works.
        if state.position is not None and state.position_updated_at is None:
            state.position_updated_at = datetime.now(UTC)
        player = self._items.get(state.bus_name)
        if player is None:
            player = Player(self._hub, state)
            self._items[state.bus_name] = player
            self._notifier.notify(ADDED, player)
        else:
            player.state = state
            player.available = True
            self._notifier.notify(UPDATED, player)
        return player

    def _remove(self, bus_name: str) -> None:
        player = self._items.pop(bus_name, None)
        if player is not None:
            player.available = False
            player.state.playback_status = PlaybackStatus.STOPPED
            self._notifier.notify(REMOVED, player)

    def _handle_event(self, event: OdioEvent) -> None:
        if event.type in (EVENT_PLAYER_UPDATED, EVENT_PLAYER_ADDED):
            for item in _as_list(event.data):
                # player.* events are wrapped in a {data, emitted_at} envelope
                inner = item.get("data")
                if not isinstance(inner, dict):
                    inner = item
                self._upsert(PlayerState.from_dict(inner))
        elif event.type == EVENT_PLAYER_REMOVED:
            for item in _as_list(event.data):
                self._remove(item.get("bus_name", ""))
        elif event.type == EVENT_PLAYER_POSITION:
            for item in _as_list(event.data):
                player = self._items.get(item.get("bus_name", ""))
                if player is None or not isinstance(item.get("position"), int):
                    continue
                player.state.position = item["position"]
                emitted = item.get("emitted_at")
                if isinstance(emitted, (int, float)):
                    player.state.position_updated_at = datetime.fromtimestamp(emitted / 1000, tz=UTC)
                else:
                    player.state.position_updated_at = datetime.now(UTC)
                self._notifier.notify(POSITION, player)


# --------------------------------------------------------------------- Audio


class AudioClient:
    """A live per-application audio stream."""

    def __init__(self, hub: OdioHub, state: AudioClientState) -> None:
        self._hub = hub
        self.state = state

    def __repr__(self) -> str:
        return f"<AudioClient {self.name!r} volume={self.volume:.2f} muted={self.muted}>"

    @property
    def name(self) -> str:
        return self.state.name

    @property
    def app(self) -> str:
        return self.state.app

    @property
    def volume(self) -> float:
        return self.state.volume

    @property
    def muted(self) -> bool:
        return self.state.muted

    @property
    def corked(self) -> bool:
        return self.state.corked

    @property
    def is_remote(self) -> bool:
        """Whether the stream comes from another host than the odio server."""
        host = self.state.host
        return bool(host) and host != self._hub.server.hostname

    async def set_volume(self, volume: float) -> None:
        await self._hub.client.set_client_volume(self.name, volume)

    async def toggle_mute(self) -> None:
        await self._hub.client.toggle_client_mute(self.name)

    async def set_muted(self, muted: bool) -> None:
        """Reach the desired mute state (the server only supports toggling)."""
        if self.state.muted != muted:
            await self.toggle_mute()


class AudioOutput:
    """A live audio output device (sink)."""

    def __init__(self, hub: OdioHub, state: AudioOutputState) -> None:
        self._hub = hub
        self.state = state

    def __repr__(self) -> str:
        return f"<AudioOutput {self.name!r} default={self.is_default}>"

    @property
    def name(self) -> str:
        return self.state.name

    @property
    def description(self) -> str:
        return self.state.description

    @property
    def volume(self) -> float:
        return self.state.volume

    @property
    def muted(self) -> bool:
        return self.state.muted

    @property
    def is_default(self) -> bool:
        return self.state.default

    async def set_volume(self, volume: float) -> None:
        await self._hub.client.set_output_volume(self.name, volume)

    async def toggle_mute(self) -> None:
        await self._hub.client.toggle_output_mute(self.name)

    async def set_muted(self, muted: bool) -> None:
        """Reach the desired mute state (the server only supports toggling)."""
        if self.state.muted != muted:
            await self.toggle_mute()

    async def make_default(self) -> None:
        await self._hub.client.set_default_output(self.name)


class Audio:
    """Live audio state: master volume, application streams and outputs."""

    def __init__(self, hub: OdioHub) -> None:
        self._hub = hub
        self._notifier = _Notifier()
        self.server: AudioServerState | None = None
        self.clients: EntityMap[AudioClient] = EntityMap()
        self.outputs: EntityMap[AudioOutput] = EntityMap()

    def on_change(self, listener: ChangeListener) -> Callable[[], None]:
        """Subscribe to audio changes: listener(change, client_or_output)."""
        return self._notifier.subscribe(listener)

    @property
    def kind(self) -> str:
        """``"pulseaudio"`` or ``"pipewire"``."""
        return self.server.kind if self.server else ""

    @property
    def default_output(self) -> AudioOutput | None:
        for output in self.outputs.values():
            if output.is_default:
                return output
        return None

    @property
    def volume(self) -> float | None:
        """Master volume; tracks the default output live, falls back to the snapshot."""
        default = self.default_output
        if default is not None:
            return default.volume
        return self.server.volume if self.server else None

    @property
    def muted(self) -> bool | None:
        default = self.default_output
        if default is not None:
            return default.muted
        return self.server.muted if self.server else None

    async def set_volume(self, volume: float) -> None:
        """Set the master volume (0.0–1.0)."""
        await self._hub.client.set_master_volume(volume)

    async def toggle_mute(self) -> None:
        await self._hub.client.toggle_master_mute()

    async def set_muted(self, muted: bool) -> None:
        """Reach the desired master mute state (the server only supports toggling)."""
        current = self.muted
        if current is None:
            current = (await self._hub.client.get_audio_server()).muted
        if current != muted:
            await self.toggle_mute()

    def _set_snapshot(self, snapshot: AudioSnapshot, server: AudioServerState | None) -> None:
        self.server = server or AudioServerState(kind=snapshot.kind)
        self._merge(self.clients, snapshot.clients, AudioClient)
        self._merge(self.outputs, snapshot.outputs, AudioOutput)

    def _merge(self, entities: EntityMap[Any], states: list[Any], factory: type) -> None:
        seen = set()
        for state in states:
            seen.add(state.name)
            self._upsert(entities, state, factory)
        for name in list(entities._items):
            if name not in seen:
                self._notifier.notify(REMOVED, entities._items.pop(name))

    def _upsert(self, entities: EntityMap[Any], state: Any, factory: type) -> Any:
        entity = entities._items.get(state.name)
        if entity is None:
            entity = factory(self._hub, state)
            entities._items[state.name] = entity
            self._notifier.notify(ADDED, entity)
        else:
            entity.state = state
            self._notifier.notify(UPDATED, entity)
        return entity

    def _handle_event(self, event: OdioEvent) -> None:
        if event.type == EVENT_AUDIO_UPDATED:
            for item in _as_list(event.data):
                self._upsert(self.clients, AudioClientState.from_dict(item), AudioClient)
        elif event.type == EVENT_AUDIO_REMOVED:
            for item in _as_list(event.data):
                entity = self.clients._items.pop(item.get("name", ""), None)
                if entity is not None:
                    self._notifier.notify(REMOVED, entity)
        elif event.type == EVENT_AUDIO_OUTPUT_UPDATED:
            for item in _as_list(event.data):
                self._upsert(self.outputs, AudioOutputState.from_dict(item), AudioOutput)
        elif event.type == EVENT_AUDIO_OUTPUT_REMOVED:
            for item in _as_list(event.data):
                output = self.outputs._items.pop(item.get("name", ""), None)
                if output is not None:
                    self._notifier.notify(REMOVED, output)


# ------------------------------------------------------------------ systemd


class Service:
    """A live systemd unit with lifecycle commands."""

    def __init__(self, hub: OdioHub, state: ServiceState) -> None:
        self._hub = hub
        self.state = state

    def __repr__(self) -> str:
        return f"<Service {self.state.key} running={self.running}>"

    @property
    def name(self) -> str:
        return self.state.name

    @property
    def scope(self) -> str:
        return self.state.scope

    @property
    def running(self) -> bool:
        return self.state.running

    @property
    def enabled(self) -> bool:
        return self.state.enabled

    @property
    def description(self) -> str:
        return self.state.description

    async def start(self) -> None:
        await self._hub.client.service_start(self.scope, self.name)

    async def stop(self) -> None:
        await self._hub.client.service_stop(self.scope, self.name)

    async def restart(self) -> None:
        await self._hub.client.service_restart(self.scope, self.name)

    async def enable(self) -> None:
        await self._hub.client.service_enable(self.scope, self.name)

    async def disable(self) -> None:
        await self._hub.client.service_disable(self.scope, self.name)


class Services(EntityMap[Service]):
    """Live systemd services, keyed by ``scope/name``."""

    def __init__(self, hub: OdioHub) -> None:
        super().__init__()
        self._hub = hub
        self._notifier = _Notifier()

    def on_change(self, listener: ChangeListener) -> Callable[[], None]:
        return self._notifier.subscribe(listener)

    def find(self, name: str, scope: str | None = None) -> Service | None:
        """Look up a service by unit name (optionally restricted to a scope)."""
        for service in self._items.values():
            if service.name == name and (scope is None or service.scope == scope):
                return service
        return None

    def _set_snapshot(self, states: list[ServiceState]) -> None:
        seen = set()
        for state in states:
            seen.add(state.key)
            self._upsert(state)
        for key in list(self._items):
            if key not in seen:
                self._notifier.notify(REMOVED, self._items.pop(key))

    def _upsert(self, state: ServiceState) -> None:
        service = self._items.get(state.key)
        if service is None:
            service = Service(self._hub, state)
            self._items[state.key] = service
            self._notifier.notify(ADDED, service)
        else:
            service.state = state
            self._notifier.notify(UPDATED, service)

    def _handle_event(self, event: OdioEvent) -> None:
        if event.type == EVENT_SERVICE_UPDATED:
            for item in _as_list(event.data):
                self._upsert(ServiceState.from_dict(item))


# ---------------------------------------------------------------- bluetooth


class BluetoothDevice:
    """A live Bluetooth device."""

    def __init__(self, hub: OdioHub, state: BluetoothDeviceState) -> None:
        self._hub = hub
        self.state = state

    def __repr__(self) -> str:
        return f"<BluetoothDevice {self.address} {self.name!r} connected={self.connected}>"

    @property
    def address(self) -> str:
        return self.state.address

    @property
    def name(self) -> str:
        return self.state.name

    @property
    def paired(self) -> bool:
        return self.state.paired

    @property
    def connected(self) -> bool:
        return self.state.connected

    async def connect(self) -> None:
        await self._hub.client.bluetooth_connect(self.address)

    async def disconnect(self) -> None:
        await self._hub.client.bluetooth_disconnect(self.address)


class Bluetooth:
    """Live Bluetooth adapter state and devices, keyed by MAC address."""

    def __init__(self, hub: OdioHub) -> None:
        self._hub = hub
        self._notifier = _Notifier()
        self.state: BluetoothState | None = None
        self.devices: EntityMap[BluetoothDevice] = EntityMap()

    def on_change(self, listener: ChangeListener) -> Callable[[], None]:
        return self._notifier.subscribe(listener)

    @property
    def powered(self) -> bool:
        return self.state.powered if self.state else False

    @property
    def scanning(self) -> bool:
        return self.state.scanning if self.state else False

    @property
    def pairing_active(self) -> bool:
        return self.state.pairing_active if self.state else False

    @property
    def connected_devices(self) -> list[BluetoothDevice]:
        return [d for d in self.devices.values() if d.connected]

    async def power_up(self) -> None:
        await self._hub.client.bluetooth_power_up()

    async def power_down(self) -> None:
        await self._hub.client.bluetooth_power_down()

    async def pairing_mode(self) -> None:
        await self._hub.client.bluetooth_pairing_mode()

    async def scan(self) -> None:
        await self._hub.client.bluetooth_scan()

    async def scan_stop(self) -> None:
        await self._hub.client.bluetooth_scan_stop()

    async def connect(self, address: str) -> None:
        await self._hub.client.bluetooth_connect(address)

    async def disconnect(self, address: str) -> None:
        await self._hub.client.bluetooth_disconnect(address)

    def _set_state(self, state: BluetoothState) -> None:
        self.state = state
        for device_state in state.known_devices:
            self._upsert(device_state, DISCOVERED)
        self._notifier.notify(UPDATED, self)

    def _upsert(self, state: BluetoothDeviceState, change: str) -> None:
        device = self.devices._items.get(state.address)
        if device is None:
            self.devices._items[state.address] = device = BluetoothDevice(self._hub, state)
            self._notifier.notify(change, device)
        else:
            device.state = state
            self._notifier.notify(UPDATED, device)

    def _handle_event(self, event: OdioEvent) -> None:
        if event.type == EVENT_BLUETOOTH_UPDATED:
            for item in _as_list(event.data):
                self._set_state(BluetoothState.from_dict(item))
        elif event.type == EVENT_BLUETOOTH_DISCOVERED:
            for item in _as_list(event.data):
                self._upsert(BluetoothDeviceState.from_dict(item), DISCOVERED)


# -------------------------------------------------------------------- power


class Power:
    """Power capabilities and actions."""

    def __init__(self, hub: OdioHub) -> None:
        self._hub = hub
        self.capabilities: PowerCapabilities | None = None

    @property
    def can_reboot(self) -> bool:
        return bool(self.capabilities and self.capabilities.reboot)

    @property
    def can_power_off(self) -> bool:
        return bool(self.capabilities and self.capabilities.power_off)

    async def reboot(self) -> None:
        await self._hub.client.reboot()

    async def power_off(self) -> None:
        await self._hub.client.power_off()


# ------------------------------------------------------------------ upgrade


class Upgrade:
    """Live upgrade status: available version, run progress, actions."""

    def __init__(self, hub: OdioHub) -> None:
        self._hub = hub
        self._notifier = _Notifier()
        self.status: UpgradeStatus | None = None

    def on_change(self, listener: ChangeListener) -> Callable[[], None]:
        return self._notifier.subscribe(listener)

    @property
    def available(self) -> bool:
        return bool(self.status and self.status.upgrade_available)

    @property
    def current_version(self) -> str:
        return self.status.current if self.status else ""

    @property
    def latest_version(self) -> str:
        return self.status.latest if self.status else ""

    @property
    def in_progress(self) -> bool:
        return bool(self.status and self.status.run.running)

    @property
    def progress_percent(self) -> int | None:
        return self.status.run.percent if self.status else None

    async def check(self) -> None:
        """Trigger an upgrade availability check."""
        await self._hub.client.upgrade_check()

    async def start(self) -> None:
        """Start the upgrade. Raises :class:`OdioApiError` (409) if already running."""
        await self._hub.client.upgrade_start()

    def _set_status(self, status: UpgradeStatus | None) -> None:
        self.status = status
        if status is not None:
            self._notifier.notify(UPDATED, self)

    def _handle_event(self, event: OdioEvent) -> None:
        if event.type == EVENT_UPGRADE_INFO:
            for item in _as_list(event.data):
                self._apply_info(item)
        elif event.type == EVENT_UPGRADE_PROGRESS:
            for item in _as_list(event.data):
                self._apply_progress(item)

    def _apply_info(self, data: dict[str, Any]) -> None:
        if self.status is None:
            self.status = UpgradeStatus()
        if "state" in data:
            # Run-lifecycle shape: {"state": ..., "percent": ..., "step": ...}
            self.status.run = UpgradeRunState.from_dict(data)
        else:
            # Detector-status shape; keep run/capability fields the event lacks.
            fresh = UpgradeStatus.from_dict(data)
            fresh.run = UpgradeRunState.from_dict(data["run"]) if "run" in data else self.status.run
            if "can_check" not in data:
                fresh.can_check = self.status.can_check
            if "can_upgrade" not in data:
                fresh.can_upgrade = self.status.can_upgrade
            self.status = fresh
        self._notifier.notify(UPDATED, self)

    def _apply_progress(self, data: dict[str, Any]) -> None:
        if self.status is None:
            self.status = UpgradeStatus()
        run = self.status.run
        kind = data.get("event")
        if kind == "begin":
            run.state = UpgradeRunStateValue.RUNNING
            run.percent = 0
        elif kind == "progress":
            run.state = UpgradeRunStateValue.RUNNING
            if isinstance(data.get("percent"), int):
                run.percent = data["percent"]
            if isinstance(data.get("step"), str):
                run.step = data["step"]
        elif kind == "end":
            run.state = UpgradeRunStateValue.IDLE if data.get("success") else UpgradeRunStateValue.FAILED
        self._notifier.notify(PROGRESS, self)


# ---------------------------------------------------------------------- hub


class OdioHub:
    """Stateful, event-driven client for an odio-api server.

    Usage::

        async with OdioHub("http://odio.local:8018") as odio:
            ...

    or explicitly::

        odio = OdioHub("http://odio.local:8018")
        await odio.connect()
        ...
        await odio.close()
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        session: aiohttp.ClientSession | None = None,
        *,
        keepalive: int = DEFAULT_KEEPALIVE,
        request_timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.client = OdioClient(base_url, session, request_timeout=request_timeout)
        self._stream = EventStream(self.client, keepalive=keepalive, on_connected=self._resync)
        self._stream.add_event_listener(self._handle_event)
        self._server: ServerInfo | None = None
        self._event_notifier = _Notifier()

        self.players = Players(self)
        self.audio = Audio(self)
        self.services = Services(self)
        self.bluetooth = Bluetooth(self)
        self.power = Power(self)
        self.upgrade = Upgrade(self)

    async def __aenter__(self) -> OdioHub:
        return await self.connect()

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def server(self) -> ServerInfo:
        """Server identity and enabled backends (available after connect)."""
        if self._server is None:
            raise OdioError("Hub is not connected yet, call connect() first")
        return self._server

    @property
    def backends(self) -> Backends:
        return self.server.backends

    @property
    def connected(self) -> bool:
        """Whether the live event stream is currently established."""
        return self._stream.connected

    def on_event(self, listener: Callable[[OdioEvent], None]) -> Callable[[], None]:
        """Subscribe to every raw SSE event; returns an unsubscribe callable."""
        return self._stream.add_event_listener(listener)

    def on_connection_change(self, listener: Callable[[bool], None]) -> Callable[[], None]:
        """Subscribe to event-stream connectivity changes (True/False)."""
        return self._stream.add_connection_listener(listener)

    async def connect(self) -> OdioHub:
        """Fetch the initial state and start the live event stream."""
        try:
            await self._resync()
            await self._stream.start()
        except BaseException:
            await self.close()
            raise
        return self

    async def close(self) -> None:
        """Stop the event stream and release resources."""
        await self._stream.stop()
        await self.client.close()

    async def _resync(self) -> None:
        """Full state refresh — run at connect and after every SSE reconnect."""
        self._server = await self.client.get_server_info()
        backends = self._server.backends

        if backends.mpris:
            self.players._set_snapshot(await self.client.get_players())
        if backends.pulseaudio:
            server_state = await self.client.get_audio_server()
            self.audio._set_snapshot(await self.client.get_audio(), server_state)
        if backends.systemd:
            try:
                self.services._set_snapshot(await self.client.get_services())
            except OdioApiError as err:
                # /services only exists when units are configured
                if err.status != 404:
                    raise
        if backends.bluetooth:
            self.bluetooth._set_state(await self.client.get_bluetooth())
        if backends.power:
            try:
                self.power.capabilities = await self.client.get_power_capabilities()
            except OdioApiError:
                self.power.capabilities = None
        if backends.upgrade:
            self.upgrade._set_status(await self.client.get_upgrade())

    def _handle_event(self, event: OdioEvent) -> None:
        if event.type.startswith("player."):
            self.players._handle_event(event)
        elif event.type.startswith("audio."):
            self.audio._handle_event(event)
        elif event.type.startswith("service."):
            self.services._handle_event(event)
        elif event.type.startswith("bluetooth."):
            self.bluetooth._handle_event(event)
        elif event.type.startswith("upgrade."):
            self.upgrade._handle_event(event)


class _HubConnection:
    """Awaitable and async-context-manager wrapper returned by :func:`connect`."""

    def __init__(self, hub: OdioHub) -> None:
        self._hub = hub

    def __await__(self):  # type: ignore[no-untyped-def]
        return self._hub.connect().__await__()

    async def __aenter__(self) -> OdioHub:
        return await self._hub.connect()

    async def __aexit__(self, *exc_info: object) -> None:
        await self._hub.close()


def connect(
    base_url: str = DEFAULT_BASE_URL,
    session: aiohttp.ClientSession | None = None,
    *,
    keepalive: int = DEFAULT_KEEPALIVE,
    request_timeout: float = DEFAULT_TIMEOUT,
) -> _HubConnection:
    """Connect to an odio-api server and return a live :class:`OdioHub`.

    Both styles work::

        odio = await pyodio.connect(url)      # remember to await odio.close()
        async with pyodio.connect(url) as odio:
            ...
    """
    return _HubConnection(OdioHub(base_url, session, keepalive=keepalive, request_timeout=request_timeout))
