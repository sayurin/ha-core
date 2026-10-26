"""Tests for the ECHONET Lite number platform."""

import time
from unittest.mock import AsyncMock

from pyhems import EOJ, ESV
import pytest

from homeassistant.components.echonet_lite.const import DOMAIN
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import PERCENTAGE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import (
    EPC_GET_PROPERTY_MAP,
    EPC_MANUFACTURER_CODE,
    EPC_SET_PROPERTY_MAP,
    TEST_MANUFACTURER_CODE,
    TestFrame,
    TestProperty,
    make_frame_event,
    make_property_map_edt,
)

from tests.common import MockConfigEntry

EPC_CURRENT_LIMIT = 0x87


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable the number platform for these tests."""
    return [Platform.NUMBER]


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_number_entity_state(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure number entities surface decoded values."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010201").hex()
    seoj = bytes.fromhex("001101")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(EPC_CURRENT_LIMIT),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(EPC_CURRENT_LIMIT),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_CURRENT_LIMIT, edt=b"\x32"),  # 50%
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "number", DOMAIN, "010201-001101-87_0"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "50"
    assert state.attributes["unit_of_measurement"] == PERCENTAGE


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_number_set_value(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure async_set_native_value sends the correct ECHONET Lite command."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010202").hex()
    seoj = bytes.fromhex("001101")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(EPC_CURRENT_LIMIT),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(EPC_CURRENT_LIMIT),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_CURRENT_LIMIT, edt=b"\x32"),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "number", DOMAIN, "010202-001101-87_0"
    )
    assert entity_id is not None

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_VALUE: 75},
        target={"entity_id": entity_id},
        blocking=True,
    )

    mock_echonet_lite_client.send.assert_called_once()
    sent_frame = mock_echonet_lite_client.send.call_args[0][1]
    assert sent_frame.properties[0].epc == EPC_CURRENT_LIMIT
    assert sent_frame.properties[0].edt == b"\x4b"  # 75


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_number_updates_on_frame_event(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure number entities track subsequent updates."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010203").hex()
    seoj = bytes.fromhex("001101")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(EPC_CURRENT_LIMIT),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(EPC_CURRENT_LIMIT),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_CURRENT_LIMIT, edt=b"\x32"),  # 50%
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "number", DOMAIN, "010203-001101-87_0"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "50"

    # Simulate update via frame event
    update_frame = TestFrame(
        tid=2,
        seoj=seoj,
        deoj=bytes.fromhex("05ff01"),
        esv=ESV.GET_RES,
        properties=[
            TestProperty(epc=EPC_CURRENT_LIMIT, edt=b"\x64"),  # 100%
        ],
    )
    await coordinator.device_manager.process_frame_event(
        make_frame_event(
            update_frame,
            received_at=time.monotonic(),
            node_id=node_id,
            eoj=eoj,
        ),
    )
    await hass.async_block_till_done()

    state2 = hass.states.get(entity_id)
    assert state2 is not None
    assert state2.state == "100"
