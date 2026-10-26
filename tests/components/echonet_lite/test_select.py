"""Tests for the ECHONET Lite select platform."""

import asyncio
import time
from unittest.mock import AsyncMock

from pyhems import EOJ, ESV
import pytest

from homeassistant.components.echonet_lite.const import (
    DOMAIN,
    EPC_INSTALLATION_LOCATION,
)
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .conftest import (
    EPC_MANUFACTURER_CODE,
    TEST_MANUFACTURER_CODE,
    TestFrame,
    TestProperty,
    make_frame_event,
    make_property_map_edt,
)

from tests.common import MockConfigEntry


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable the select platform for these tests."""

    return [Platform.SELECT]


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_select_air_flow_direction(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test select entities for air conditioner airflow direction (0x0130).

    Validates creation from property maps, option decoding, and writing EDTs.
    """

    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    # Initial get response with property maps and current select values
    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=0x9E, edt=make_property_map_edt(0xA1, 0xA4, 0xA5)),
        TestProperty(epc=0x9F, edt=make_property_map_edt(0xA1, 0xA4, 0xA5)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xA1, edt=b"\x41"),  # auto
        TestProperty(epc=0xA4, edt=b"\x41"),  # uppermost
        TestProperty(epc=0xA5, edt=b"\x51"),  # right
    ]

    await coordinator.device_manager.setup_device("010106", EOJ(0x013001))
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "010106-013001-a1"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "auto"
    assert "non_auto" in state.attributes["options"]

    mock_echonet_lite_client.send.reset_mock()

    # Select another option and ensure correct EDT is sent
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": "auto_horizontal"},
        blocking=True,
    )

    assert mock_echonet_lite_client.send.call_count == 1
    frame_arg = mock_echonet_lite_client.send.call_args_list[0].args[1]
    assert frame_arg.properties[0].epc == 0xA1
    assert frame_arg.properties[0].edt == b"\x44"  # auto_horizontal
    assert frame_arg.deoj == EOJ(0x013001)
    assert frame_arg.esv == 0x61

    # Simulate device update to non_auto via frame event
    update_frame = TestFrame(
        tid=2,
        seoj=bytes.fromhex("013001"),
        deoj=bytes.fromhex("05ff01"),
        esv=ESV.GET_RES,
        properties=[TestProperty(epc=0xA1, edt=b"\x42")],
    )
    await coordinator.device_manager.process_frame_event(
        make_frame_event(
            update_frame,
            received_at=time.monotonic(),
            node_id="010106",
            eoj=EOJ(int("013001", 16)),
        )
    )
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "non_auto"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_select_not_writable_raises_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that selecting an option on a non-writable EPC raises an error."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    # EPC 0xA1 is in GET property map but NOT in SET property map
    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=0x9E, edt=b"\x00"),  # SET property map: empty
        TestProperty(epc=0x9F, edt=make_property_map_edt(0xA1)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xA1, edt=b"\x41"),  # auto
    ]

    await coordinator.device_manager.setup_device("010109", EOJ(0x013001))
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "010109-013001-a1"
    )
    assert entity_id is not None

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": entity_id, "option": "auto_horizontal"},
            blocking=True,
        )
    assert exc_info.value.translation_key == "epc_not_writable"

    mock_echonet_lite_client.send.assert_not_called()


# ===========================================================================
# Installation Location (EPC 0x81) tests
# ===========================================================================


