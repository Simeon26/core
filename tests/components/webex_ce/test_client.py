"""Test the Webex CE client module."""

import asyncio
from collections.abc import Callable
from unittest.mock import MagicMock, patch

import pytest
import xows

from homeassistant.components.webex_ce.client import WebexCEClient

from .conftest import DEVICE_INFO, FakeXoWS

HOST = "192.168.1.100"


async def _run_until(condition: Callable[[], bool]) -> None:
    """Let background tasks run until a condition is met."""
    for _ in range(1000):
        if condition():
            return
        await asyncio.sleep(0.001)
    pytest.fail("Condition was not met")


@pytest.fixture
def client() -> WebexCEClient:
    """Return a client for the fake device."""
    return WebexCEClient(HOST, "admin", "password")


async def test_connect_and_disconnect(
    client: WebexCEClient, mock_xows: FakeXoWS
) -> None:
    """Test connecting and disconnecting tells the listeners."""
    listener = MagicMock()
    remove_listener = client.add_connection_listener(listener)
    assert client.connected is False

    await client.connect()

    assert client.connected is True
    listener.assert_called_once_with()
    assert mock_xows.latest.url == HOST
    assert mock_xows.latest.username == "admin"
    assert mock_xows.latest.password == "password"

    remove_listener()
    await client.disconnect()

    assert client.connected is False
    mock_xows.latest.disconnect.assert_awaited_once()
    listener.assert_called_once_with()
    with pytest.raises(ConnectionError):
        await client.xget(["Status", "SystemUnit"])


async def test_disconnect_when_not_connected(client: WebexCEClient) -> None:
    """Test disconnecting without a connection does nothing."""
    await client.disconnect()

    assert client.connected is False


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (xows.AuthenticationFailure("Invalid", 403), xows.AuthenticationFailure),
        (xows.NotEnabledError("Not enabled", 401), xows.AuthenticationFailure),
        (xows.NotEnabledError("Not enabled", 404), xows.NotEnabledError),
        (ConnectionError("Refused"), ConnectionError),
    ],
)
async def test_connect_failure(
    client: WebexCEClient,
    mock_xows: FakeXoWS,
    error: Exception,
    expected: type[Exception],
) -> None:
    """Test connection failures are raised."""
    mock_xows.connect_errors.append(error)

    with pytest.raises(expected):
        await client.connect()

    assert client.connected is False


async def test_requests(client: WebexCEClient, mock_xows: FakeXoWS) -> None:
    """Test requests are passed to the connection."""
    await client.connect()
    connection = mock_xows.latest
    connection.xCommand.return_value = {"result": "success"}

    assert await client.xget(["Status", "SystemUnit", "ProductId"]) == (
        DEVICE_INFO.product
    )
    assert await client.xcommand(["Dial"], Number="1234") == {"result": "success"}
    connection.xCommand.assert_awaited_once_with(["Dial"], Number="1234")

    path = ["Configuration", "Audio", "DefaultVolume"]
    assert await client.xset(path, 50) is True
    connection.xSet.assert_awaited_once_with(path, 50)

    callback = MagicMock()
    await client.subscribe_feedback("test_id", ["Status", "Call"], callback)
    assert connection.subscriptions == [(["Status", "Call"], callback)]

    await client.disconnect()


@pytest.mark.parametrize(
    "request_fn",
    [
        lambda client: client.xget(["Status", "SystemUnit"]),
        lambda client: client.xcommand(["Dial"], Number="1234"),
        lambda client: client.xset(["Configuration", "Audio", "DefaultVolume"], 5),
        lambda client: client.subscribe_feedback("id", ["Status"], MagicMock()),
    ],
)
async def test_requests_not_connected(client: WebexCEClient, request_fn) -> None:
    """Test requests fail without a connection."""
    with pytest.raises(ConnectionError, match="Not connected to 192.168.1.100"):
        await request_fn(client)


async def test_get_device_info(client: WebexCEClient, mock_xows: FakeXoWS) -> None:
    """Test reading the device information."""
    await client.connect()

    assert await client.get_device_info() == DEVICE_INFO

    # The serial number may be wrapped and the name may be empty
    mock_xows.status[("Status", "SystemUnit", "Hardware", "Module", "SerialNumber")] = {
        "SerialNumber": "FOC0000000"
    }
    mock_xows.status[("Status", "UserInterface", "ContactInfo", "Name")] = ""
    device_info = await client.get_device_info()
    assert device_info.serial == "FOC0000000"
    assert device_info.name == "Webex Device"

    mock_xows.latest.xGet.side_effect = xows.CommandError("Device error")
    with pytest.raises(xows.CommandError, match="Device error"):
        await client.get_device_info()

    await client.disconnect()


async def test_reconnect_after_connection_closed(
    client: WebexCEClient, mock_xows: FakeXoWS, caplog: pytest.LogCaptureFixture
) -> None:
    """Test the client reconnects and restores subscriptions."""
    await client.connect()
    callback = MagicMock()
    await client.subscribe_feedback("call", ["Status", "Call"], callback)
    # Subscribing again with the same ID replaces the subscription
    await client.subscribe_feedback("call", ["Status", "Call"], callback)
    states: list[bool] = []
    client.add_connection_listener(lambda: states.append(client.connected))
    first = mock_xows.latest

    task = asyncio.create_task(client.async_maintain_connection())
    first.close()
    await _run_until(lambda: len(mock_xows.clients) == 2 and client.connected)

    assert states == [False, True]
    assert "Device 192.168.1.100 is unavailable" in caplog.text
    assert "Device 192.168.1.100 is back online" in caplog.text
    first.disconnect.assert_awaited_once()
    assert mock_xows.latest.subscriptions == [(["Status", "Call"], callback)]

    await client.disconnect()
    await task
    assert states == [False, True]


