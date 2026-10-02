"""Client for Webex CE devices."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

import xows

_LOGGER = logging.getLogger(__name__)

# Seconds to wait for the device to answer a request
REQUEST_TIMEOUT = 10
# Seconds between checks that an idle connection is still alive
KEEPALIVE_INTERVAL = 30
# Seconds to wait between reconnection attempts, doubled after each failure
RECONNECT_MIN_DELAY = 5
RECONNECT_MAX_DELAY = 300
# Seconds the library gets to notice a connection that was closed
CLOSE_GRACE_PERIOD = 1

KEEPALIVE_PATH = ["Status", "SystemUnit", "Uptime"]

type FeedbackCallback = Callable[[dict[str, Any], Any], Any]


@dataclass(frozen=True)
class WebexCEDeviceInfo:
    """Information about a Webex device."""

    name: str
    serial: str
    product: str
    software_version: str


def _consume_result(task: asyncio.Task[None]) -> None:
    """Mark the result of a task as retrieved."""
    if not task.cancelled():
        task.exception()


class WebexCEClient:
    """Client for interacting with Cisco Webex CE devices."""

    def __init__(self, host: str, username: str, password: str) -> None:
        """Initialize the client."""
        self.host = host
        self.username = username
        self.password = password
        self.connected = False
        self._client: xows.XoWSClient | None = None
        self._closed_waiter: asyncio.Task[None] | None = None
        self._subscriptions: dict[str, tuple[list[str], FeedbackCallback]] = {}
        self._listeners: list[Callable[[], None]] = []
        self._stopped = False

    async def connect(self) -> None:
        """Connect to the device.

        Raises xows.AuthenticationFailure when the device rejects the
        credentials.
        """
        self._stopped = False
        await self._async_open()
        self._set_connected(True)

    async def disconnect(self) -> None:
        """Disconnect from the device and stop reconnecting."""
        self._stopped = True
        self.connected = False
        await self._async_close()

    async def async_maintain_connection(self) -> None:
        """Watch the connection and reconnect when it is lost.

        Runs until disconnect() is called. Raises xows.AuthenticationFailure
        when the device rejects the credentials while reconnecting.
        """
        while not self._stopped:
            await self._async_wait_until_lost()
            if self._stopped:
                return
            _LOGGER.info("Device %s is unavailable", self.host)
            self._set_connected(False)
            await self._async_close()
            await self._async_reconnect()

    def add_connection_listener(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Call a listener when the connection state changes.

        Returns a function that removes the listener.
        """
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    async def xget(self, path: list[str]) -> Any:
        """Get a status value from the device."""
        client = self._get_client()
        async with asyncio.timeout(REQUEST_TIMEOUT):
            return await client.xGet(path)

    async def xcommand(self, path: list[str], **params: Any) -> Any:
        """Execute a command on the device."""
        client = self._get_client()
        async with asyncio.timeout(REQUEST_TIMEOUT):
            return await client.xCommand(path, **params)

    async def xset(self, path: list[str | int], value: Any) -> Any:
        """Set a configuration value on the device."""
        client = self._get_client()
        async with asyncio.timeout(REQUEST_TIMEOUT):
            return await client.xSet(path, value)

    async def subscribe_feedback(
        self, feedback_id: str, path: list[str], callback: FeedbackCallback
    ) -> None:
        """Subscribe to feedback for a status path.

        The subscription is restored when the client reconnects.
        """
        client = self._get_client()
        self._subscriptions[feedback_id] = (path, callback)
        async with asyncio.timeout(REQUEST_TIMEOUT):
            await client.subscribe(path, callback, notify_current_value=True)

    async def get_serial_number(self) -> str:
        """Get the device serial number."""
        result = await self.xget(
            ["Status", "SystemUnit", "Hardware", "Module", "SerialNumber"]
        )
        # The value is either returned directly or wrapped in a dict
        if isinstance(result, dict):
            return str(result.get("SerialNumber", ""))
        return str(result)

    async def get_device_info(self) -> WebexCEDeviceInfo:
        """Get device information for the device registry."""
        device_name, product, software, serial = await asyncio.gather(
            self.xget(["Status", "UserInterface", "ContactInfo", "Name"]),
            self.xget(["Status", "SystemUnit", "ProductId"]),
            self.xget(["Status", "SystemUnit", "Software", "Version"]),
            self.get_serial_number(),
        )
        return WebexCEDeviceInfo(
            name=str(device_name) if device_name else "Webex Device",
            serial=serial,
            product=str(product),
            software_version=str(software),
        )

    def _get_client(self) -> xows.XoWSClient:
        """Return the open connection to the device."""
        if self._client is None:
            raise ConnectionError(f"Not connected to {self.host}")
        return self._client

    def _set_connected(self, connected: bool) -> None:
        """Update the connection state and tell the listeners."""
        if connected == self.connected:
            return
        self.connected = connected
        for listener in list(self._listeners):
            listener()

    async def _async_open(self) -> None:
        """Open a connection to the device."""
        client = xows.XoWSClient(
            self.host, username=self.username, password=self.password
        )
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                await client.connect()
        except xows.NotEnabledError as err:
            # The library only treats HTTP 403 as an authentication failure,
            # but a device can also reject the credentials with HTTP 401.
            if 401 in err.args:
                raise xows.AuthenticationFailure(
                    xows.AuthenticationFailure.__doc__, 401
                ) from err
            raise
        self._client = client
        self._closed_waiter = asyncio.ensure_future(client.wait_until_closed())
        self._closed_waiter.add_done_callback(_consume_result)

    async def _async_close(self) -> None:
        """Close the connection to the device, if any."""
        client, self._client = self._client, None
        waiter, self._closed_waiter = self._closed_waiter, None
        if client is not None:
            try:
                await client.disconnect()
            except (xows.XoWSError, OSError) as err:
                _LOGGER.debug("Error closing connection to %s: %s", self.host, err)
        if waiter is not None and not waiter.done():
            # Give the library a moment to notice the closed connection before
            # giving up on it, for example when its read loop has stopped.
            await asyncio.wait((waiter,), timeout=CLOSE_GRACE_PERIOD)
            waiter.cancel()

    async def _async_wait_until_lost(self) -> None:
        """Return once the connection to the device is lost."""
        waiter = self._closed_waiter
        if waiter is None:
            return
        while not self._stopped:
            done, _ = await asyncio.wait((waiter,), timeout=KEEPALIVE_INTERVAL)
            if done:
                _LOGGER.debug("Connection to %s was closed", self.host)
                return
            # A connection that silently died is only noticed by using it
            try:
                await self.xget(KEEPALIVE_PATH)
            except (xows.XoWSError, OSError) as err:
                _LOGGER.debug("Connection to %s is not responding: %s", self.host, err)
                return

    async def _async_reconnect(self) -> None:
        """Reconnect to the device and restore the feedback subscriptions."""
        delay = RECONNECT_MIN_DELAY
        while True:
            await asyncio.sleep(delay)
            if self._stopped:
                return
            try:
                await self._async_open()
                await self._async_resubscribe()
            except xows.AuthenticationFailure:
                await self._async_close()
                raise
            except (xows.XoWSError, OSError) as err:
                _LOGGER.debug("Could not reconnect to %s: %s", self.host, err)
                await self._async_close()
                delay = min(delay * 2, RECONNECT_MAX_DELAY)
                continue
            if self._stopped:
                await self._async_close()
                return
            _LOGGER.info("Device %s is back online", self.host)
            self._set_connected(True)
            return

    async def _async_resubscribe(self) -> None:
        """Restore the feedback subscriptions on a new connection."""
        client = self._get_client()
        async with asyncio.timeout(REQUEST_TIMEOUT):
            await asyncio.gather(
                *(
                    client.subscribe(path, callback, notify_current_value=True)
                    for path, callback in self._subscriptions.values()
                )
            )
