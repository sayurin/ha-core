"""Tests for the ECHONET Lite binary sensor platform."""

import time

from pyhems import EOJ, ESV
import pytest

from homeassistant.components.echonet_lite.const import DOMAIN
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import (
    EPC_GET_PROPERTY_MAP,
    EPC_MANUFACTURER_CODE,
    TEST_MANUFACTURER_CODE,
    TestFrame,
    TestProperty,
    make_frame_event,
    make_property_map_edt,
)


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable the binary sensor platform for these tests."""

    return [Platform.BINARY_SENSOR]


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_occupancy_status(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure occupancy sensors surface status correctly."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010101").hex()
    eoj = EOJ(int.from_bytes(bytes.fromhex("026f01"), "big"))

    entry.runtime_data.client.get.side_effect = [
        [
            TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xE4)),
            TestProperty(
                epc=EPC_MANUFACTURER_CODE,
                edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
            ),
            TestProperty(epc=0xE4, edt=b"\x41"),
        ],
        [
            TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xE4)),
            TestProperty(
                epc=EPC_MANUFACTURER_CODE,
                edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
            ),
            TestProperty(epc=0xE4, edt=b"\x42"),
        ],
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "010101-026f01-e4"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"

    # Simulate update via frame event
    update_frame = TestFrame(
        tid=2,
        seoj=int(eoj).to_bytes(3, "big"),
        deoj=bytes.fromhex("05ff01"),
        esv=ESV.GET_RES,
        properties=[
            TestProperty(epc=0xE4, edt=b"\x42"),
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
    assert state2.state == "off"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_human_detection_sensor(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure human detection sensors register under binary_sensor domain."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010101").hex()
    eoj = EOJ(int.from_bytes(bytes.fromhex("000701"), "big"))

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xB1)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xB1, edt=b"\x41"),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "010101-000701-b1"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"
    # "Human detection status" name → OCCUPANCY via keyword inference
    assert state.attributes["device_class"] == "occupancy"
