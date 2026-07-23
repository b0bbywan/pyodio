"""Low-level typed REST client for odio-api.

One method per endpoint, returning models from :mod:`pyodio.models`.
Action endpoints (``POST``) return ``None`` on success (the server answers
``202 Accepted`` with an empty body).

For a stateful, event-driven API, use :class:`pyodio.OdioHub` instead.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

import aiohttp

from .exceptions import OdioApiError, OdioConnectionError, OdioTimeoutError
from .models import (
    AudioClientState,
    AudioOutputState,
    AudioServerState,
    AudioSnapshot,
    BluetoothDeviceState,
    BluetoothState,
    PlayerState,
    PowerCapabilities,
    ServerInfo,
    ServiceState,
    UpgradeStatus,
)

DEFAULT_BASE_URL = "http://localhost:8018"
DEFAULT_TIMEOUT = 10.0

_SERVICE_ACTIONS = frozenset({"enable", "disable", "start", "stop", "restart"})


def _seg(value: str) -> str:
    """URL-encode a path segment (bus names, sink names contain slashes/dots)."""
    return quote(value, safe="")


class OdioClient:
    """Async REST client for a single odio-api server.

    ``session`` is optional: pass a shared :class:`aiohttp.ClientSession`
    (e.g. Home Assistant's) to reuse it, or omit it and the client manages
    its own. Only sessions created by the client are closed by
    :meth:`close`.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        session: aiohttp.ClientSession | None = None,
        *,
        request_timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None
        self._timeout = request_timeout

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close the session if this client created it."""
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> OdioClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        client_timeout = aiohttp.ClientTimeout(total=timeout or self._timeout)
        try:
            async with self.session.request(method, url, json=json, timeout=client_timeout) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise OdioApiError(resp.status, text.strip())
                if resp.status in (202, 204) or resp.content_length == 0:
                    return None
                return await resp.json()
        except TimeoutError as err:
            raise OdioTimeoutError(f"Timeout on {method} {url}") from err
        except aiohttp.ClientError as err:
            raise OdioConnectionError(f"Cannot reach {url}: {err}") from err

    async def _get(self, path: str) -> Any:
        return await self._request("GET", path)

    async def _post(self, path: str, json: dict[str, Any] | None = None, timeout: float | None = None) -> None:
        await self._request("POST", path, json=json, timeout=timeout)

    # ------------------------------------------------------------- server
    async def get_server_info(self) -> ServerInfo:
        return ServerInfo.from_dict(await self._get("/server"))

    # -------------------------------------------------------------- power
    async def get_power_capabilities(self) -> PowerCapabilities:
        return PowerCapabilities.from_dict(await self._get("/power"))

    async def reboot(self) -> None:
        await self._post("/power/reboot")

    async def power_off(self) -> None:
        await self._post("/power/power_off")

    # ------------------------------------------------------------ players
    async def get_players(self) -> list[PlayerState]:
        return [PlayerState.from_dict(p) for p in await self._get("/players") or []]

    def player_cover_url(self, bus_name: str, *, art_url: str | None = None, track_id: str | None = None) -> str:
        """Absolute URL of the server-side cover art proxy for a player.

        ``art_url``/``track_id`` are cache-busting query params (ignored
        server-side): the URL changes whenever the track or its art does.
        """
        url = f"{self._base_url}/players/{_seg(bus_name)}/cover"
        if art_url or track_id:
            url += "?" + urlencode({"t": track_id or "", "a": art_url or ""})
        return url

    async def player_play(self, bus_name: str) -> None:
        await self._post(f"/players/{_seg(bus_name)}/play")

    async def player_pause(self, bus_name: str) -> None:
        await self._post(f"/players/{_seg(bus_name)}/pause")

    async def player_play_pause(self, bus_name: str) -> None:
        await self._post(f"/players/{_seg(bus_name)}/play_pause")

    async def player_stop(self, bus_name: str) -> None:
        await self._post(f"/players/{_seg(bus_name)}/stop")

    async def player_next(self, bus_name: str) -> None:
        await self._post(f"/players/{_seg(bus_name)}/next")

    async def player_previous(self, bus_name: str) -> None:
        await self._post(f"/players/{_seg(bus_name)}/previous")

    async def player_seek(self, bus_name: str, offset: int) -> None:
        """Seek relative to the current position, in microseconds."""
        await self._post(f"/players/{_seg(bus_name)}/seek", {"offset": offset})

    async def player_set_position(self, bus_name: str, position: int, track_id: str | None = None) -> None:
        """Set the absolute position in microseconds. ``track_id`` guards against track changes."""
        body: dict[str, Any] = {"position": position}
        if track_id is not None:
            body["track_id"] = track_id
        await self._post(f"/players/{_seg(bus_name)}/position", body)

    async def player_set_volume(self, bus_name: str, volume: float) -> None:
        await self._post(f"/players/{_seg(bus_name)}/volume", {"volume": volume})

    async def player_set_loop(self, bus_name: str, loop: str) -> None:
        """Set loop mode: ``"None"``, ``"Track"`` or ``"Playlist"`` (see :class:`LoopStatus`)."""
        await self._post(f"/players/{_seg(bus_name)}/loop", {"loop": str(loop)})

    async def player_set_shuffle(self, bus_name: str, shuffle: bool) -> None:
        await self._post(f"/players/{_seg(bus_name)}/shuffle", {"shuffle": shuffle})

    # -------------------------------------------------------------- audio
    async def get_audio(self) -> AudioSnapshot:
        """Unified audio state. Falls back to per-endpoint fetches on older servers.

        Older servers may lack ``/audio`` (and even ``/audio/outputs``);
        whatever endpoint is missing yields an empty list instead of failing.
        """
        try:
            return AudioSnapshot.from_dict(await self._get("/audio"))
        except OdioApiError as err:
            if err.status != 404:
                raise
        server = await self.get_audio_server()
        return AudioSnapshot(
            kind=server.kind,
            clients=await self._tolerate_404(self.get_audio_clients()),
            outputs=await self._tolerate_404(self.get_audio_outputs()),
        )

    async def _tolerate_404(self, call: Any) -> list[Any]:
        try:
            return await call
        except OdioApiError as err:
            if err.status == 404:
                return []
            raise

    async def get_audio_server(self) -> AudioServerState:
        return AudioServerState.from_dict(await self._get("/audio/server"))

    async def get_audio_clients(self) -> list[AudioClientState]:
        return [AudioClientState.from_dict(c) for c in await self._get("/audio/clients") or []]

    async def get_audio_outputs(self) -> list[AudioOutputState]:
        return [AudioOutputState.from_dict(o) for o in await self._get("/audio/outputs") or []]

    async def toggle_master_mute(self) -> None:
        """Toggle master mute. The server only supports toggling, not setting."""
        await self._post("/audio/server/mute")

    async def set_master_volume(self, volume: float) -> None:
        await self._post("/audio/server/volume", {"volume": _check_volume(volume)})

    async def toggle_client_mute(self, name: str) -> None:
        """Toggle an audio client's mute. Clients are addressed by ``name``."""
        await self._post(f"/audio/clients/{_seg(name)}/mute")

    async def set_client_volume(self, name: str, volume: float) -> None:
        await self._post(f"/audio/clients/{_seg(name)}/volume", {"volume": _check_volume(volume)})

    async def set_default_output(self, name: str) -> None:
        await self._post(f"/audio/outputs/{_seg(name)}/default")

    async def toggle_output_mute(self, name: str) -> None:
        await self._post(f"/audio/outputs/{_seg(name)}/mute")

    async def set_output_volume(self, name: str, volume: float) -> None:
        await self._post(f"/audio/outputs/{_seg(name)}/volume", {"volume": _check_volume(volume)})

    # ----------------------------------------------------------- services
    async def get_services(self) -> list[ServiceState]:
        return [ServiceState.from_dict(s) for s in await self._get("/services") or []]

    async def service_action(self, scope: str, unit: str, action: str) -> None:
        """Run a lifecycle action on a unit. System-scope units are read-only server-side."""
        if action not in _SERVICE_ACTIONS:
            raise ValueError(f"Unknown service action {action!r}, expected one of {sorted(_SERVICE_ACTIONS)}")
        await self._post(f"/services/{_seg(scope)}/{_seg(unit)}/{action}", timeout=15.0)

    async def service_start(self, scope: str, unit: str) -> None:
        await self.service_action(scope, unit, "start")

    async def service_stop(self, scope: str, unit: str) -> None:
        await self.service_action(scope, unit, "stop")

    async def service_restart(self, scope: str, unit: str) -> None:
        await self.service_action(scope, unit, "restart")

    async def service_enable(self, scope: str, unit: str) -> None:
        await self.service_action(scope, unit, "enable")

    async def service_disable(self, scope: str, unit: str) -> None:
        await self.service_action(scope, unit, "disable")

    # ---------------------------------------------------------- bluetooth
    async def get_bluetooth(self) -> BluetoothState:
        return BluetoothState.from_dict(await self._get("/bluetooth"))

    async def get_bluetooth_devices(self) -> list[BluetoothDeviceState]:
        return [BluetoothDeviceState.from_dict(d) for d in await self._get("/bluetooth/devices") or []]

    async def bluetooth_power_up(self) -> None:
        await self._post("/bluetooth/power_up")

    async def bluetooth_power_down(self) -> None:
        await self._post("/bluetooth/power_down")

    async def bluetooth_pairing_mode(self) -> None:
        await self._post("/bluetooth/pairing_mode")

    async def bluetooth_scan(self) -> None:
        await self._post("/bluetooth/scan")

    async def bluetooth_scan_stop(self) -> None:
        await self._post("/bluetooth/scan/stop")

    async def bluetooth_connect(self, address: str) -> None:
        await self._post("/bluetooth/connect", {"address": address})

    async def bluetooth_disconnect(self, address: str) -> None:
        await self._post("/bluetooth/disconnect", {"address": address})

    # ------------------------------------------------------------ upgrade
    async def get_upgrade(self) -> UpgradeStatus | None:
        data = await self._get("/upgrade")
        return UpgradeStatus.from_dict(data) if data else None

    async def upgrade_check(self) -> None:
        await self._post("/upgrade/check")

    async def upgrade_start(self) -> None:
        """Start an upgrade. Raises :class:`OdioApiError` with status 409 if one is running."""
        await self._post("/upgrade/start")


def _check_volume(volume: float) -> float:
    """Fail fast on volumes the server would reject with a 400."""
    if not 0.0 <= volume <= 1.0:
        raise ValueError(f"volume must be between 0 and 1, got {volume}")
    return volume
