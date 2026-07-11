# pyodio

Async Python client for [odio-api](https://github.com/b0bbywan/go-odio-api) — the universal remote for your Linux multimedia server.

pyodio gives you a **high-level, stateful, event-driven API**: connect once, and the library keeps a live mirror of the server state (MPRIS players, audio streams and outputs, systemd services, Bluetooth, upgrades) through the odio-api SSE stream, with automatic reconnection and resynchronization. Every entity carries both its current state and the commands that act on it.

## Install

```bash
pip install pyodio
```

Requires Python ≥ 3.12. The only dependency is `aiohttp`.

## Quick start

```python
import asyncio
import pyodio

async def main():
    async with pyodio.connect("http://odio.local:8018") as odio:
        print(f"Connected to {odio.server.hostname} (odio-api {odio.server.api_version})")

        # MPRIS players — live objects with transport controls
        player = odio.players.find("spotify")
        if player:
            print(f"{player.title} — {player.artist} [{player.playback_status}]")
            await player.play_pause()
            await player.set_volume(0.5)

        # Master volume / outputs
        await odio.audio.set_volume(0.4)
        for output in odio.audio.outputs.values():
            print(f"{'*' if output.is_default else ' '} {output.description}")

        # React to live changes pushed by the server
        odio.players.on_change(lambda change, p: print(f"[{change}] {p.app_name}: {p.title}"))
        await asyncio.sleep(60)

asyncio.run(main())
```

The URL defaults to `http://localhost:8018`; `pyodio.connect()` also works with the `odio = await pyodio.connect(...)` style (then call `await odio.close()` yourself).

## The two layers

### `OdioHub` — high-level (recommended)

`pyodio.connect()` / `pyodio.OdioHub` fetches a full snapshot, then applies SSE deltas forever:

| Domain | State | Commands |
|---|---|---|
| `odio.players` | live `Player` entities by bus name, `find()`, `playing` | `play/pause/play_pause/stop/next/previous/seek/set_position/set_volume/set_loop/set_shuffle`, `cover_url` |
| `odio.audio` | master `volume`/`muted`, `clients`, `outputs`, `default_output` | `set_volume`, `set_muted`, per-client/output volume & mute, `make_default()` |
| `odio.services` | `Service` entities by `scope/name` | `start/stop/restart/enable/disable` |
| `odio.bluetooth` | adapter state + `devices` by MAC | `power_up/down`, `pairing_mode`, `scan`, `connect/disconnect` |
| `odio.power` | `can_reboot`, `can_power_off` | `reboot()`, `power_off()` |
| `odio.upgrade` | `available`, versions, live `progress_percent` | `check()`, `start()` |

Only the domains whose backend is enabled server-side (`odio.backends`) are populated.

Niceties handled for you:

- **Positions extrapolated**: `player.position` projects the last server beacon with the playback rate, so it is accurate between events (microseconds, like MPRIS).
- **Mute semantics**: the server only supports *toggling* mute; `set_muted(True/False)` compares with the live state and toggles only when needed.
- **Reconnection**: the SSE stream reconnects with exponential backoff (1 s → 5 min) and re-snapshots the whole state after every reconnect. Watch it with `odio.connected` / `odio.on_connection_change(cb)`.

Subscriptions (all return an unsubscribe callable, listeners must not block):

```python
odio.players.on_change(cb)        # cb(change, player)   change: added/updated/removed/position
odio.audio.on_change(cb)          # cb(change, client_or_output)
odio.services.on_change(cb)
odio.bluetooth.on_change(cb)      # includes "discovered" during scans
odio.upgrade.on_change(cb)        # includes "progress" during upgrades
odio.on_event(cb)                 # every raw SSE event (pyodio.OdioEvent)
odio.on_connection_change(cb)     # cb(connected: bool)
```

### `OdioClient` — low-level

A stateless, typed, one-method-per-endpoint REST client, if you want full control:

```python
from pyodio import OdioClient

async with OdioClient("http://odio.local:8018") as client:
    info = await client.get_server_info()
    players = await client.get_players()
    await client.player_play(players[0].bus_name)
```

Pass an existing `aiohttp.ClientSession` as second argument to reuse it (e.g. Home Assistant's shared session) — the client then never closes it. `pyodio.stream_events(client)` exposes the raw SSE stream as an async generator, and `pyodio.EventStream` adds supervision (reconnect, listeners) without the stateful hub.

## Errors

All errors derive from `pyodio.OdioError`:

- `OdioConnectionError` — server unreachable, stream lost
- `OdioTimeoutError` — request or keepalive timeout
- `OdioApiError` — HTTP error from the server (`.status`, `.message`)

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
uv run mypy src
```

## License

MIT — see [LICENSE](LICENSE).
