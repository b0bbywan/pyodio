"""SSE event stream for odio-api's ``GET /events`` endpoint.

Two layers:

- :meth:`OdioClient.stream_events` equivalent: :func:`stream_events`, a bare
  async generator that parses the wire format and yields :class:`OdioEvent`.
  It does not reconnect.
- :class:`EventStream`, a supervisor that keeps the stream connected with
  exponential backoff and dispatches events to listeners.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

from .exceptions import OdioConnectionError, OdioTimeoutError
from .rest import OdioClient

_LOGGER = logging.getLogger(__name__)

EVENT_SERVER_INFO = "server.info"

DEFAULT_KEEPALIVE = 30
KEEPALIVE_BUFFER = 15.0
RECONNECT_MIN_INTERVAL = 1.0
RECONNECT_MAX_INTERVAL = 300.0

EventListener = Callable[["OdioEvent"], None]
ConnectionListener = Callable[[bool], None]


@dataclass(slots=True)
class OdioEvent:
    """A single server-sent event: its type and JSON-decoded payload."""

    type: str
    data: Any


def _build_query(
    types: list[str] | None,
    backends: list[str] | None,
    exclude: list[str] | None,
    keepalive: int,
) -> dict[str, str]:
    params = {"keepalive": str(keepalive)}
    if types:
        params["types"] = ",".join(types)
    if backends:
        params["backend"] = ",".join(backends)
    if exclude:
        params["exclude"] = ",".join(exclude)
    return params


async def stream_events(
    client: OdioClient,
    *,
    types: list[str] | None = None,
    backends: list[str] | None = None,
    exclude: list[str] | None = None,
    keepalive: int = DEFAULT_KEEPALIVE,
) -> AsyncIterator[OdioEvent]:
    """Yield events from ``GET /events`` until the connection drops.

    Keepalive ``server.info`` events are yielded too, so consumers can track
    liveness. Raises :class:`OdioConnectionError` or :class:`OdioTimeoutError`
    when the stream fails; reconnecting is the caller's job (or use
    :class:`EventStream`).
    """
    url = f"{client.base_url}/events"
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=10.0, sock_read=keepalive + KEEPALIVE_BUFFER)
    try:
        async with client.session.get(
            url,
            params=_build_query(types, backends, exclude, keepalive),
            headers={"Accept": "text/event-stream"},
            timeout=timeout,
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise OdioConnectionError(f"SSE connection refused: HTTP {resp.status}: {text.strip()}")
            event_type = ""
            data_lines: list[str] = []
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if line.startswith("event:"):
                    event_type = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:"):].strip())
                elif line == "" and event_type:
                    payload = "\n".join(data_lines)
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        data = payload
                    yield OdioEvent(event_type, data)
                    event_type = ""
                    data_lines = []
    except TimeoutError as err:
        raise OdioTimeoutError("SSE stream timed out (no keepalive received)") from err
    except aiohttp.ClientError as err:
        raise OdioConnectionError(f"SSE stream failed: {err}") from err


class EventStream:
    """Keeps an SSE connection alive and dispatches events to listeners.

    Reconnects with exponential backoff (1 s up to 5 min, reset on success).
    Listeners are plain callables invoked in the event loop; they must not
    block. ``on_connected`` is an optional coroutine awaited each time the
    stream (re)connects, before events are dispatched — the natural place to
    resynchronize state.
    """

    def __init__(
        self,
        client: OdioClient,
        *,
        types: list[str] | None = None,
        backends: list[str] | None = None,
        exclude: list[str] | None = None,
        keepalive: int = DEFAULT_KEEPALIVE,
        on_connected: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._client = client
        self._types = types
        self._backends = backends
        self._exclude = exclude
        self._keepalive = keepalive
        self._on_connected = on_connected
        self._task: asyncio.Task[None] | None = None
        self._connected = False
        self._event_listeners: list[EventListener] = []
        self._connection_listeners: list[ConnectionListener] = []

    @property
    def connected(self) -> bool:
        """Whether the SSE stream is currently established."""
        return self._connected

    def add_event_listener(self, listener: EventListener) -> Callable[[], None]:
        """Register a listener for every event; returns an unsubscribe callable."""
        self._event_listeners.append(listener)
        return lambda: self._event_listeners.remove(listener)

    def add_connection_listener(self, listener: ConnectionListener) -> Callable[[], None]:
        """Register a listener called with True/False on connect/disconnect."""
        self._connection_listeners.append(listener)
        return lambda: self._connection_listeners.remove(listener)

    async def start(self) -> None:
        """Start the background streaming task (idempotent)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="pyodio-event-stream")

    async def stop(self) -> None:
        """Stop streaming and mark the stream disconnected."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._set_connected(False)

    async def _run(self) -> None:
        backoff = RECONNECT_MIN_INTERVAL
        while True:
            try:
                async for event in stream_events(
                    self._client,
                    types=self._types,
                    backends=self._backends,
                    exclude=self._exclude,
                    keepalive=self._keepalive,
                ):
                    if not self._connected:
                        backoff = RECONNECT_MIN_INTERVAL
                        if self._on_connected is not None:
                            await self._on_connected()
                        self._set_connected(True)
                    self._dispatch(event)
                # Server closed the stream cleanly; reconnect.
                raise OdioConnectionError("SSE stream ended")
            except asyncio.CancelledError:
                raise
            except OdioConnectionError as err:
                _LOGGER.debug("SSE stream lost (%s), reconnecting in %.0fs", err, backoff)
            except Exception:
                _LOGGER.exception("Unexpected error in SSE stream, reconnecting in %.0fs", backoff)
            self._set_connected(False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_INTERVAL)

    def _set_connected(self, connected: bool) -> None:
        if connected == self._connected:
            return
        self._connected = connected
        for listener in list(self._connection_listeners):
            try:
                listener(connected)
            except Exception:
                _LOGGER.exception("Connection listener failed")

    def _dispatch(self, event: OdioEvent) -> None:
        for listener in list(self._event_listeners):
            try:
                listener(event)
            except Exception:
                _LOGGER.exception("Event listener failed for %s", event.type)