async def test_reconnect_retries(client: WebexCEClient, mock_xows: FakeXoWS) -> None:
    """Test the client keeps trying to reconnect with a growing delay."""
    await client.connect()
    mock_xows.connect_errors.extend(
        [ConnectionError("Refused"), TimeoutError(), xows.NotEnabledError("x", 404)]
    )

    with (
        patch("homeassistant.components.webex_ce.client.RECONNECT_MIN_DELAY", 0.001),
        patch("homeassistant.components.webex_ce.client.RECONNECT_MAX_DELAY", 0.002),
    ):
        task = asyncio.create_task(client.async_maintain_connection())
        mock_xows.latest.close()
        await _run_until(lambda: len(mock_xows.clients) == 5 and client.connected)

    assert not mock_xows.connect_errors
    assert len(mock_xows.clients) == 5

    await client.disconnect()
    await task


async def test_reconnect_resubscribe_failure(
    client: WebexCEClient, mock_xows: FakeXoWS
) -> None:
    """Test a failure to restore subscriptions closes the new connection."""
    await client.connect()
    await client.subscribe_feedback("call", ["Status", "Call"], MagicMock())

    async def fail_subscribe(*args, **kwargs) -> None:
        mock_xows.on_connect = None
        raise xows.SubscriberCountExceeded("Too many subscriptions")

    async def on_connect() -> None:
        mock_xows.latest.subscribe = fail_subscribe

    mock_xows.on_connect = on_connect
    task = asyncio.create_task(client.async_maintain_connection())
    mock_xows.latest.close()
    await _run_until(lambda: len(mock_xows.clients) == 3 and client.connected)

    assert len(mock_xows.clients) == 3
    mock_xows.clients[1].disconnect.assert_awaited_once()

    await client.disconnect()
    await task


async def test_keepalive_failure(client: WebexCEClient, mock_xows: FakeXoWS) -> None:
    """Test a connection that stops responding is replaced."""
    await client.connect()
    first = mock_xows.latest
    # The library does not notice the connection is gone
    mock_xows.resolve_on_disconnect = False
    first.xGet.side_effect = [None, TimeoutError()]

    with patch("homeassistant.components.webex_ce.client.KEEPALIVE_INTERVAL", 0):
        task = asyncio.create_task(client.async_maintain_connection())
        await _run_until(lambda: len(mock_xows.clients) == 2 and client.connected)

    assert first.xGet.await_count == 2
    first.disconnect.assert_awaited_once()

    mock_xows.resolve_on_disconnect = True
    await client.disconnect()
    await task


async def test_close_error(client: WebexCEClient, mock_xows: FakeXoWS) -> None:
    """Test errors while closing the connection are ignored."""
    await client.connect()
    mock_xows.latest.disconnect.side_effect = OSError("Already closed")
    mock_xows.resolve_on_disconnect = False

    await client.disconnect()

    assert client.connected is False


async def test_reconnect_authentication_failure(
    client: WebexCEClient, mock_xows: FakeXoWS
) -> None:
    """Test the client stops when the credentials are rejected."""
    await client.connect()
    mock_xows.connect_errors.append(xows.AuthenticationFailure("Invalid", 403))

    task = asyncio.create_task(client.async_maintain_connection())
    mock_xows.latest.close()

    with pytest.raises(xows.AuthenticationFailure):
        await task
    assert client.connected is False


async def test_disconnect_while_reconnecting(
    client: WebexCEClient, mock_xows: FakeXoWS
) -> None:
    """Test a connection opened after disconnecting is closed again."""
    await client.connect()

    async def on_connect() -> None:
        mock_xows.on_connect = None
        await client.disconnect()

    mock_xows.on_connect = on_connect
    task = asyncio.create_task(client.async_maintain_connection())
    mock_xows.latest.close()
    await task

    assert client.connected is False
    assert len(mock_xows.clients) == 2
    mock_xows.latest.disconnect.assert_awaited_once()


async def test_disconnect_while_waiting_to_reconnect(
    client: WebexCEClient, mock_xows: FakeXoWS
) -> None:
    """Test disconnecting stops waiting to reconnect."""
    await client.connect()

    with patch("homeassistant.components.webex_ce.client.RECONNECT_MIN_DELAY", 0.01):
        task = asyncio.create_task(client.async_maintain_connection())
        mock_xows.latest.close()
        await _run_until(lambda: not client.connected)
        await client.disconnect()
        await task

    assert len(mock_xows.clients) == 1


async def test_maintain_connection_without_connection(
    client: WebexCEClient, mock_xows: FakeXoWS
) -> None:
    """Test maintaining the connection connects when there is none."""
    task = asyncio.create_task(client.async_maintain_connection())
    await _run_until(lambda: client.connected)

    await client.disconnect()
    await task
