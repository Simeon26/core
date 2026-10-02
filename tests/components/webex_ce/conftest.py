"""Common fixtures for the Webex devices tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.webex_ce import PLATFORMS
from homeassistant.components.webex_ce.client import WebexCEClient, WebexCEDeviceInfo
from homeassistant.components.webex_ce.const import DOMAIN
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry

SERIAL = "FOC2341P2G6"
DEVICE_INFO = WebexCEDeviceInfo(
    name="Test Device",
    serial=SERIAL,
    product="Cisco Webex Room Kit",
    software_version="ce9.15.3.17",
)


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with patch(
        "homeassistant.components.webex_ce.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        yield mock_setup_entry


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Device",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password123",
        },
        unique_id=SERIAL,
    )


@pytest.fixture
def xget_responses() -> dict[tuple[str, ...], Any]:
    """Return the values returned by xget, keyed by status path.

    An exception instance as value is raised instead of returned.
    """
    return {}


@pytest.fixture
def bookings() -> list[dict[str, Any]]:
    """Return the bookings returned by xCommand Bookings List."""
    return []


def create_mock_client(
    device_info: WebexCEDeviceInfo,
    xget_responses: dict[tuple[str, ...], Any],
    bookings: list[dict[str, Any]],
) -> MagicMock:
    """Create a mocked WebexCEClient.

    The mock records feedback subscriptions in `feedback_callbacks` and
    connection listeners in `connection_listeners`.
    """
    client = MagicMock(spec=WebexCEClient)
    client.connected = True
    client.host = "192.168.1.100"
    client.feedback_callbacks = {}
    client.connection_listeners = []

    def _add_connection_listener(listener: Callable[[], None]) -> Callable[[], None]:
        client.connection_listeners.append(listener)
        return lambda: client.connection_listeners.remove(listener)

    async def _subscribe_feedback(
        feedback_id: str, path: list[str], callback: Callable
    ) -> None:
        client.feedback_callbacks.setdefault(tuple(path), []).append(callback)

    async def _xget(path: list[str]) -> Any:
        value = xget_responses.get(tuple(path))
        if isinstance(value, Exception):
            raise value
        return value

    async def _xcommand(path: list[str], **params: Any) -> Any:
        if path == ["Bookings", "List"]:
            return {"Booking": bookings}
        return {}

    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.xget = AsyncMock(side_effect=_xget)
    client.xset = AsyncMock(return_value=True)
    client.xcommand = AsyncMock(side_effect=_xcommand)
    client.subscribe_feedback = AsyncMock(side_effect=_subscribe_feedback)
    client.add_connection_listener = MagicMock(side_effect=_add_connection_listener)
    client.async_maintain_connection = AsyncMock()
    client.get_serial_number = AsyncMock(return_value=device_info.serial)
    client.get_device_info = AsyncMock(return_value=device_info)
    return client


@pytest.fixture
def mock_webex_client(
    xget_responses: dict[tuple[str, ...], Any],
    bookings: list[dict[str, Any]],
) -> Generator[MagicMock]:
    """Return a mocked WebexCEClient used by the integration."""
    client = create_mock_client(DEVICE_INFO, xget_responses, bookings)
    with patch("homeassistant.components.webex_ce.WebexCEClient", return_value=client):
        yield client


@pytest.fixture
def platforms() -> list[Platform]:
    """Return the platforms to set up."""
    return PLATFORMS


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webex_client: MagicMock,
    platforms: list[Platform],
) -> MockConfigEntry:
    """Set up the Webex devices integration for testing."""
    mock_config_entry.add_to_hass(hass)

    with patch("homeassistant.components.webex_ce.PLATFORMS", platforms):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    return mock_config_entry


DEVICE_STATUS: dict[tuple[str, ...], Any] = {
    ("Status", "UserInterface", "ContactInfo", "Name"): DEVICE_INFO.name,
    ("Status", "SystemUnit", "ProductId"): DEVICE_INFO.product,
    ("Status", "SystemUnit", "Software", "Version"): DEVICE_INFO.software_version,
    ("Status", "SystemUnit", "Hardware", "Module", "SerialNumber"): SERIAL,
}


class FakeXoWSClient:
    """Fake connection of the xows library to a device."""

    def __init__(
        self, factory: FakeXoWS, url: str, username: str, password: str
    ) -> None:
        """Initialize the fake connection."""
        self.factory = factory
        self.url = url
        self.username = username
        self.password = password
        self.subscriptions: list[tuple[list[str], Callable]] = []
        self.xGet = AsyncMock(side_effect=self._xget)
        self.xCommand = AsyncMock(return_value={})
        self.xSet = AsyncMock(return_value=True)
        self.disconnect = AsyncMock(side_effect=self._disconnect)
        self._closed: asyncio.Future[None] | None = None

    async def connect(self) -> None:
        """Open the fake connection, or raise the next queued error."""
        if self.factory.connect_errors:
            raise self.factory.connect_errors.pop(0)
        self._closed = asyncio.get_running_loop().create_future()
        if self.factory.on_connect:
            await self.factory.on_connect()

    async def _disconnect(self) -> None:
        """Close the fake connection."""
        if self.factory.resolve_on_disconnect:
            self.close()

    def close(self) -> None:
        """Close the connection as the device would."""
        assert self._closed is not None
        if not self._closed.done():
            self._closed.set_result(None)

    async def wait_until_closed(self) -> None:
        """Wait until the connection is closed."""
        assert self._closed is not None
        await self._closed

    async def _xget(self, path: list[str]) -> Any:
        """Return a status value of the fake device."""
        return self.factory.status.get(tuple(path))

    async def subscribe(
        self, query: list[str], handler: Callable, notify_current_value: bool = False
    ) -> int:
        """Record a feedback subscription."""
        assert notify_current_value
        self.subscriptions.append((query, handler))
        return len(self.subscriptions)


class FakeXoWS:
    """Factory for fake xows connections that keeps track of them."""

    def __init__(self) -> None:
        """Initialize the factory."""
        self.clients: list[FakeXoWSClient] = []
        self.connect_errors: list[Exception] = []
        self.status: dict[tuple[str, ...], Any] = dict(DEVICE_STATUS)
        self.resolve_on_disconnect = True
        self.on_connect: Callable[[], Awaitable[None]] | None = None

    def __call__(
        self, url_or_host: str, username: str = "admin", password: str = ""
    ) -> FakeXoWSClient:
        """Create a fake connection."""
        client = FakeXoWSClient(self, url_or_host, username, password)
        self.clients.append(client)
        return client

    @property
    def latest(self) -> FakeXoWSClient:
        """Return the most recently created connection."""
        return self.clients[-1]


@pytest.fixture
def mock_xows() -> Generator[FakeXoWS]:
    """Replace the xows connection with a fake and speed up reconnecting."""
    factory = FakeXoWS()
    with (
        patch("homeassistant.components.webex_ce.client.xows.XoWSClient", new=factory),
        patch("homeassistant.components.webex_ce.client.RECONNECT_MIN_DELAY", 0),
        patch("homeassistant.components.webex_ce.client.CLOSE_GRACE_PERIOD", 0),
    ):
        yield factory
