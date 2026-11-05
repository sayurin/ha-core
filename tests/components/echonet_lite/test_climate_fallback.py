"""Tests for the ECHONET Lite climate platform fallback logic."""

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
    """Enable the climate platform for these tests."""

    return [Platform.CLIMATE]


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_climate_target_temp_fallback(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure climate entities fall back to 0xB3 if 0xB5/0xB6 are not supported."""

    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010104").hex()
    seoj = bytes.fromhex("013001")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    # Create device that lacks a required EPC (0xB3)
    # Climate requires: 0x80 (status), 0xB0 (mode), 0xB3 (target temp), 0xBB (room temp)
    required_epcs = frozenset({0x80, 0xB0, 0xB3, 0xBB})

    property_map_frame = TestFrame(
        tid=0,
        seoj=seoj,
        deoj=bytes.fromhex("0ef001"),
        esv=ESV.GET_RES,
        properties=[
            TestProperty(
                epc=EPC_GET_PROPERTY_MAP,
                # Required EPCs omit 0xB3 -> should reject entity creation
                edt=make_property_map_edt(*sorted(required_epcs - {0xB3})),
            ),
            TestProperty(
                epc=EPC_MANUFACTURER_CODE,
                edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
            ),
        ],
    )
    await coordinator.device_manager.process_frame_event(
        make_frame_event(
            property_map_frame,
            received_at=9.0,
            node_id=node_id,
            eoj=eoj,
        ),
    )
    await hass.async_block_till_done()

    # Send status: COOL mode (0x42), Target Temp 0xB3 = 25C (0x19)
    frame = TestFrame(
        tid=1,
        seoj=seoj,
        deoj=bytes.fromhex("0ef001"),
        esv=ESV.INF,
        properties=[
            TestProperty(epc=0x80, edt=b"\x30"),  # ON
            TestProperty(epc=0xB0, edt=b"\x42"),  # COOL
            TestProperty(epc=0xB3, edt=b"\x19"),  # 25C
            TestProperty(epc=0xBB, edt=b"\x1e"),  # Room 30C
        ],
    )
    await coordinator.device_manager.process_frame_event(
        make_frame_event(
            frame,
            received_at=10.0,
            node_id=node_id,
            eoj=eoj,
        ),
    )
    await hass.async_block_till_done()

    # 必須EPC不足なのでエンティティは生成されない
    entity_id = entity_registry.async_get_entity_id(
        "climate", DOMAIN, "010104-013001-climate"
    )
    assert entity_id is None
