"""Tests for the ECHONET Lite fan platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from pyhems import EOJ, BinaryCodec, get_codec_for_epc
import pytest

from homeassistant.components.echonet_lite.const import (
    DOMAIN,
    EPC_AIR_FLOW_LEVEL,
    EPC_OPERATION_STATUS,
)
from homeassistant.components.echonet_lite.fan import (
    _DESCRIPTIONS,
    PRESET_MODE_AUTO,
    PRESET_MODE_MANUAL,
    EchonetLiteFanEntityDescription,
)
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .conftest import (
    EPC_GET_PROPERTY_MAP,
    EPC_INF_PROPERTY_MAP,
    EPC_MANUFACTURER_CODE,
    EPC_SET_PROPERTY_MAP,
    TEST_MANUFACTURER_CODE,
    TestProperty,
    make_property_map_edt,
)

from tests.common import MockConfigEntry


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable the fan platform for these tests."""
    return [Platform.FAN]


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_fan_entity_state(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure fan entities expose on/off state and speed."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    seoj = bytes.fromhex("013501")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    # Air cleaner with operation status and air flow level
    advertised_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30"),  # ON
        TestProperty(epc=EPC_AIR_FLOW_LEVEL, edt=b"\x34"),  # Level 4
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id("fan", DOMAIN, "010105-013501-fan")
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"
    assert state.attributes.get("percentage") == 50  # Level 4 of 8 = 50%
    assert state.attributes.get("preset_mode") == PRESET_MODE_MANUAL


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_fan_entity_auto_mode(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure fan entity reports auto preset mode correctly."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    seoj = bytes.fromhex("013501")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30"),  # ON
        TestProperty(epc=EPC_AIR_FLOW_LEVEL, edt=b"\x41"),  # Auto
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id("fan", DOMAIN, "010105-013501-fan")
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"
    assert state.attributes.get("percentage") is None  # None when in auto mode
    assert state.attributes.get("preset_mode") == PRESET_MODE_AUTO


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_on_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test turn_on and turn_off service calls."""
    entry = init_integration
    entity_id = await _setup_fan_device(hass, entry, entity_registry)

    # Test turn_off
    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "turn_off",
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == b"\x31"  # OFF

    # Test turn_on
    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "turn_on",
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == b"\x30"  # ON


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_percentage(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test setting fan speed percentage."""
    entry = init_integration
    entity_id = await _setup_fan_device(hass, entry, entity_registry)

    # Test setting to 100% (level 8 = 0x38)
    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "set_percentage",
            {ATTR_ENTITY_ID: entity_id, "percentage": 100},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    # Fan is already on: only the air flow level is sent.
    assert len(call.args[1].properties) == 1
    assert call.args[1].properties[0].epc == EPC_AIR_FLOW_LEVEL
    assert call.args[1].properties[0].edt == b"\x38"  # Level 8

    # Test setting to 25% (level 2 = 0x32)
    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "set_percentage",
            {ATTR_ENTITY_ID: entity_id, "percentage": 25},
            blocking=True,
        )

    call = mock_echonet_lite_client.send.call_args_list[0]
    # Fan is already on: only the air flow level is sent.
    assert len(call.args[1].properties) == 1
    assert call.args[1].properties[0].epc == EPC_AIR_FLOW_LEVEL
    assert call.args[1].properties[0].edt == b"\x32"  # Level 2

    # Test setting to 0% (should turn off)
    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "set_percentage",
            {ATTR_ENTITY_ID: entity_id, "percentage": 0},
            blocking=True,
        )

    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == b"\x31"  # OFF


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_preset_mode(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test setting fan preset mode to auto."""
    entry = init_integration
    entity_id = await _setup_fan_device(hass, entry, entity_registry)

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "set_preset_mode",
            {ATTR_ENTITY_ID: entity_id, "preset_mode": PRESET_MODE_AUTO},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    # Fan is already on: only the auto level is sent.
    assert len(call.args[1].properties) == 1
    assert call.args[1].properties[0].epc == EPC_AIR_FLOW_LEVEL
    assert call.args[1].properties[0].edt == b"\x41"  # Auto


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_preset_mode_manual(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test setting preset_mode=manual on an already-on fan is a no-op."""
    entry = init_integration
    entity_id = await _setup_fan_device(hass, entry, entity_registry)

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "set_preset_mode",
            {ATTR_ENTITY_ID: entity_id, "preset_mode": PRESET_MODE_MANUAL},
            blocking=True,
        )

    # ``manual`` is not a distinct ECHONET level; any non-auto level
    # already represents manual, so nothing is sent when already on.
    assert mock_echonet_lite_client.send.call_count == 0


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_on_with_percentage(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test turn_on with percentage parameter."""
    entry = init_integration
    entity_id = await _setup_fan_device(hass, entry, entity_registry)

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "turn_on",
            {ATTR_ENTITY_ID: entity_id, "percentage": 75},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    # Should send both ON and air flow level
    assert len(call.args[1].properties) == 2
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == b"\x30"  # ON
    assert call.args[1].properties[1].epc == EPC_AIR_FLOW_LEVEL
    assert call.args[1].properties[1].edt == b"\x36"  # Level 6 (75% ≈ level 6)


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_on_with_preset_mode(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test turn_on with preset_mode parameter."""
    entry = init_integration
    entity_id = await _setup_fan_device(hass, entry, entity_registry)

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "turn_on",
            {ATTR_ENTITY_ID: entity_id, "preset_mode": PRESET_MODE_AUTO},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    # Should send both ON and auto mode
    assert len(call.args[1].properties) == 2
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == b"\x30"  # ON
    assert call.args[1].properties[1].epc == EPC_AIR_FLOW_LEVEL
    assert call.args[1].properties[1].edt == b"\x41"  # Auto


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_percentage_when_off_turns_on(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that setting percentage when off also turns on the fan."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    seoj = bytes.fromhex("013501")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}
    set_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(set_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x31"),  # OFF
        TestProperty(epc=EPC_AIR_FLOW_LEVEL, edt=b"\x34"),  # Level 4
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id("fan", DOMAIN, "010105-013501-fan")
    assert entity_id is not None

    # Verify fan is off
    state = hass.states.get(entity_id)
    assert state.state == "off"

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "set_percentage",
            {ATTR_ENTITY_ID: entity_id, "percentage": 50},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    # Should send both ON and air flow level
    assert len(call.args[1].properties) == 2
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == b"\x30"  # ON
    assert call.args[1].properties[1].epc == EPC_AIR_FLOW_LEVEL
    assert call.args[1].properties[1].edt == b"\x34"  # Level 4


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_preset_mode_when_off_turns_on(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that setting preset mode when off also turns on the fan."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    seoj = bytes.fromhex("013501")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}
    set_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(set_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x31"),  # OFF
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id("fan", DOMAIN, "010105-013501-fan")
    assert entity_id is not None

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "set_preset_mode",
            {ATTR_ENTITY_ID: entity_id, "preset_mode": PRESET_MODE_AUTO},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    # Should send both ON and auto mode
    assert len(call.args[1].properties) == 2
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == b"\x30"  # ON
    assert call.args[1].properties[1].epc == EPC_AIR_FLOW_LEVEL
    assert call.args[1].properties[1].edt == b"\x41"  # Auto


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_operation_status_not_writable_raises_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test error is raised when operation status is not writable."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    seoj = bytes.fromhex("013501")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    # Device with read-only operation status
    advertised_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}
    set_epcs = {EPC_AIR_FLOW_LEVEL}  # No operation status

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(set_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30"),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id("fan", DOMAIN, "010105-013501-fan")
    assert entity_id is not None

    # Fan entity without TURN_ON feature should not support turn_on service
    # HA raises ServiceNotSupported (subclass of ServiceValidationError)

    with pytest.raises(
        ServiceValidationError, match="does not support action fan.turn_on"
    ):
        await hass.services.async_call(
            "fan",
            "turn_on",
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_air_flow_level_not_writable_raises_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test error is raised when air flow level is not writable."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    seoj = bytes.fromhex("013501")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    # Device with read-only air flow level
    advertised_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}
    set_epcs = {EPC_OPERATION_STATUS}  # No air flow level

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(set_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30"),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id("fan", DOMAIN, "010105-013501-fan")
    assert entity_id is not None

    # Fan entity without SET_SPEED feature should not support set_percentage service

    with pytest.raises(
        ServiceValidationError, match="does not support action fan.set_percentage"
    ):
        await hass.services.async_call(
            "fan",
            "set_percentage",
            {ATTR_ENTITY_ID: entity_id, "percentage": 50},
            blocking=True,
        )


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_fan_without_air_flow_control(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test fan entity with only on/off control (no air flow level)."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    seoj = bytes.fromhex("013501")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    # Device with only operation status
    advertised_epcs = {EPC_OPERATION_STATUS}
    set_epcs = {EPC_OPERATION_STATUS}

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(set_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30"),  # ON
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id("fan", DOMAIN, "010105-013501-fan")
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"
    # No percentage or preset_mode attributes when air flow control is unavailable
    assert "percentage" not in state.attributes
    assert "preset_mode" not in state.attributes


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_on_preserves_auto_preset_across_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Regression: bare ``turn_on`` must not clobber a cached auto preset.

    Flow: on + auto -> off -> bare turn_on. The new design sends only
    ``OP_STATUS=ON`` on bare turn_on, leaving ``AIR_FLOW_LEVEL``
    untouched so the device retains ``auto`` (0x41) from before.
    """
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    seoj = bytes.fromhex("013501")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x31"),  # OFF
        TestProperty(epc=EPC_AIR_FLOW_LEVEL, edt=b"\x41"),  # Auto cached
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id("fan", DOMAIN, "010105-013501-fan")
    assert entity_id is not None

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "turn_on",
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    # Only OP_STATUS is sent; AIR_FLOW_LEVEL is not retransmitted, so the
    # device's cached auto preset survives the OFF->ON transition.
    assert len(call.args[1].properties) == 1
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == b"\x30"  # ON


async def _setup_fan_device(
    hass: HomeAssistant,
    entry,
    entity_registry: er.EntityRegistry,
    *,
    seoj: bytes = bytes.fromhex("013501"),
) -> str:
    """Set up a fan device and return the entity_id."""
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    eoj = EOJ(int.from_bytes(seoj, "big"))

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}
    set_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(set_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30"),  # ON
        TestProperty(epc=EPC_AIR_FLOW_LEVEL, edt=b"\x34"),  # Level 4
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    unique_id = f"010105-{seoj.hex()}-fan"
    entity_id = entity_registry.async_get_entity_id("fan", DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


@pytest.mark.parametrize(
    ("seoj_hex", "unique_id_suffix"),
    [
        ("013301", "010105-013301-fan"),  # Ventilation fan
        ("013401", "010105-013401-fan"),  # Air conditioner ventilation fan
    ],
)
@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_ventilation_fan_entity_state(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    seoj_hex: str,
    unique_id_suffix: str,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure ventilation fan entities expose on/off state and speed."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    seoj = bytes.fromhex(seoj_hex)
    eoj = EOJ(int.from_bytes(seoj, "big"))

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30"),  # ON
        TestProperty(epc=EPC_AIR_FLOW_LEVEL, edt=b"\x34"),  # Level 4
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id("fan", DOMAIN, unique_id_suffix)
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"
    assert state.attributes.get("percentage") == 50  # Level 4 of 8 = 50%
    assert state.attributes.get("preset_mode") == PRESET_MODE_MANUAL


@pytest.mark.parametrize(
    ("seoj_hex", "unique_id_suffix"),
    [
        ("013301", "010105-013301-fan"),  # Ventilation fan
        ("013401", "010105-013401-fan"),  # Air conditioner ventilation fan
    ],
)
@pytest.mark.usefixtures("mock_definitions_registry")
async def test_ventilation_fan_turn_on_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    seoj_hex: str,
    unique_id_suffix: str,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test turn_on and turn_off for ventilation fan devices."""
    entry = init_integration
    entity_id = await _setup_fan_device(
        hass, entry, entity_registry, seoj=bytes.fromhex(seoj_hex)
    )

    # Test turn_off
    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "turn_off",
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == b"\x31"  # OFF

    # Test turn_on
    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "turn_on",
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == b"\x30"  # ON


@pytest.mark.parametrize(
    ("seoj_hex", "unique_id_suffix"),
    [
        ("013301", "010105-013301-fan"),  # Ventilation fan
        ("013401", "010105-013401-fan"),  # Air conditioner ventilation fan
    ],
)
@pytest.mark.usefixtures("mock_definitions_registry")
async def test_ventilation_fan_set_percentage(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    seoj_hex: str,
    unique_id_suffix: str,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test setting fan speed percentage for ventilation fan devices."""
    entry = init_integration
    entity_id = await _setup_fan_device(
        hass, entry, entity_registry, seoj=bytes.fromhex(seoj_hex)
    )

    # Test setting to 100% (level 8 = 0x38)
    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "set_percentage",
            {ATTR_ENTITY_ID: entity_id, "percentage": 100},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert len(call.args[1].properties) == 1
    assert call.args[1].properties[0].epc == EPC_AIR_FLOW_LEVEL
    assert call.args[1].properties[0].edt == b"\x38"  # Level 8


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_ventilation_fan_only_on_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test ventilation fan with only on/off control (no air flow level)."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    seoj = bytes.fromhex("013301")  # Ventilation fan
    eoj = EOJ(int.from_bytes(seoj, "big"))

    # Device with only operation status
    advertised_epcs = {EPC_OPERATION_STATUS}
    set_epcs = {EPC_OPERATION_STATUS}

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(set_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30"),  # ON
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id("fan", DOMAIN, "010105-013301-fan")
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"
    # No percentage or preset_mode attributes when air flow control is unavailable
    assert "percentage" not in state.attributes
    assert "preset_mode" not in state.attributes


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_preset_mode_manual_when_off_sends_op_status(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Manual preset on an OFF fan turns it on without changing level."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    seoj = bytes.fromhex("013501")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}
    set_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(set_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x31"),  # OFF
        TestProperty(epc=EPC_AIR_FLOW_LEVEL, edt=b"\x33"),  # Level 3
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id("fan", DOMAIN, "010105-013501-fan")
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "off"

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "fan",
            "set_preset_mode",
            {ATTR_ENTITY_ID: entity_id, "preset_mode": PRESET_MODE_MANUAL},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    # Manual on an OFF fan: send only OP_STATUS=ON, level unchanged.
    assert len(call.args[1].properties) == 1
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == b"\x30"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_percentage_skipped_when_air_flow_level_not_writable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Direct-call defensive: set_percentage returns silently if level is read-only.

    The HA service framework normally blocks ``fan.set_percentage`` when
    SET_SPEED is unsupported (see
    ``test_air_flow_level_not_writable_raises_error``), but the entity
    method also short-circuits to keep the call site safe.
    """
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    seoj = bytes.fromhex("013501")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}
    set_epcs = {EPC_OPERATION_STATUS}  # AIR_FLOW_LEVEL is read-only

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(set_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x31"),  # OFF
        TestProperty(epc=EPC_AIR_FLOW_LEVEL, edt=b"\x32"),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id("fan", DOMAIN, "010105-013501-fan")
    assert entity_id is not None

    component = hass.data[FAN_DOMAIN]
    fan_entity = component.get_entity(entity_id)
    assert fan_entity is not None

    mock_echonet_lite_client.send.reset_mock()
    await fan_entity.async_set_percentage(50)

    mock_echonet_lite_client.send.assert_not_called()


async def _setup_fan_custom(
    hass: HomeAssistant,
    entry,
    entity_registry: er.EntityRegistry,
    *,
    set_epcs: set[int],
    properties: list[TestProperty],
    node_hex: str = "010105",
    seoj: bytes = bytes.fromhex("013501"),
):
    """Set up a fan with custom SET map/properties and return its entity object."""
    coordinator = entry.runtime_data.coordinator
    node_id = bytes.fromhex(node_hex).hex()
    eoj = EOJ(int.from_bytes(seoj, "big"))

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL}

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(set_epcs)),
        ),
        TestProperty(
            epc=EPC_INF_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        *properties,
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "fan", DOMAIN, f"{node_id}-{seoj.hex()}-fan"
    )
    assert entity_id is not None
    fan_entity = hass.data[FAN_DOMAIN].get_entity(entity_id)
    assert fan_entity is not None
    return entity_id, fan_entity


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_no_fan_entity_for_other_class(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """No fan entity is created for non-fan device classes."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("01010c").hex()
    seoj = bytes.fromhex("001101")  # Temperature sensor, not a fan
    eoj = EOJ(int.from_bytes(seoj, "big"))

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xE0)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xE0, edt=b"\x00d"),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    assert (
        entity_registry.async_get_entity_id("fan", DOMAIN, f"{node_id}-001101-fan")
        is None
    )


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_is_on_unknown_without_operation_status(
    hass: HomeAssistant, init_integration, entity_registry: er.EntityRegistry
) -> None:
    """Fan state is unknown when the operation status has no value."""
    entry = init_integration

    entity_id, _ = await _setup_fan_custom(
        hass,
        entry,
        entity_registry,
        set_epcs={EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL},
        properties=[TestProperty(epc=EPC_AIR_FLOW_LEVEL, edt=b"\x34")],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_UNKNOWN


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_on_percentage_zero_turns_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Calling turn_on with percentage 0 sends OFF (HA convention)."""
    entry = init_integration

    _, fan_entity = await _setup_fan_custom(
        hass,
        entry,
        entity_registry,
        set_epcs={EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL},
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x31"),
            TestProperty(epc=EPC_AIR_FLOW_LEVEL, edt=b"\x34"),
        ],
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await fan_entity.async_turn_on(percentage=0)

    assert mock_echonet_lite_client.send.call_count == 1
    props = mock_echonet_lite_client.send.call_args_list[0].args[1].properties
    assert len(props) == 1
    assert props[0].epc == EPC_OPERATION_STATUS
    assert props[0].edt == b"\x31"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_on_op_status_not_writable_direct(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Direct call to turn_on raises when operation status is read-only."""
    entry = init_integration

    _, fan_entity = await _setup_fan_custom(
        hass,
        entry,
        entity_registry,
        set_epcs={EPC_AIR_FLOW_LEVEL},  # operation status not writable
        properties=[TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30")],
    )

    with pytest.raises(HomeAssistantError) as exc_info:
        await fan_entity.async_turn_on()
    assert exc_info.value.translation_key == "epc_not_writable"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_preset_auto_skipped_when_level_not_writable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Direct call: auto preset returns silently when level is read-only."""
    entry = init_integration

    _, fan_entity = await _setup_fan_custom(
        hass,
        entry,
        entity_registry,
        set_epcs={EPC_OPERATION_STATUS},  # air flow level not writable
        properties=[TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30")],
    )

    mock_echonet_lite_client.send.reset_mock()
    await fan_entity.async_set_preset_mode(PRESET_MODE_AUTO)

    mock_echonet_lite_client.send.assert_not_called()


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_preset_manual_op_status_not_writable_direct(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Direct call: manual preset on an off fan raises when status read-only."""
    entry = init_integration

    _, fan_entity = await _setup_fan_custom(
        hass,
        entry,
        entity_registry,
        set_epcs={EPC_AIR_FLOW_LEVEL},  # operation status not writable
        properties=[TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x31")],  # OFF
    )

    with pytest.raises(HomeAssistantError) as exc_info:
        await fan_entity.async_set_preset_mode(PRESET_MODE_MANUAL)
    assert exc_info.value.translation_key == "epc_not_writable"


@pytest.mark.usefixtures("mock_echonet_lite_client")
async def test_fan_op_status_codec_is_binary(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """EPC 0x80 must return BinaryCodec for the fan class codes.

    This is an HA-side contract test: if pyhems changes its definitions in a
    way that breaks the fan entity's on/off behaviour, this test catches it.
    """
    for class_code in _DESCRIPTIONS:
        codec = get_codec_for_epc(class_code, EPC_OPERATION_STATUS)
        assert isinstance(codec, BinaryCodec), (
            f"Expected BinaryCodec for EPC 0x80 on class 0x{class_code:04X}"
        )


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_percentage_and_preset_mode_unknown_air_flow_key(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Percentage and preset_mode return None for an unrecognised air-flow key.

    The EnumCodec for EPC_AIR_FLOW_LEVEL only defines level_1..level_8 and
    auto; future pyhems updates could add vendor-specific keys. The entity
    must return None gracefully rather than raising an error.  We exercise
    the branch by substituting a mock prop on the entity description.
    """

    entry = init_integration
    _, fan_entity = await _setup_fan_custom(
        hass,
        entry,
        entity_registry,
        set_epcs={EPC_OPERATION_STATUS, EPC_AIR_FLOW_LEVEL},
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30"),  # ON
            TestProperty(epc=EPC_AIR_FLOW_LEVEL, edt=b"\x34"),  # level_4
        ],
    )

    # Substitute a mock prop that returns a vendor-specific key unknown to
    # _SPEED_LEVELS to exercise the defensive ``return None`` branches.
    mock_air_flow = MagicMock()
    mock_air_flow.get.return_value = "vendor_turbo"

    original = fan_entity.entity_description
    fan_entity.entity_description = EchonetLiteFanEntityDescription(
        key=original.key,
        translation_key=original.translation_key,
        op_status=original.op_status,
        air_flow_prop=mock_air_flow,  # type: ignore[arg-type]
    )

    assert fan_entity.percentage is None
    assert fan_entity.preset_mode is None
