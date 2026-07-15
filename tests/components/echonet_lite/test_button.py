"""Tests for the ECHONET Lite button platform."""

from unittest.mock import AsyncMock

from pyhems import EOJ
import pytest

from homeassistant.components.echonet_lite.const import DOMAIN
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import EPC_MANUFACTURER_CODE, TEST_MANUFACTURER_CODE, TestProperty

from tests.common import MockConfigEntry


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable the button platform for these tests."""

    return [Platform.BUTTON]


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_reset_button(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test button entity for crime prevention sensor reset (0x0002).

    This tests that:
    - Button entity is created when write-only EPC 0xBF is in SET property map
    - Button press service call sends correct command (EDT=0x00 for reset)
    """
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=0x9E, edt=b"\x01\xbf"),  # SET property map: 0xBF
        TestProperty(epc=0x9F, edt=b"\x02\xb0\xb1"),  # GET property map: 0xB0, 0xB1
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xB0, edt=b"\x41"),  # Detection threshold level
        TestProperty(epc=0xB1, edt=b"\x42"),  # Invasion occurrence status (no invasion)
    ]

    await coordinator.device_manager.setup_device("010201", EOJ(0x000201))
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "button", DOMAIN, "010201-000201-bf"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    # Button entities don't have a meaningful state
    assert state.state is not None

    # Reset mock for explicit service call test
    mock_echonet_lite_client.set_properties.reset_mock()

    # Test button.press service call
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": entity_id},
        blocking=True,
    )

    assert mock_echonet_lite_client.set_properties.call_count == 1
    call = mock_echonet_lite_client.set_properties.call_args_list[0]
    assert call.kwargs["node_id"] == "010201"
    assert call.kwargs["deoj"] == EOJ(0x000201)
    assert call.kwargs["properties"][0].epc == 0xBF
    assert call.kwargs["properties"][0].edt == b"\x00"  # Reset command


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_emergency_button_reset(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test button entity for emergency button reset (0x0003).

    Similar to crime prevention sensor, but for emergency button class.
    """
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=0x9E, edt=b"\x01\xbf"),  # SET property map: 0xBF
        TestProperty(epc=0x9F, edt=b"\x01\xb1"),  # GET property map: 0xB1
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(
            epc=0xB1, edt=b"\x42"
        ),  # Emergency occurrence status (no emergency)
    ]

    await coordinator.device_manager.setup_device("010301", EOJ(0x000301))
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "button", DOMAIN, "010301-000301-bf"
    )
    assert entity_id is not None

    # Test button.press service call
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": entity_id},
        blocking=True,
    )

    assert mock_echonet_lite_client.set_properties.call_count == 1
    call = mock_echonet_lite_client.set_properties.call_args_list[0]
    assert call.kwargs["properties"][0].epc == 0xBF
    assert call.kwargs["properties"][0].edt == b"\x00"  # Reset command
    assert call.kwargs["deoj"] == EOJ(0x000301)


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_button_not_created_when_not_in_set_map(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that button entity is NOT created when EPC not in SET property map.

    Even though the entity definition exists, if the device doesn't support
    writing to EPC 0xBF, no button should be created.
    """
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    # EPC 0xBF NOT in SET property map
    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=0x9E, edt=b"\x00"),  # SET property map: empty
        TestProperty(epc=0x9F, edt=b"\x02\xb0\xb1"),  # GET property map: 0xB0, 0xB1
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xB0, edt=b"\x41"),
        TestProperty(epc=0xB1, edt=b"\x42"),
    ]

    await coordinator.device_manager.setup_device("010202", EOJ(0x000201))
    await hass.async_block_till_done()

    # Verify NO button entity was created
    entity_id = entity_registry.async_get_entity_id(
        "button", DOMAIN, "010202-000201-bf"
    )
    assert entity_id is None
