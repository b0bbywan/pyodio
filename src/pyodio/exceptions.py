"""Exceptions raised by pyodio."""

from __future__ import annotations


class OdioError(Exception):
    """Base class for all pyodio errors."""


class OdioConnectionError(OdioError):
    """The odio-api server could not be reached."""


class OdioTimeoutError(OdioConnectionError):
    """A request to the odio-api server timed out."""


class OdioApiError(OdioError):
    """The odio-api server answered with an error status.

    The server returns plain-text error bodies; the text is available as
    ``message`` and the HTTP status code as ``status``.
    """

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(f"HTTP {status}: {message}" if message else f"HTTP {status}")
        self.status = status
        self.message = message