def _make_get_response(epc_81_edt: bytes) -> list[TestProperty]:
    """Build a minimal GET response that includes EPC 0x81."""
    return [
        TestProperty(
            epc=0x9E,
            edt=make_property_map_edt(EPC_INSTALLATION_LOCATION),
        ),
        TestProperty(
            epc=0x9F,
            edt=make_property_map_edt(EPC_INSTALLATION_LOCATION),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_INSTALLATION_LOCATION, edt=epc_81_edt),
    ]


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_installation_location_initial_state(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Code entity shows correct option; number entity is unavailable when unset."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    # 0x00 = unset (LLLL=0, NNN=0)
    mock_echonet_lite_client.get.return_value = _make_get_response(b"\x00")

    await coordinator.device_manager.setup_device("010101", EOJ(0x013001))
    await hass.async_block_till_done()

    code_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "010101-013001_81_code"
    )
    number_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "010101-013001_81_number"
    )
    assert code_id is not None
    assert number_id is not None

    code_state = hass.states.get(code_id)
    assert code_state is not None
    assert code_state.state == "unset"

    number_state = hass.states.get(number_id)
    assert number_state is not None
    assert number_state.state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_installation_location_code_with_number(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Code entity shows living_room; number entity shows 3."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    # LLLL=1 (living_room), NNN=3 → (0x1 << 3) | 0x3 = 0x0B
    mock_echonet_lite_client.get.return_value = _make_get_response(b"\x0b")

    await coordinator.device_manager.setup_device("010102", EOJ(0x013001))
    await hass.async_block_till_done()

    code_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "010102-013001_81_code"
    )
    number_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "010102-013001_81_number"
    )
    assert code_id is not None
    assert number_id is not None

    assert hass.states.get(code_id).state == "living_room"
    assert hass.states.get(number_id).state == "3"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_installation_location_code_select_write(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Selecting a code preserves the current NNN and sends the correct byte."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    # LLLL=1 (living_room), NNN=2 → 0x0A
    mock_echonet_lite_client.get.return_value = _make_get_response(b"\x0a")

    await coordinator.device_manager.setup_device("010103", EOJ(0x013001))
    await hass.async_block_till_done()

    code_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "010103-013001_81_code"
    )
    assert code_id is not None

    mock_echonet_lite_client.send.reset_mock()

    # Change to kitchen (LLLL=3); NNN=2 should be preserved → (3<<3)|2 = 0x1A
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": code_id, "option": "kitchen"},
        blocking=True,
    )

    assert mock_echonet_lite_client.send.call_count == 1
    frame_arg = mock_echonet_lite_client.send.call_args_list[0].args[1]
    assert frame_arg.properties[0].epc == EPC_INSTALLATION_LOCATION
    assert frame_arg.properties[0].edt == bytes([0x1A])


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_installation_location_code_select_unset(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Selecting 'unset' always writes 0x00 regardless of current NNN."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    # LLLL=2 (dining_room), NNN=5 → (2<<3)|5 = 0x15
    mock_echonet_lite_client.get.return_value = _make_get_response(b"\x15")

    await coordinator.device_manager.setup_device("010104", EOJ(0x013001))
    await hass.async_block_till_done()

    code_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "010104-013001_81_code"
    )
    assert code_id is not None

    mock_echonet_lite_client.send.reset_mock()

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": code_id, "option": "unset"},
        blocking=True,
    )

    assert mock_echonet_lite_client.send.call_count == 1
    frame_arg = mock_echonet_lite_client.send.call_args_list[0].args[1]
    assert frame_arg.properties[0].epc == EPC_INSTALLATION_LOCATION
    assert frame_arg.properties[0].edt == b"\x00"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_installation_location_number_select_write(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Selecting a number preserves the current LLLL and sends the correct byte."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    # LLLL=3 (kitchen), NNN=0 → 0x18
    mock_echonet_lite_client.get.return_value = _make_get_response(b"\x18")

    await coordinator.device_manager.setup_device("010105", EOJ(0x013001))
    await hass.async_block_till_done()

    number_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "010105-013001_81_number"
    )
    assert number_id is not None

    mock_echonet_lite_client.send.reset_mock()

    # Change to NNN=5; LLLL=3 should be preserved → (3<<3)|5 = 0x1D
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": number_id, "option": "5"},
        blocking=True,
    )

    assert mock_echonet_lite_client.send.call_count == 1
    frame_arg = mock_echonet_lite_client.send.call_args_list[0].args[1]
    assert frame_arg.properties[0].epc == EPC_INSTALLATION_LOCATION
    assert frame_arg.properties[0].edt == bytes([0x1D])


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_installation_location_writes_are_globally_serialized(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Serialize installation location read-modify-write operations globally."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator
    mock_echonet_lite_client.get.return_value = _make_get_response(b"\x0a")

    await coordinator.device_manager.setup_device("010108", EOJ(0x013001))
    mock_echonet_lite_client.get.return_value = _make_get_response(b"\x18")
    await coordinator.device_manager.setup_device("010109", EOJ(0x013001))
    await hass.async_block_till_done()

    code_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "010108-013001_81_code"
    )
    number_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "010109-013001_81_number"
    )
    assert code_id is not None
    assert number_id is not None

    active = 0
    max_active = 0

    def _send(_node_id: str, _frame: object) -> bool:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        active -= 1
        return True

    mock_echonet_lite_client.send.reset_mock()
    mock_echonet_lite_client.send.side_effect = _send

    first_call = asyncio.create_task(
        hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": code_id, "option": "kitchen"},
            blocking=True,
        )
    )
    second_call = asyncio.create_task(
        hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": number_id, "option": "5"},
            blocking=True,
        )
    )

    await asyncio.gather(first_call, second_call)
    assert mock_echonet_lite_client.send.call_count == 2
    assert max_active == 1


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_installation_location_number_unavailable_when_code_unset(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Number entity becomes unavailable when device reports LLLL=0 (unset)."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    # LLLL=2 (dining_room), NNN=0 → 0x10
    mock_echonet_lite_client.get.return_value = _make_get_response(b"\x10")

    await coordinator.device_manager.setup_device("010106b", EOJ(0x013001))
    await hass.async_block_till_done()

    number_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "010106b-013001_81_number"
    )
    assert number_id is not None
    assert hass.states.get(number_id).state != STATE_UNAVAILABLE

    # Simulate device updating 0x81 to 0x00 (unset) via frame event
    update_frame = TestFrame(
        tid=2,
        seoj=bytes.fromhex("013001"),
        deoj=bytes.fromhex("05ff01"),
        esv=ESV.GET_RES,
        properties=[TestProperty(epc=EPC_INSTALLATION_LOCATION, edt=b"\x00")],
    )
    await coordinator.device_manager.process_frame_event(
        make_frame_event(
            update_frame,
            received_at=1.0,
            node_id="010106b",
            eoj=EOJ(int("013001", 16)),
        )
    )
    await hass.async_block_till_done()

    # Number entity must become unavailable once LLLL=0
    assert hass.states.get(number_id).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_installation_location_not_in_get_epcs(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """No installation location entities created when EPC 0x81 is not in GET map."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    # EPC 0x81 absent from the GET property map
    mock_echonet_lite_client.get.return_value = [
        TestProperty(epc=0x9E, edt=b"\x00"),
        TestProperty(epc=0x9F, edt=make_property_map_edt(0xA1)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
    ]

    await coordinator.device_manager.setup_device("010107", EOJ(0x013001))
    await hass.async_block_till_done()

    assert (
        entity_registry.async_get_entity_id("select", DOMAIN, "010107-013001_81_code")
        is None
    )
    assert (
        entity_registry.async_get_entity_id("select", DOMAIN, "010107-013001_81_number")
        is None
    )


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_installation_location_prohibited_value(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Code is unknown and number is unavailable for an undecodable 0x81 byte."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    # 0x01 is a prohibited (17-byte format indicator) value → decodes to None
    mock_echonet_lite_client.get.return_value = _make_get_response(b"\x01")

    await coordinator.device_manager.setup_device("01010b", EOJ(0x013001))
    await hass.async_block_till_done()

    code_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "01010b-013001_81_code"
    )
    number_id = entity_registry.async_get_entity_id(
        "select", DOMAIN, "01010b-013001_81_number"
    )
    assert code_id is not None
    assert number_id is not None

    assert hass.states.get(code_id).state == STATE_UNKNOWN
    assert hass.states.get(number_id).state == STATE_UNAVAILABLE
