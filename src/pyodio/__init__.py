"""pyodio — async Python client for odio-api.

High-level, stateful usage (recommended)::

    import pyodio

    async with pyodio.connect("http://odio.local:8018") as odio:
        player = odio.players.find("spotify")
        if player:
            await player.play_pause()
        await odio.audio.set_volume(0.4)

Low-level, stateless usage::

    from pyodio import OdioClient

    async with OdioClient("http://odio.local:8018") as client:
        players = await client.get_players()
"""

from .exceptions import OdioApiError, OdioConnectionError, OdioError, OdioTimeoutError
from .hub import (
    ADDED,
    DISCOVERED,
    POSITION,
    PROGRESS,
    REMOVED,
    UPDATED,
    Audio,
    AudioClient,
    AudioOutput,
    Bluetooth,
    BluetoothDevice,
    OdioHub,
    Player,
    Players,
    Power,
    Service,
    Services,
    Upgrade,
    connect,
)
from .models import (
    AudioClientState,
    AudioOutputState,
    AudioServerState,
    AudioSnapshot,
    Backends,
    BluetoothDeviceState,
    BluetoothState,
    LoopStatus,
    PlaybackStatus,
    PlayerCapabilities,
    PlayerState,
    PowerCapabilities,
    ServerInfo,
    ServiceScope,
    ServiceState,
    UpgradeRunState,
    UpgradeRunStateValue,
    UpgradeStatus,
)
from .rest import DEFAULT_BASE_URL, OdioClient
from .sse import EventStream, OdioEvent, stream_events

__version__ = "0.1.0"

__all__ = [
    "ADDED",
    "DEFAULT_BASE_URL",
    "DISCOVERED",
    "POSITION",
    "PROGRESS",
    "REMOVED",
    "UPDATED",
    "Audio",
    "AudioClient",
    "AudioClientState",
    "AudioOutput",
    "AudioOutputState",
    "AudioServerState",
    "AudioSnapshot",
    "Backends",
    "Bluetooth",
    "BluetoothDevice",
    "BluetoothDeviceState",
    "BluetoothState",
    "EventStream",
    "LoopStatus",
    "OdioApiError",
    "OdioClient",
    "OdioConnectionError",
    "OdioError",
    "OdioEvent",
    "OdioHub",
    "OdioTimeoutError",
    "PlaybackStatus",
    "Player",
    "PlayerCapabilities",
    "PlayerState",
    "Players",
    "Power",
    "PowerCapabilities",
    "ServerInfo",
    "Service",
    "ServiceScope",
    "ServiceState",
    "Services",
    "Upgrade",
    "UpgradeRunState",
    "UpgradeRunStateValue",
    "UpgradeStatus",
    "connect",
    "stream_events",
]
