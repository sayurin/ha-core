"""Tests for the ECHONET Lite time platform."""

from unittest.mock import AsyncMock

from pyhems import EOJ
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.echonet_lite.const import DOMAIN
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .conftest import TestProperty, make_property_map_edt


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable the time platform."""
    return [Platform.TIME]


@pytest.mark.usefixtures("mock_echonet_lite_client")
@pytest.mark.parametrize(
    ("class_code", "epc", "edt", "platform", "state"),
    [(0x0130, 0x97, "081e", Platform.TIME, "08:30:00")],
)
async def test_platform_state(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    class_code: int,
    epc: int,
    edt: str,
    platform: Platform,
    state: str,
) -> None:
    """Create real entities through platform forwarding and property maps."""
    await hass.config.async_set_time_zone("UTC")
    entry = init_integration
    entry.runtime_data.client.get.return_value = [
        TestProperty(0x9F, make_property_map_edt(epc)),
        TestProperty(0x9E, make_property_map_edt(epc)),
        TestProperty(0x8A, b"\x00\x00\x05"),
        TestProperty(epc, bytes.fromhex(edt)),
    ]
    await entry.runtime_data.coordinator.device_manager.setup_device(
        "010201", EOJ((class_code << 8) | 1)
    )
    await hass.async_block_till_done()
    entity_id = entity_registry.async_get_entity_id(
        platform, DOMAIN, f"010201-{class_code:04x}01-{epc:02x}_0"
    )
    assert entity_id is not None
    result = hass.states.get(entity_id)
    assert result is not None
    assert result.state == state


@pytest.mark.parametrize(
    ("class_code", "epc", "initial", "platform", "field", "value", "encoded"),
    [(0x0130, 0x97, "081e", "time", "time", "09:15:00", "090f")],
)
async def test_service_write(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
    class_code: int,
    epc: int,
    initial: str,
    platform: str,
    field: str,
    value: str | int,
    encoded: str,
) -> None:
    """Real HA services send precise values, using HA timezone for datetime."""
    await hass.config.async_set_time_zone("Asia/Tokyo")
    entry = init_integration
    mock_echonet_lite_client.get.return_value = [
        TestProperty(0x9F, make_property_map_edt(epc)),
        TestProperty(0x9E, make_property_map_edt(epc)),
        TestProperty(0x8A, b"\x00\x00\x05"),
        TestProperty(epc, bytes.fromhex(initial)),
    ]
    await entry.runtime_data.coordinator.device_manager.setup_device(
        "010201", EOJ((class_code << 8) | 1)
    )
    await hass.async_block_till_done()
    entity_id = entity_registry.async_get_entity_id(
        platform, DOMAIN, f"010201-{class_code:04x}01-{epc:02x}_0"
    )
    assert entity_id is not None
    await hass.services.async_call(
        platform, "set_value", {"entity_id": entity_id, field: value}, blocking=True
    )
    properties = mock_echonet_lite_client.set_properties.call_args.kwargs["properties"]
    assert [(prop.epc, prop.edt.hex()) for prop in properties] == [(epc, encoded)]


@pytest.mark.usefixtures("mock_echonet_lite_client")
async def test_service_precision_failure(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Minute-only clocks reject seconds as a user-visible error before SET."""
    entry = init_integration
    entry.runtime_data.client.get.return_value = [
        TestProperty(0x9F, make_property_map_edt(0x97)),
        TestProperty(0x9E, make_property_map_edt(0x97)),
        TestProperty(0x8A, b"\x00\x00\x05"),
        TestProperty(0x97, b"\x08\x1e"),
    ]
    await entry.runtime_data.coordinator.device_manager.setup_device(
        "010201", EOJ(0x013001)
    )
    await hass.async_block_till_done()
    entity_id = entity_registry.async_get_entity_id(
        "time", DOMAIN, "010201-013001-97_0"
    )
    assert entity_id is not None
    with pytest.raises(HomeAssistantError, match="cannot be represented"):
        await hass.services.async_call(
            "time",
            "set_value",
            {"entity_id": entity_id, "time": "08:30:01"},
            blocking=True,
        )
