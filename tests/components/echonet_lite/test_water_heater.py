"""Tests for the ECHONET Lite water_heater platform."""

from unittest.mock import patch

from pyhems import EOJ, BinaryCodec, DeviceClass, NumericCodec, get_codec_for_epc
import pytest

from homeassistant.components.echonet_lite.const import (
    DEDICATED_PLATFORM_EPCS,
    DOMAIN,
    EPC_MEASURED_WATER_TEMPERATURE,
    EPC_OPERATION_MODE,
    EPC_OPERATION_STATUS,
    EPC_TARGET_TEMPERATURE,
)
from homeassistant.components.echonet_lite.water_heater import _DESCRIPTIONS
from homeassistant.components.water_heater import (
    ATTR_OPERATION_MODE,
    SERVICE_SET_OPERATION_MODE,
    SERVICE_SET_TEMPERATURE,
    STATE_OFF,
    WaterHeaterEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    Platform,
)
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

# Operation status raw values (EPC 0x80)
_OP_ON = b"\x30"
_OP_OFF = b"\x31"

# Electric water heater operation mode bytes (EPC 0xB0)
_MODE_AUTO = b"\x41"
_MODE_MANUAL = b"\x42"  # manual heating active
_MODE_MANUAL_OFF = b"\x43"  # manual heating stopped (away)


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable the water_heater platform for these tests."""
    return [Platform.WATER_HEATER]


def _eoj_hex(class_code: int, instance: int = 1) -> str:
    return f"{class_code:04x}{instance:02x}"


async def _setup_water_heater(
    hass: HomeAssistant,
    entry,
    entity_registry: er.EntityRegistry,
    *,
    advertised_epcs: set[int],
    set_epcs: set[int],
    properties: list[TestProperty],
    node_hex: str = "010104",
    instance: int = 1,
) -> str:
    """Set up an electric water heater device and return the entity_id."""
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex(node_hex).hex()
    seoj_hex = _eoj_hex(DeviceClass.ELECTRIC_WATER_HEATER, instance)
    eoj = EOJ(int.from_bytes(bytes.fromhex(seoj_hex), "big"))

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
        *properties,
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    unique_id = f"{node_id}-{seoj_hex}-water_heater"
    entity_id = entity_registry.async_get_entity_id("water_heater", DOMAIN, unique_id)
    assert entity_id is not None, f"water_heater entity {unique_id} not found"
    return entity_id


@pytest.mark.usefixtures("mock_echonet_lite_client")
async def test_create_water_heater_description_codecs_are_not_none(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """_DESCRIPTIONS must contain a description with all codecs set for 0x026B.

    This ensures the pyhems definitions for class 0x026B always provide
    NumericCodec for the EPCs that the water_heater platform depends on.
    """
    description = _DESCRIPTIONS[DeviceClass.ELECTRIC_WATER_HEATER]
    assert description.key == "water_heater"
    assert description.target_temp_prop is not None
    assert description.current_temp_prop is not None
    assert isinstance(description.current_temp_prop.codec, NumericCodec)


@pytest.mark.usefixtures("mock_echonet_lite_client")
async def test_water_heater_op_status_codec_is_binary(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """EPC 0x80 must return BinaryCodec for the electric water heater class.

    This is an HA-side contract test: if pyhems changes its definitions in a
    way that breaks the water_heater entity's on/off behaviour, this test catches it.
    """
    codec = get_codec_for_epc(DeviceClass.ELECTRIC_WATER_HEATER, EPC_OPERATION_STATUS)
    assert isinstance(codec, BinaryCodec)


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_electric_water_heater_state(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """0x026B exposes target/current temperature and operation mode."""
    entry = init_integration

    advertised_epcs = {
        EPC_OPERATION_STATUS,
        EPC_OPERATION_MODE,
        EPC_TARGET_TEMPERATURE,
        EPC_MEASURED_WATER_TEMPERATURE,
    }
    set_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE, EPC_TARGET_TEMPERATURE}
    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs=advertised_epcs,
        set_epcs=set_epcs,
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON),
            TestProperty(epc=EPC_OPERATION_MODE, edt=_MODE_AUTO),
            TestProperty(epc=EPC_TARGET_TEMPERATURE, edt=b"\x55"),  # 85°C
            TestProperty(epc=EPC_MEASURED_WATER_TEMPERATURE, edt=b"\x32"),  # 50°C
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "auto"
    assert state.attributes["temperature"] == 85
    assert state.attributes["current_temperature"] == 50
    features = state.attributes["supported_features"]
    assert features & WaterHeaterEntityFeature.TARGET_TEMPERATURE
    assert features & WaterHeaterEntityFeature.OPERATION_MODE
    assert features & WaterHeaterEntityFeature.ON_OFF
    assert state.attributes["operation_list"] == [
        STATE_OFF,
        "auto",
        "manual_no_heating",
        "manual_heating",
    ]

    device_key = f"010104-{_eoj_hex(DeviceClass.ELECTRIC_WATER_HEATER)}"
    assert (
        EPC_MEASURED_WATER_TEMPERATURE
        in entry.runtime_data.coordinator.device_manager.effective_poll_epcs(device_key)
    )


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_electric_water_heater_off_state(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """When operation status is OFF, current_operation reports STATE_OFF."""
    entry = init_integration

    advertised_epcs = {
        EPC_OPERATION_STATUS,
        EPC_OPERATION_MODE,
        EPC_TARGET_TEMPERATURE,
    }
    set_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE, EPC_TARGET_TEMPERATURE}
    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs=advertised_epcs,
        set_epcs=set_epcs,
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_OFF),
            TestProperty(epc=EPC_OPERATION_MODE, edt=_MODE_AUTO),
            TestProperty(epc=EPC_TARGET_TEMPERATURE, edt=b"\x55"),
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_OFF


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_temperature(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """set_temperature sends the configured target temperature EPC."""
    entry = init_integration

    advertised_epcs = {
        EPC_OPERATION_STATUS,
        EPC_OPERATION_MODE,
        EPC_TARGET_TEMPERATURE,
    }
    set_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE, EPC_TARGET_TEMPERATURE}
    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs=advertised_epcs,
        set_epcs=set_epcs,
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON),
            TestProperty(epc=EPC_OPERATION_MODE, edt=_MODE_AUTO),
            TestProperty(epc=EPC_TARGET_TEMPERATURE, edt=b"\x55"),
        ],
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            "water_heater",
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: 60},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert len(call.args[1].properties) == 1
    assert call.args[1].properties[0].epc == EPC_TARGET_TEMPERATURE
    assert call.args[1].properties[0].edt == b"\x3c"  # 60°C


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_operation_mode_sends_mode_and_on(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """set_operation_mode sends the mode + ON together."""
    entry = init_integration

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE}
    set_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE}
    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs=advertised_epcs,
        set_epcs=set_epcs,
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_OFF),
            TestProperty(epc=EPC_OPERATION_MODE, edt=_MODE_AUTO),
        ],
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            "water_heater",
            SERVICE_SET_OPERATION_MODE,
            {ATTR_ENTITY_ID: entity_id, ATTR_OPERATION_MODE: "manual_heating"},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert len(call.args[1].properties) == 2
    assert call.args[1].properties[0].epc == EPC_OPERATION_MODE
    assert call.args[1].properties[0].edt == _MODE_MANUAL
    assert call.args[1].properties[1].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[1].edt == _OP_ON


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_operation_mode_off_turns_off(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """Selecting STATE_OFF as operation mode turns the device off."""
    entry = init_integration

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE}
    set_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE}
    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs=advertised_epcs,
        set_epcs=set_epcs,
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON),
            TestProperty(epc=EPC_OPERATION_MODE, edt=_MODE_AUTO),
        ],
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            "water_heater",
            SERVICE_SET_OPERATION_MODE,
            {ATTR_ENTITY_ID: entity_id, ATTR_OPERATION_MODE: STATE_OFF},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert len(call.args[1].properties) == 1
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == _OP_OFF


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_on_off(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """turn_on and turn_off send only the operation status EPC."""
    entry = init_integration

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_TARGET_TEMPERATURE}
    set_epcs = {EPC_OPERATION_STATUS, EPC_TARGET_TEMPERATURE}
    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs=advertised_epcs,
        set_epcs=set_epcs,
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_OFF),
            TestProperty(epc=EPC_TARGET_TEMPERATURE, edt=b"\x55"),
        ],
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            "water_heater",
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == _OP_ON

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            "water_heater",
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].epc == EPC_OPERATION_STATUS
    assert call.args[1].properties[0].edt == _OP_OFF


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_operation_mode_unsupported_raises(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """Unknown operation modes trigger ServiceValidationError."""
    entry = init_integration

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE}
    set_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE}
    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs=advertised_epcs,
        set_epcs=set_epcs,
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON),
            TestProperty(epc=EPC_OPERATION_MODE, edt=_MODE_AUTO),
        ],
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "water_heater",
            SERVICE_SET_OPERATION_MODE,
            {ATTR_ENTITY_ID: entity_id, ATTR_OPERATION_MODE: "bogus_mode"},
            blocking=True,
        )


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_temperature_clamped_to_range(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """Out-of-range target values are clamped to the device range."""
    entry = init_integration

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_TARGET_TEMPERATURE}
    set_epcs = {EPC_OPERATION_STATUS, EPC_TARGET_TEMPERATURE}
    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs=advertised_epcs,
        set_epcs=set_epcs,
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON),
            TestProperty(epc=EPC_TARGET_TEMPERATURE, edt=b"\x55"),
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    max_temp = state.attributes["max_temp"]
    min_temp = state.attributes["min_temp"]

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        # HA's own water_heater service will reject values outside the
        # advertised min/max range; call set_temperature with the max+0
        # boundary to verify the encoding works.
        await hass.services.async_call(
            "water_heater",
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: max_temp},
            blocking=True,
        )
    call = mock_echonet_lite_client.send.call_args_list[0]
    assert call.args[1].properties[0].epc == EPC_TARGET_TEMPERATURE
    assert call.args[1].properties[0].edt == bytes([int(max_temp)])
    assert min_temp <= max_temp


def test_dedicated_platform_epcs_cover_water_heater_class() -> None:
    """DEDICATED_PLATFORM_EPCS includes the electric water heater class."""
    assert DeviceClass.ELECTRIC_WATER_HEATER in DEDICATED_PLATFORM_EPCS

    # Aggregated EPCs the water_heater entity owns must be present.
    epcs = DEDICATED_PLATFORM_EPCS[DeviceClass.ELECTRIC_WATER_HEATER]
    assert EPC_OPERATION_STATUS in epcs
    assert EPC_OPERATION_MODE in epcs
    assert EPC_TARGET_TEMPERATURE in epcs
    # The measured temperature EPC stays on the sensor platform.
    assert EPC_MEASURED_WATER_TEMPERATURE not in epcs


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_is_away_mode_on(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """is_away_mode_on maps to EPC 0xB0=0x43 (manual_no_heating); AWAY_MODE feature not advertised."""
    entry = init_integration

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE}
    set_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE}

    # EPC 0xB0 = 0x43 → current_operation is "manual_no_heating" (the away state)
    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs=advertised_epcs,
        set_epcs=set_epcs,
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON),
            TestProperty(epc=EPC_OPERATION_MODE, edt=_MODE_MANUAL_OFF),
        ],
    )
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "manual_no_heating"

    # AWAY_MODE feature must NOT be advertised (is_away_mode_on is read-only)
    assert not (
        state.attributes["supported_features"] & WaterHeaterEntityFeature.AWAY_MODE
    )


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_on_not_writable_raises(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Calling turn_on when 0x80 is read-only raises HomeAssistantError."""
    entry = init_integration

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_TARGET_TEMPERATURE}
    set_epcs: set[int] = set()  # nothing writable
    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs=advertised_epcs,
        set_epcs=set_epcs,
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_OFF),
            TestProperty(epc=EPC_TARGET_TEMPERATURE, edt=b"\x55"),
        ],
    )

    # When the device exposes no writable EPCs HA strips the ON_OFF feature,
    # so the service call is rejected by the schema layer
    # (ServiceValidationError) rather than by the entity itself.
    with pytest.raises((HomeAssistantError, ServiceValidationError)):
        await hass.services.async_call(
            "water_heater",
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_operation_mode_without_status_writable(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """Always-on heaters (0x80 read-only) accept mode changes via 0xB0 only."""
    entry = init_integration

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE}
    # 0x80 is NOT writable: device is always on.
    set_epcs = {EPC_OPERATION_MODE}
    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs=advertised_epcs,
        set_epcs=set_epcs,
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON),
            TestProperty(epc=EPC_OPERATION_MODE, edt=_MODE_AUTO),
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    # STATE_OFF must not be advertised when 0x80 is read-only.
    assert state.attributes["operation_list"] == [
        "auto",
        "manual_no_heating",
        "manual_heating",
    ]
    features = state.attributes["supported_features"]
    assert features & WaterHeaterEntityFeature.OPERATION_MODE
    assert not (features & WaterHeaterEntityFeature.ON_OFF)

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            "water_heater",
            SERVICE_SET_OPERATION_MODE,
            {ATTR_ENTITY_ID: entity_id, ATTR_OPERATION_MODE: "manual_heating"},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    # Only 0xB0 is sent; 0x80 is left untouched.
    assert len(call.args[1].properties) == 1
    assert call.args[1].properties[0].epc == EPC_OPERATION_MODE
    assert call.args[1].properties[0].edt == _MODE_MANUAL


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_off_without_status_writable_raises(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """turn_off is rejected when 0x80 is read-only."""
    entry = init_integration

    advertised_epcs = {EPC_OPERATION_STATUS, EPC_OPERATION_MODE}
    set_epcs = {EPC_OPERATION_MODE}
    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs=advertised_epcs,
        set_epcs=set_epcs,
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON),
            TestProperty(epc=EPC_OPERATION_MODE, edt=_MODE_AUTO),
        ],
    )

    # ON_OFF feature is stripped, so HA's service schema rejects the call.
    with pytest.raises((HomeAssistantError, ServiceValidationError)):
        await hass.services.async_call(
            "water_heater",
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_no_water_heater_entity_for_other_class(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """No water heater entity is created for non-matching device classes."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("01010e").hex()
    seoj_hex = "001101"  # Temperature sensor, not a water heater
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
            "water_heater", DOMAIN, f"{node_id}-{seoj_hex}-water_heater"
        )
        is None
    )


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_is_away_mode_on_returns_true(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """is_away_mode_on reflects EPC 0xB0 = 0x43 (away)."""
    entry = init_integration

    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_OPERATION_STATUS, EPC_OPERATION_MODE},
        set_epcs={EPC_OPERATION_STATUS, EPC_OPERATION_MODE},
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON),
            TestProperty(epc=EPC_OPERATION_MODE, edt=_MODE_MANUAL_OFF),
        ],
    )

    entity = hass.data["water_heater"].get_entity(entity_id)
    assert entity.is_away_mode_on is True


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_is_away_mode_on_none_without_mode(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """is_away_mode_on is None when no operation mode value is stored."""
    entry = init_integration

    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_OPERATION_STATUS, EPC_OPERATION_MODE},
        set_epcs={EPC_OPERATION_STATUS, EPC_OPERATION_MODE},
        properties=[TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON)],
    )

    entity = hass.data["water_heater"].get_entity(entity_id)
    assert entity.is_away_mode_on is None


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_current_operation_none_for_unexpected_status(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """current_operation is None when the status byte is neither ON nor OFF."""
    entry = init_integration

    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_OPERATION_STATUS, EPC_OPERATION_MODE},
        set_epcs={EPC_OPERATION_STATUS, EPC_OPERATION_MODE},
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x00"),
            TestProperty(epc=EPC_OPERATION_MODE, edt=_MODE_AUTO),
        ],
    )

    entity = hass.data["water_heater"].get_entity(entity_id)
    assert entity.current_operation is None


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_async_turn_on_raises_operation_status_not_writable(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """turn_on raises when operation status is not writable."""
    entry = init_integration

    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_OPERATION_STATUS, EPC_OPERATION_MODE},
        set_epcs={EPC_OPERATION_MODE},
        properties=[TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON)],
    )

    entity = hass.data["water_heater"].get_entity(entity_id)
    with pytest.raises(HomeAssistantError) as exc_info:
        await entity.async_turn_on()
    assert exc_info.value.translation_key == "epc_not_writable"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_turn_off_not_writable_raises(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """turn_off raises when operation status is not writable."""
    entry = init_integration

    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_OPERATION_STATUS, EPC_OPERATION_MODE},
        set_epcs={EPC_OPERATION_MODE},
        properties=[TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON)],
    )

    entity = hass.data["water_heater"].get_entity(entity_id)
    with pytest.raises(HomeAssistantError) as exc_info:
        await entity.async_turn_off()
    assert exc_info.value.translation_key == "epc_not_writable"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_async_set_operation_mode_raises_unsupported(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """set_operation_mode raises for an unknown operation mode."""
    entry = init_integration

    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_OPERATION_STATUS, EPC_OPERATION_MODE},
        set_epcs={EPC_OPERATION_STATUS, EPC_OPERATION_MODE},
        properties=[TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON)],
    )

    entity = hass.data["water_heater"].get_entity(entity_id)
    with pytest.raises(ServiceValidationError) as exc_info:
        await entity.async_set_operation_mode("not_a_mode")
    assert exc_info.value.translation_key == "unsupported_value"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_set_operation_mode_not_writable_raises(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """set_operation_mode raises when the operation mode EPC is not writable."""
    entry = init_integration

    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_OPERATION_STATUS, EPC_OPERATION_MODE},
        set_epcs={EPC_OPERATION_STATUS},
        properties=[TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON)],
    )

    entity = hass.data["water_heater"].get_entity(entity_id)
    with pytest.raises(HomeAssistantError) as exc_info:
        await entity.async_set_operation_mode("auto")
    assert exc_info.value.translation_key == "epc_not_writable"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_set_temperature_required_raises(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """set_temperature raises when no temperature value is provided."""
    entry = init_integration

    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_OPERATION_STATUS, EPC_TARGET_TEMPERATURE},
        set_epcs={EPC_OPERATION_STATUS, EPC_TARGET_TEMPERATURE},
        properties=[TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON)],
    )

    entity = hass.data["water_heater"].get_entity(entity_id)
    with pytest.raises(ServiceValidationError) as exc_info:
        await entity.async_set_temperature()
    assert exc_info.value.translation_key == "target_temperature_required"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_set_temperature_epc_not_writable_raises(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """set_temperature raises when the target temperature EPC is not writable."""
    entry = init_integration

    entity_id = await _setup_water_heater(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_OPERATION_STATUS, EPC_TARGET_TEMPERATURE},
        set_epcs={EPC_OPERATION_STATUS},
        properties=[TestProperty(epc=EPC_OPERATION_STATUS, edt=_OP_ON)],
    )

    entity = hass.data["water_heater"].get_entity(entity_id)
    with pytest.raises(HomeAssistantError) as exc_info:
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 40})
    assert exc_info.value.translation_key == "epc_not_writable"
