"""Tests for the ECHONET Lite climate platform."""

from unittest.mock import AsyncMock, patch

from pyhems import EOJ, BinaryCodec, DeviceClass, NumericCodec, get_codec_for_epc
import pytest

from homeassistant.components.climate import (
    ATTR_CURRENT_HUMIDITY,
    ATTR_HVAC_ACTION,
    FAN_AUTO,
    SWING_BOTH,
    SWING_OFF,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.echonet_lite.climate import _DESCRIPTIONS
from homeassistant.components.echonet_lite.const import (
    DOMAIN,
    EPC_FAN_SPEED,
    EPC_OPERATION_MODE,
    EPC_OPERATION_STATUS,
    EPC_ROOM_HUMIDITY,
    EPC_ROOM_TEMPERATURE,
    EPC_SPECIAL_STATE,
    EPC_SWING_AIR_FLOW,
    EPC_TARGET_TEMPERATURE,
)
from homeassistant.const import ATTR_TEMPERATURE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .conftest import (
    EPC_GET_PROPERTY_MAP,
    EPC_MANUFACTURER_CODE,
    EPC_SET_PROPERTY_MAP,
    TEST_MANUFACTURER_CODE,
    TestProperty,
    make_property_map_edt,
)

from tests.common import MockConfigEntry

# EPCs that were previously in _CLIMATE_REQUIRED_EPCS_FOR_TESTING
_CLIMATE_REQUIRED_EPCS_FOR_TESTING: dict[int, frozenset[int]] = {
    DeviceClass.HOME_AIR_CONDITIONER: frozenset(
        {
            EPC_OPERATION_STATUS,
            EPC_OPERATION_MODE,
            EPC_TARGET_TEMPERATURE,
            EPC_ROOM_HUMIDITY,
            EPC_ROOM_TEMPERATURE,
        }
    ),
}


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable the climate platform for these tests."""

    return [Platform.CLIMATE]


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_climate_entity_state(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure climate entities expose temperatures and modes."""

    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010104").hex()
    seoj = bytes.fromhex("013001")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    required_epcs = _CLIMATE_REQUIRED_EPCS_FOR_TESTING.get(
        DeviceClass.HOME_AIR_CONDITIONER, frozenset()
    )
    advertised_epcs = required_epcs | {0xB5, 0xB6}
    set_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE, EPC_TARGET_TEMPERATURE}

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
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0x80, edt=b"\x30"),
        TestProperty(epc=0xB0, edt=b"\x42"),
        TestProperty(epc=0xB3, edt=b"\x18"),
        TestProperty(epc=0xBB, edt=b"\x1e"),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "climate", DOMAIN, "010104-013001-climate"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == HVACMode.COOL
    assert state.attributes.get("current_temperature") == 30
    assert state.attributes.get("temperature") == 24

    device_key = f"{node_id}-{int(eoj):06x}"
    effective_epcs = coordinator.device_manager.effective_poll_epcs(device_key)
    assert {EPC_ROOM_HUMIDITY, EPC_ROOM_TEMPERATURE} <= effective_epcs


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_hvac_mode_sends_commands(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure hvac mode changes emit the expected frames."""

    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010104").hex()
    seoj = bytes.fromhex("013001")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    required_epcs = _CLIMATE_REQUIRED_EPCS_FOR_TESTING.get(
        DeviceClass.HOME_AIR_CONDITIONER, frozenset()
    )
    # Include HEAT-specific target so heat commands prefer 0xB6
    advertised_epcs = required_epcs | {0xB6}
    set_epcs = {0x80, 0xB0}

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
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0x80, edt=b"\x30"),
        TestProperty(epc=0xB0, edt=b"\x42"),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "climate", DOMAIN, "010104-013001-climate"
    )
    assert entity_id is not None

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ) as mock_schedule:
        await hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": entity_id, "hvac_mode": HVACMode.HEAT},
            blocking=True,
        )
        mock_schedule.assert_called_once_with("010104-013001")
    # Now sends mode and ON together in one call
    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    # Check that frame has 2 properties: mode and ON
    assert len(call.args[1].properties) == 2
    # Mode should be 0x43 (HEAT)
    assert call.args[1].properties[0].epc == 0xB0
    assert call.args[1].properties[0].edt == b"\x43"
    # ON should be 0x30
    assert call.args[1].properties[1].epc == 0x80
    assert call.args[1].properties[1].edt == b"\x30"

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ) as mock_schedule:
        await hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": entity_id, "hvac_mode": HVACMode.OFF},
            blocking=True,
        )
        mock_schedule.assert_called_once_with("010104-013001")
    # OFF sends only operation status
    assert mock_echonet_lite_client.send.call_count == 1
    off_call = mock_echonet_lite_client.send.call_args_list[0]
    assert off_call.args[1].properties[0].epc == 0x80
    assert off_call.args[1].properties[0].edt == b"\x31"


async def _setup_climate_device(
    hass: HomeAssistant,
    entry,
    entity_registry: er.EntityRegistry,
    *,
    set_epcs: set[int] | None = None,
    fan_mode: bool = False,
    swing_mode: bool = False,
) -> str:
    """Set up a climate device and return the entity_id.

    Args:
        hass: Home Assistant instance.
        entry: Config entry.
        set_epcs: Optional set of writable EPCs. Defaults to {0x80, 0xB0, 0xB3}.
        fan_mode: If True, add EPC_FAN_SPEED to both get and set EPCs.
        swing_mode: If True, add EPC_SWING_AIR_FLOW to both get and set EPCs.

    Returns:
        The entity_id of the created climate entity.
    """
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010104").hex()
    seoj = bytes.fromhex("013001")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    required_epcs = _CLIMATE_REQUIRED_EPCS_FOR_TESTING.get(
        DeviceClass.HOME_AIR_CONDITIONER, frozenset()
    )
    advertised_epcs = set(required_epcs)

    if set_epcs is None:
        set_epcs = {
            EPC_OPERATION_STATUS,
            EPC_OPERATION_MODE,
            EPC_TARGET_TEMPERATURE,
        }

    if fan_mode:
        advertised_epcs.add(EPC_FAN_SPEED)
        set_epcs.add(EPC_FAN_SPEED)
    if swing_mode:
        advertised_epcs.add(EPC_SWING_AIR_FLOW)
        set_epcs.add(EPC_SWING_AIR_FLOW)

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
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30"),
        TestProperty(epc=EPC_OPERATION_MODE, edt=b"\x42"),  # COOL
        TestProperty(epc=EPC_TARGET_TEMPERATURE, edt=b"\x18"),  # 24°C
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "climate", DOMAIN, "010104-013001-climate"
    )
    assert entity_id is not None
    return entity_id


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_temperature_sends_command(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test setting target temperature sends the correct frame."""
    entry = init_integration
    entity_id = await _setup_climate_device(hass, entry, entity_registry)

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ) as mock_schedule:
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity_id, ATTR_TEMPERATURE: 26},
            blocking=True,
        )
        mock_schedule.assert_called_once_with("010104-013001")

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].epc == EPC_TARGET_TEMPERATURE
    assert call.args[1].properties[0].edt == b"\x1a"  # 26°C


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_fan_mode_sends_command(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test setting fan mode sends the correct frame."""
    entry = init_integration
    entity_id = await _setup_climate_device(hass, entry, entity_registry, fan_mode=True)

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ) as mock_schedule:
        await hass.services.async_call(
            "climate",
            "set_fan_mode",
            {"entity_id": entity_id, "fan_mode": FAN_AUTO},
            blocking=True,
        )
        mock_schedule.assert_called_once_with("010104-013001")

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].epc == EPC_FAN_SPEED
    assert call.args[1].properties[0].edt == b"\x41"  # AUTO

    # Test level_8
    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "climate",
            "set_fan_mode",
            {"entity_id": entity_id, "fan_mode": "level_8"},
            blocking=True,
        )

    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].edt == b"\x38"  # Level 8


def test_swing_mode_description_uses_registry_keys() -> None:
    """Swing mode descriptions should use registry-defined pyhems enum keys."""
    description = _DESCRIPTIONS[DeviceClass.HOME_AIR_CONDITIONER]

    assert description.swing_mode_prop.options == [
        "off",
        "vertical",
        "horizontal",
        "vertical_and_horizontal",
    ]


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_swing_mode_sends_command(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test setting swing mode sends the correct frame."""
    entry = init_integration
    entity_id = await _setup_climate_device(
        hass, entry, entity_registry, swing_mode=True
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ) as mock_schedule:
        await hass.services.async_call(
            "climate",
            "set_swing_mode",
            {"entity_id": entity_id, "swing_mode": SWING_BOTH},
            blocking=True,
        )
        mock_schedule.assert_called_once_with("010104-013001")

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].epc == EPC_SWING_AIR_FLOW
    assert call.args[1].properties[0].edt == b"\x43"  # BOTH

    # Test SWING_OFF
    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "climate",
            "set_swing_mode",
            {"entity_id": entity_id, "swing_mode": SWING_OFF},
            blocking=True,
        )

    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].edt == b"\x31"  # OFF


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_target_temperature_feature_requires_writable_epc(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """TARGET_TEMPERATURE feature is only exposed when EPC 0xB3 is writable."""
    entry = init_integration
    # Set up device without EPC_TARGET_TEMPERATURE in set_epcs
    entity_id = await _setup_climate_device(
        hass,
        entry,
        entity_registry,
        set_epcs={EPC_OPERATION_STATUS, EPC_OPERATION_MODE},  # No 0xB3
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert not (
        state.attributes["supported_features"] & ClimateEntityFeature.TARGET_TEMPERATURE
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity_id, ATTR_TEMPERATURE: 26},
            blocking=True,
        )


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_fan_mode_feature_requires_writable_epc(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """FAN_MODE feature is only exposed when EPC 0xA0 is writable."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010104").hex()
    seoj = bytes.fromhex("013001")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    required_epcs = _CLIMATE_REQUIRED_EPCS_FOR_TESTING.get(
        DeviceClass.HOME_AIR_CONDITIONER, frozenset()
    )
    # Add EPC_FAN_SPEED to GET but not SET
    advertised_epcs = set(required_epcs) | {EPC_FAN_SPEED}
    set_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE}

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
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30"),
        TestProperty(epc=EPC_OPERATION_MODE, edt=b"\x42"),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "climate", DOMAIN, "010104-013001-climate"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert not (state.attributes["supported_features"] & ClimateEntityFeature.FAN_MODE)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_fan_mode",
            {"entity_id": entity_id, "fan_mode": FAN_AUTO},
            blocking=True,
        )


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_swing_mode_feature_requires_writable_epc(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """SWING_MODE feature is only exposed when EPC 0xA3 is writable."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010104").hex()
    seoj = bytes.fromhex("013001")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    required_epcs = _CLIMATE_REQUIRED_EPCS_FOR_TESTING.get(
        DeviceClass.HOME_AIR_CONDITIONER, frozenset()
    )
    # Add EPC_SWING_AIR_FLOW to GET but not SET
    advertised_epcs = set(required_epcs) | {EPC_SWING_AIR_FLOW}
    set_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE}

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
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x30"),
        TestProperty(epc=EPC_OPERATION_MODE, edt=b"\x42"),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "climate", DOMAIN, "010104-013001-climate"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert not (
        state.attributes["supported_features"] & ClimateEntityFeature.SWING_MODE
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_swing_mode",
            {"entity_id": entity_id, "swing_mode": SWING_BOTH},
            blocking=True,
        )


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_hvac_mode_operation_mode_not_writable_raises_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test error is raised when operation mode EPC is not writable."""
    entry = init_integration
    # Set up device without EPC_OPERATION_MODE in set_epcs
    entity_id = await _setup_climate_device(
        hass,
        entry,
        entity_registry,
        set_epcs={EPC_OPERATION_STATUS},  # No 0xB0
    )

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": entity_id, "hvac_mode": HVACMode.HEAT},
            blocking=True,
        )
    assert exc_info.value.translation_key == "epc_not_writable"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_hvac_mode_operation_status_not_writable_raises_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test error is raised when operation status EPC is not writable."""
    entry = init_integration
    # Set up device without EPC_OPERATION_STATUS in set_epcs
    entity_id = await _setup_climate_device(
        hass,
        entry,
        entity_registry,
        set_epcs={EPC_OPERATION_MODE},  # No 0x80
    )

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": entity_id, "hvac_mode": HVACMode.HEAT},
            blocking=True,
        )
    assert exc_info.value.translation_key == "epc_not_writable"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_on_off_directly(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test turn_on and turn_off service calls."""
    entry = init_integration
    entity_id = await _setup_climate_device(hass, entry, entity_registry)

    # Test turn_off
    mock_echonet_lite_client.send.reset_mock()
    with patch.object(
        entry.runtime_data.property_poller,
        "schedule_immediate_poll",
    ):
        await hass.services.async_call(
            "climate",
            "turn_off",
            {"entity_id": entity_id},
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
            "climate",
            "turn_on",
            {"entity_id": entity_id},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == b"\x30"  # ON


async def _setup_climate_device_with_state(
    hass: HomeAssistant,
    entry,
    entity_registry: er.EntityRegistry,
    *,
    operation_status: bytes = b"\x30",
    operation_mode: bytes = b"\x42",
    target_temperature: bytes = b"\x18",
    room_temperature: bytes = b"\x1e",
    room_humidity: bytes | None = None,
    special_state: bytes | None = None,
) -> str:
    """Set up a climate device with custom state values and return the entity_id.

    Args:
        hass: Home Assistant instance.
        entry: Config entry.
        operation_status: EDT for EPC 0x80 (default: ON).
        operation_mode: EDT for EPC 0xB0 (default: COOL 0x42).
        target_temperature: EDT for EPC 0xB3 (default: 24°C).
        room_temperature: EDT for EPC 0xBB (default: 30°C).
        room_humidity: EDT for EPC 0xBA if provided.
        special_state: EDT for EPC 0xAA if provided.

    Returns:
        The entity_id of the created climate entity.
    """
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010104").hex()
    seoj = bytes.fromhex("013001")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    required_epcs = _CLIMATE_REQUIRED_EPCS_FOR_TESTING.get(
        DeviceClass.HOME_AIR_CONDITIONER, frozenset()
    )
    advertised_epcs = set(required_epcs)
    if room_humidity is not None:
        advertised_epcs.add(EPC_ROOM_HUMIDITY)
    if special_state is not None:
        advertised_epcs.add(EPC_SPECIAL_STATE)

    properties: list[TestProperty] = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(*sorted(advertised_epcs)),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=operation_status),
        TestProperty(epc=EPC_OPERATION_MODE, edt=operation_mode),
        TestProperty(epc=EPC_TARGET_TEMPERATURE, edt=target_temperature),
        TestProperty(epc=EPC_ROOM_TEMPERATURE, edt=room_temperature),
    ]
    if room_humidity is not None:
        properties.append(TestProperty(epc=EPC_ROOM_HUMIDITY, edt=room_humidity))
    if special_state is not None:
        properties.append(TestProperty(epc=EPC_SPECIAL_STATE, edt=special_state))

    entry.runtime_data.client.get.return_value = properties

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "climate", DOMAIN, "010104-013001-climate"
    )
    assert entity_id is not None
    return entity_id


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_operation_mode_other_maps_to_fan_only_idle(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test operation mode 0x40 ('other') maps to FAN_ONLY + IDLE.

    ECHONET Lite operation status (0x80) and operation mode (0xB0) are
    independent. Value 0xB0 = 0x40 is used by some appliances as a
    persistent vendor-defined mode (internal clean, coil drying, etc.).
    Map to ``HVACMode.FAN_ONLY`` + ``HVACAction.IDLE`` so the entity is
    never chronically unknown and stays distinguishable from 0x45 ("fan
    only") which maps to ``HVACMode.FAN_ONLY`` + ``HVACAction.FAN``.
    """
    entity_id = await _setup_climate_device_with_state(
        hass, init_integration, entity_registry, operation_mode=b"\x40"
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == HVACMode.FAN_ONLY
    assert state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.IDLE


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_operation_status_out_of_spec_returns_none(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test out-of-spec operation status returns unknown state."""
    entity_id = await _setup_climate_device_with_state(
        hass, init_integration, entity_registry, operation_status=b"\xff"
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "unknown"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_operation_status_off_returns_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test operation status OFF returns HVACMode.OFF and HVACAction.OFF."""
    entity_id = await _setup_climate_device_with_state(
        hass, init_integration, entity_registry, operation_status=b"\x31"
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == HVACMode.OFF
    assert state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.OFF


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_special_state_defrosting(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test special state defrosting (0x41) returns DEFROSTING action."""
    entity_id = await _setup_climate_device_with_state(
        hass, init_integration, entity_registry, special_state=b"\x41"
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.DEFROSTING


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_special_state_preheating(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test special state preheating (0x42) returns PREHEATING action."""
    entity_id = await _setup_climate_device_with_state(
        hass, init_integration, entity_registry, special_state=b"\x42"
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.PREHEATING


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_special_state_heat_removal(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test special state heat removal (0x43) returns IDLE action."""
    entity_id = await _setup_climate_device_with_state(
        hass, init_integration, entity_registry, special_state=b"\x43"
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.IDLE


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_special_state_normal_falls_through(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test special state normal (0x40) falls through to operation mode logic."""
    entity_id = await _setup_climate_device_with_state(
        hass, init_integration, entity_registry, special_state=b"\x40"
    )

    state = hass.states.get(entity_id)
    assert state is not None
    # Should use operation mode (COOL) for action
    assert state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.COOLING


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_special_state_overrides_off_status(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test special state takes priority even when operation status is OFF."""
    entity_id = await _setup_climate_device_with_state(
        hass,
        init_integration,
        entity_registry,
        operation_status=b"\x31",
        special_state=b"\x41",
    )

    state = hass.states.get(entity_id)
    assert state is not None
    # hvac_mode follows operation status (OFF)
    assert state.state == HVACMode.OFF
    # hvac_action follows special state (DEFROSTING overrides OFF)
    assert state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.DEFROSTING


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_auto_mode_action_cooling(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test AUTO mode infers COOLING when target <= current temperature."""
    entity_id = await _setup_climate_device_with_state(
        hass,
        init_integration,
        entity_registry,
        operation_mode=b"\x41",  # AUTO
        target_temperature=b"\x18",  # 24°C
        room_temperature=b"\x1e",  # 30°C
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == HVACMode.AUTO
    assert state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.COOLING


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_auto_mode_action_heating(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test AUTO mode infers HEATING when target > current temperature."""
    entity_id = await _setup_climate_device_with_state(
        hass,
        init_integration,
        entity_registry,
        operation_mode=b"\x41",  # AUTO
        target_temperature=b"\x1e",  # 30°C
        room_temperature=b"\x18",  # 24°C
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == HVACMode.AUTO
    assert state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.HEATING


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_auto_mode_action_cooling_when_equal(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test AUTO mode infers COOLING when target == current temperature."""
    entity_id = await _setup_climate_device_with_state(
        hass,
        init_integration,
        entity_registry,
        operation_mode=b"\x41",  # AUTO
        target_temperature=b"\x18",  # 24°C
        room_temperature=b"\x18",  # 24°C
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == HVACMode.AUTO
    assert state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.COOLING


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_auto_mode_action_idle_when_temp_unavailable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test AUTO mode returns IDLE when temperature is unavailable."""
    entity_id = await _setup_climate_device_with_state(
        hass,
        init_integration,
        entity_registry,
        operation_mode=b"\x41",  # AUTO
        target_temperature=b"\xfd",  # special "unknown" value
        room_temperature=b"\x1e",  # 30°C
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == HVACMode.AUTO
    assert state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.IDLE


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_special_state_out_of_spec_falls_through(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test out-of-spec special state (0xFF) falls through to operation mode action."""
    entity_id = await _setup_climate_device_with_state(
        hass, init_integration, entity_registry, special_state=b"\xff"
    )

    state = hass.states.get(entity_id)
    assert state is not None
    # special state 0xFF is not in the dict → falls through to operation mode (COOL)
    assert state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.COOLING


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_operation_mode_out_of_spec_action_returns_none(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test out-of-spec operation mode (0xFF) returns None for hvac_action."""
    entity_id = await _setup_climate_device_with_state(
        hass, init_integration, entity_registry, operation_mode=b"\xff"
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "unknown"
    assert state.attributes.get(ATTR_HVAC_ACTION) is None


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_current_humidity(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test current_humidity reports indoor relative humidity."""
    entity_id = await _setup_climate_device_with_state(
        hass,
        init_integration,
        entity_registry,
        room_humidity=b"\x36",  # 54%
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes.get(ATTR_CURRENT_HUMIDITY) == 54


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_current_humidity_unavailable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test current_humidity returns None when value is out of range."""
    entity_id = await _setup_climate_device_with_state(
        hass,
        init_integration,
        entity_registry,
        room_humidity=b"\xfd",  # unmeasurable
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes.get(ATTR_CURRENT_HUMIDITY) is None


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_current_humidity_not_provided(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test current_humidity is None when device does not report humidity."""
    entity_id = await _setup_climate_device_with_state(
        hass, init_integration, entity_registry
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes.get(ATTR_CURRENT_HUMIDITY) is None


@pytest.mark.usefixtures("mock_echonet_lite_client")
async def test_create_climate_description_codecs_are_not_none(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """_DESCRIPTIONS must contain a description with all codecs set for 0x0130.

    This ensures the pyhems definitions for class 0x0130 always provide
    NumericCodec for the EPCs that the climate platform depends on.
    """
    description = _DESCRIPTIONS[DeviceClass.HOME_AIR_CONDITIONER]
    assert description.key == "climate"
    assert description.target_temp_prop is not None
    assert isinstance(description.target_temp_prop.codec, NumericCodec)
    assert description.room_temp_prop is not None
    assert isinstance(description.room_temp_prop.codec, NumericCodec)
    assert description.humidity_prop is not None
    assert isinstance(description.humidity_prop.codec, NumericCodec)


@pytest.mark.usefixtures("mock_echonet_lite_client")
async def test_climate_op_status_codec_is_binary(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """EPC 0x80 must return BinaryCodec for the home air conditioner class.

    This is an HA-side contract test: if pyhems changes its definitions in a
    way that breaks the climate entity's on/off behaviour, this test catches it.
    """
    codec = get_codec_for_epc(DeviceClass.HOME_AIR_CONDITIONER, EPC_OPERATION_STATUS)
    assert isinstance(codec, BinaryCodec)


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_no_climate_entity_for_other_class(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """No climate entity is created for non-climate device classes."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010108").hex()
    seoj_hex = "001101"  # Temperature sensor, not a climate device
    eoj = EOJ(int.from_bytes(bytes.fromhex(seoj_hex), "big"))

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
        entity_registry.async_get_entity_id(
            "climate", DOMAIN, f"{node_id}-{seoj_hex}-climate"
        )
        is None
    )


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_hvac_mode_unsupported_raises(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """An unsupported HVAC mode raises a ServiceValidationError."""
    entry = init_integration
    entity_id = await _setup_climate_device(hass, entry, entity_registry)

    climate_entity = hass.data["climate"].get_entity(entity_id)
    with pytest.raises(ServiceValidationError) as exc_info:
        await climate_entity.async_set_hvac_mode(HVACMode.HEAT_COOL)
    assert exc_info.value.translation_key == "unsupported_value"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_on_not_writable_raises(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """turn_on raises when operation status is not writable."""
    entry = init_integration
    entity_id = await _setup_climate_device(
        hass, entry, entity_registry, set_epcs={EPC_OPERATION_MODE}
    )

    climate_entity = hass.data["climate"].get_entity(entity_id)
    with pytest.raises(HomeAssistantError) as exc_info:
        await climate_entity.async_turn_on()
    assert exc_info.value.translation_key == "epc_not_writable"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_off_not_writable_raises(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """turn_off raises when operation status is not writable."""
    entry = init_integration
    entity_id = await _setup_climate_device(
        hass, entry, entity_registry, set_epcs={EPC_OPERATION_MODE}
    )

    climate_entity = hass.data["climate"].get_entity(entity_id)
    with pytest.raises(HomeAssistantError) as exc_info:
        await climate_entity.async_turn_off()
    assert exc_info.value.translation_key == "epc_not_writable"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_temperature_without_value_raises(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """set_temperature raises when no temperature is provided."""
    entry = init_integration
    entity_id = await _setup_climate_device(hass, entry, entity_registry)

    climate_entity = hass.data["climate"].get_entity(entity_id)
    with pytest.raises(ServiceValidationError) as exc_info:
        await climate_entity.async_set_temperature()
    assert exc_info.value.translation_key == "target_temperature_required"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_fan_mode_unsupported_raises(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """set_fan_mode raises for an unsupported fan mode."""
    entry = init_integration
    entity_id = await _setup_climate_device(hass, entry, entity_registry, fan_mode=True)

    climate_entity = hass.data["climate"].get_entity(entity_id)
    with pytest.raises(ServiceValidationError) as exc_info:
        await climate_entity.async_set_fan_mode("not_a_fan_mode")
    assert exc_info.value.translation_key == "unsupported_value"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_swing_mode_unsupported_raises(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """set_swing_mode raises for an unsupported swing mode."""
    entry = init_integration
    entity_id = await _setup_climate_device(
        hass, entry, entity_registry, swing_mode=True
    )

    climate_entity = hass.data["climate"].get_entity(entity_id)
    with pytest.raises(ServiceValidationError) as exc_info:
        await climate_entity.async_set_swing_mode("not_a_swing_mode")
    assert exc_info.value.translation_key == "unsupported_value"
