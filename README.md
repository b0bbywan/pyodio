  <p align="center">
    <a href="https://odio.love"><img src="https://odio.love/logo.png" alt="odio" width="160" /></a>
  </p>
  <h1 align="center">pyodio</h1>
  <p align="center"><em>Async Python client for odio — the live server state as high-level Python objects.</em></p>
  <p align="center">
    <a href="https://github.com/b0bbywan/pyodio/releases"><img src="https://img.shields.io/github/v/release/b0bbywan/pyodio?include_prereleases" alt="Release" /></a>
    <a href="https://pypi.org/project/pyodio/"><img src="https://img.shields.io/pypi/v/pyodio" alt="PyPI" /></a>
    <a href="https://github.com/b0bbywan/pyodio/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License" /></a>
    <a href="https://github.com/b0bbywan/pyodio/actions/workflows/build.yml"><img src="https://github.com/b0bbywan/pyodio/actions/workflows/build.yml/badge.svg" alt="Build" /></a>
    <a href="https://github.com/sponsors/b0bbywan"><img src="https://img.shields.io/github/sponsors/b0bbywan?label=Sponsor&logo=GitHub" alt="GitHub Sponsors" /></a>
  </p>
  <p align="center">
    <a href="https://docs.odio.love/api/mpris/"><img src="https://img.shields.io/badge/MPRIS-003399" alt="MPRIS" /></a>
    <a href="https://docs.odio.love/api/pulseaudio/"><img src="https://img.shields.io/badge/PulseAudio-0055AA" alt="PulseAudio" /></a>
    <a href="https://docs.odio.love/api/bluetooth/"><img src="https://img.shields.io/badge/Bluetooth-0082FC?logo=bluetooth&logoColor=white" alt="Bluetooth" /></a>
    <a href="https://docs.odio.love/api/systemd/"><img src="https://img.shields.io/badge/systemd-FF6B35" alt="systemd" /></a>
    <a href="https://docs.odio.love/api/power/"><img src="https://img.shields.io/badge/Power-10B981" alt="Power" /></a>
    <a href="https://docs.odio.love/api/events/"><img src="https://img.shields.io/badge/SSE%20Events-F97316" alt="SSE Events" /></a>
  </p>
  <p align="center">
    Part of the <a href="https://odio.love">odio</a> project — <a href="https://docs.odio.love/control/pyodio/">full documentation</a>.
  </p>
  <p align="center">
    <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white" alt="Python" /></a>
    <a href="https://docs.aiohttp.org/"><img src="https://img.shields.io/badge/aiohttp-2C5BB4?logo=aiohttp&logoColor=white" alt="aiohttp" /></a>
    <a href="https://docs.astral.sh/uv/"><img src="https://img.shields.io/badge/uv-DE5FE9?logo=uv&logoColor=white" alt="uv" /></a>
    <a href="https://github.com/features/actions"><img src="https://img.shields.io/badge/GitHub%20Actions-2088FF?logo=githubactions&logoColor=white" alt="GitHub Actions" /></a>
  </p>

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
