"""Tests for the ECHONET Lite config flow."""

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant import config_entries
from homeassistant.components.echonet_lite.config_flow import (
    _async_get_interface_options,
)
from homeassistant.components.echonet_lite.const import (
    CONF_INTERFACE,
    DEFAULT_INTERFACE,
    DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from tests.common import MockConfigEntry

pytestmark = pytest.mark.usefixtures("mock_echonet_lite_client")


async def test_user_flow_success(
    hass: HomeAssistant, mock_async_validate_network: AsyncMock
) -> None:
    """Test the happy path of the user initiated config flow."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_INTERFACE: DEFAULT_INTERFACE}


async def test_user_flow_with_custom_interface(
    hass: HomeAssistant, mock_async_validate_network: AsyncMock
) -> None:
    """Test user flow with a custom interface selection."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_INTERFACE: "10.10.10.10"},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_INTERFACE: "10.10.10.10"}


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    """Test errors are surfaced when the socket cannot bind."""

    with patch(
        "homeassistant.components.echonet_lite.config_flow.create_multicast_socket",
        AsyncMock(side_effect=OSError("boom")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_flow_single_instance_abort(hass: HomeAssistant) -> None:
    """Ensure only a single config entry can exist."""

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reconfigure_flow(
    hass: HomeAssistant, mock_async_validate_network: AsyncMock
) -> None:
    """Test reconfiguring an existing entry changes interface only."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "192.168.1.50"})
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    # Change interface to a new value, other options should be preserved
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_INTERFACE: "10.10.10.10"},
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    # Interface changed in data, options preserved
    assert entry.data == {CONF_INTERFACE: "10.10.10.10"}


async def test_interface_options_skip_disabled_adapter(hass: HomeAssistant) -> None:
    """Disabled adapters should be skipped when building interface options."""

    adapters = [
        {
            "name": "eth0",
            "enabled": False,
            "ipv4": [{"address": "192.168.1.10"}],
        },
        {
            "name": "eth1",
            "enabled": True,
            "ipv4": [
                {"address": "192.168.1.20"},
                {"address": "127.0.0.1"},
            ],
        },
    ]

    with patch(
        "homeassistant.components.echonet_lite.config_flow.network.async_get_adapters",
        AsyncMock(return_value=adapters),
    ):
        options = await _async_get_interface_options(hass)

    values = [option["value"] for option in options]
    # Auto option is always present, disabled adapter and loopback are excluded
    assert values == [DEFAULT_INTERFACE, "192.168.1.20"]


async def test_interface_options_handles_oserror(hass: HomeAssistant) -> None:
    """An OSError while enumerating adapters falls back to the auto option."""

    with patch(
        "homeassistant.components.echonet_lite.config_flow.network.async_get_adapters",
        AsyncMock(side_effect=OSError("boom")),
    ):
        options = await _async_get_interface_options(hass)

    values = [option["value"] for option in options]
    assert values == [DEFAULT_INTERFACE]
