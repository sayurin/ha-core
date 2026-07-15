"""Tests for the ECHONET Lite light platform."""

from unittest.mock import patch

from pyhems import EOJ, BinaryCodec, DeviceClass, get_codec_for_epc
import pytest

from homeassistant.components.echonet_lite.const import (
    DOMAIN,
    EPC_LIGHT_COLOR,
    EPC_LIGHT_LEVEL,
    EPC_LIGHTING_MODE,
    EPC_OPERATION_STATUS,
)
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    DOMAIN as LIGHT_DOMAIN,
    ColorMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import (
    EPC_GET_PROPERTY_MAP,
    EPC_MANUFACTURER_CODE,
    EPC_SET_PROPERTY_MAP,
    TEST_MANUFACTURER_CODE,
    TestProperty,
    make_property_map_edt,
)

_POWER_ON = b"\x30"
_POWER_OFF = b"\x31"


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable the light platform for these tests."""
    return [Platform.LIGHT]


def _eoj_hex(class_code: int, instance: int = 1) -> str:
    return f"{class_code:04x}{instance:02x}"


async def _setup_light(
    hass: HomeAssistant,
    entry,
    entity_registry: er.EntityRegistry,
    *,
    class_code: int,
    advertised_epcs: set[int],
    set_epcs: set[int],
    properties: list[TestProperty],
    node_hex: str = "010104",
    instance: int = 1,
) -> str:
    """Set up a light device and return its entity_id."""
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex(node_hex).hex()
    seoj_hex = _eoj_hex(class_code, instance)
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

    unique_id = f"{node_id}-{seoj_hex}-light"
    entity_id = entity_registry.async_get_entity_id("light", DOMAIN, unique_id)
    assert entity_id is not None, f"light entity {unique_id} not found"
    return entity_id


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_mono_lighting_state(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """0x0291 with brightness reports ON with brightness color mode."""
    entry = init_integration

    entity_id = await _setup_light(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.MONO_FUNCTIONAL_LIGHTING,
        advertised_epcs={EPC_OPERATION_STATUS, EPC_LIGHT_LEVEL},
        set_epcs={EPC_OPERATION_STATUS, EPC_LIGHT_LEVEL},
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_POWER_ON),
            TestProperty(epc=EPC_LIGHT_LEVEL, edt=b"\x32"),  # 50%
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["color_mode"] == ColorMode.BRIGHTNESS
    # 50% -> 127 (round(50*255/100)) or 128 depending on rounding
    assert state.attributes["brightness"] in (127, 128)
    assert state.attributes["supported_color_modes"] == [ColorMode.BRIGHTNESS]


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_general_lighting_color_temp_and_effect(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """0x0290 advertises color_temp + effect."""
    entry = init_integration

    entity_id = await _setup_light(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.GENERAL_LIGHTING,
        advertised_epcs={
            EPC_OPERATION_STATUS,
            EPC_LIGHT_LEVEL,
            EPC_LIGHT_COLOR,
            EPC_LIGHTING_MODE,
        },
        set_epcs={
            EPC_OPERATION_STATUS,
            EPC_LIGHT_LEVEL,
            EPC_LIGHT_COLOR,
            EPC_LIGHTING_MODE,
        },
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_POWER_ON),
            TestProperty(epc=EPC_LIGHT_LEVEL, edt=b"\x64"),  # 100%
            TestProperty(epc=EPC_LIGHT_COLOR, edt=b"\x42"),  # White
            TestProperty(epc=EPC_LIGHTING_MODE, edt=b"\x41"),  # Auto
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["color_mode"] == ColorMode.COLOR_TEMP
    assert state.attributes["color_temp_kelvin"] == 4000
    assert state.attributes["min_color_temp_kelvin"] == 2700
    assert state.attributes["max_color_temp_kelvin"] == 6500
    assert state.attributes["effect"] == "auto"
    assert set(state.attributes[ATTR_EFFECT_LIST]) == {
        "auto",
        "normal",
        "night",
        "color",
    }


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_light_off(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Operation status off reports OFF."""
    entry = init_integration

    entity_id = await _setup_light(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.MONO_FUNCTIONAL_LIGHTING,
        advertised_epcs={EPC_OPERATION_STATUS, EPC_LIGHT_LEVEL},
        set_epcs={EPC_OPERATION_STATUS, EPC_LIGHT_LEVEL},
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_POWER_OFF),
            TestProperty(epc=EPC_LIGHT_LEVEL, edt=b"\x64"),
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_OFF


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_on_with_brightness(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """turn_on with brightness sends power-on and EPC 0xB0 in one SetC."""
    entry = init_integration

    entity_id = await _setup_light(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.MONO_FUNCTIONAL_LIGHTING,
        advertised_epcs={EPC_OPERATION_STATUS, EPC_LIGHT_LEVEL},
        set_epcs={EPC_OPERATION_STATUS, EPC_LIGHT_LEVEL},
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_POWER_OFF),
            TestProperty(epc=EPC_LIGHT_LEVEL, edt=b"\x32"),
        ],
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: entity_id, ATTR_BRIGHTNESS: 128},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    properties = mock_echonet_lite_client.send.call_args_list[0].args[1].properties
    first, second = properties
    assert first.epc == EPC_OPERATION_STATUS
    assert first.edt == _POWER_ON
    assert second.epc == EPC_LIGHT_LEVEL
    # 128 / 255 * 100 ≈ 50
    assert second.edt == bytes([50])


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_on_with_color_temp_snaps_to_preset(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """Color temperature snaps to the closest preset."""
    entry = init_integration

    entity_id = await _setup_light(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.GENERAL_LIGHTING,
        advertised_epcs={
            EPC_OPERATION_STATUS,
            EPC_LIGHT_LEVEL,
            EPC_LIGHT_COLOR,
            EPC_LIGHTING_MODE,
        },
        set_epcs={
            EPC_OPERATION_STATUS,
            EPC_LIGHT_LEVEL,
            EPC_LIGHT_COLOR,
            EPC_LIGHTING_MODE,
        },
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_POWER_ON),
            TestProperty(epc=EPC_LIGHT_LEVEL, edt=b"\x64"),
            TestProperty(epc=EPC_LIGHT_COLOR, edt=b"\x41"),
            TestProperty(epc=EPC_LIGHTING_MODE, edt=b"\x41"),
        ],
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            # 5400K is closer to 5000K (daylight white = 0x43)
            {ATTR_ENTITY_ID: entity_id, ATTR_COLOR_TEMP_KELVIN: 5400},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    color_props = mock_echonet_lite_client.send.call_args_list[0].args[1].properties
    assert color_props[0].epc == EPC_OPERATION_STATUS
    assert color_props[0].edt == _POWER_ON
    assert color_props[1].epc == EPC_LIGHT_COLOR
    assert color_props[1].edt == b"\x43"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_on_with_effect(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """Effect maps to EPC 0xB6 raw value."""
    entry = init_integration

    entity_id = await _setup_light(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.GENERAL_LIGHTING,
        advertised_epcs={
            EPC_OPERATION_STATUS,
            EPC_LIGHT_LEVEL,
            EPC_LIGHT_COLOR,
            EPC_LIGHTING_MODE,
        },
        set_epcs={
            EPC_OPERATION_STATUS,
            EPC_LIGHT_LEVEL,
            EPC_LIGHT_COLOR,
            EPC_LIGHTING_MODE,
        },
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_POWER_ON),
            TestProperty(epc=EPC_LIGHT_LEVEL, edt=b"\x64"),
            TestProperty(epc=EPC_LIGHT_COLOR, edt=b"\x42"),
            TestProperty(epc=EPC_LIGHTING_MODE, edt=b"\x41"),
        ],
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: entity_id, ATTR_EFFECT: "night"},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    effect_props = mock_echonet_lite_client.send.call_args_list[0].args[1].properties
    assert effect_props[0].epc == EPC_OPERATION_STATUS
    assert effect_props[0].edt == _POWER_ON
    assert effect_props[1].epc == EPC_LIGHTING_MODE
    assert effect_props[1].edt == b"\x43"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_turn_off(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """turn_off writes operation status 0x31."""
    entry = init_integration

    entity_id = await _setup_light(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.MONO_FUNCTIONAL_LIGHTING,
        advertised_epcs={EPC_OPERATION_STATUS, EPC_LIGHT_LEVEL},
        set_epcs={EPC_OPERATION_STATUS, EPC_LIGHT_LEVEL},
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_POWER_ON),
            TestProperty(epc=EPC_LIGHT_LEVEL, edt=b"\x64"),
        ],
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    props = mock_echonet_lite_client.send.call_args_list[0].args[1].properties
    assert len(props) == 1
    assert props[0].epc == EPC_OPERATION_STATUS
    assert props[0].edt == _POWER_OFF


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_no_light_entity_for_other_class(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """No light entity is created for non-lighting device classes."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("01010d").hex()
    seoj_hex = "001101"  # Temperature sensor, not a light
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
            "light", DOMAIN, f"{node_id}-{seoj_hex}-light"
        )
        is None
    )


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_onoff_only_light(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """A light with only operation status writable uses the ONOFF color mode."""
    entry = init_integration

    entity_id = await _setup_light(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.GENERAL_LIGHTING,
        advertised_epcs={EPC_OPERATION_STATUS},
        set_epcs={EPC_OPERATION_STATUS},
        properties=[TestProperty(epc=EPC_OPERATION_STATUS, edt=_POWER_ON)],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["supported_color_modes"] == [ColorMode.ONOFF]

    light_entity = hass.data[LIGHT_DOMAIN].get_entity(entity_id)
    assert light_entity is not None
    # Unsupported features return None even though no value is stored.
    assert light_entity.brightness is None
    assert light_entity.color_temp_kelvin is None
    assert light_entity.effect is None


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_full_feature_light_without_values(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """A full-feature light reports None for every attribute when no value is set."""
    entry = init_integration

    entity_id = await _setup_light(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.GENERAL_LIGHTING,
        advertised_epcs={
            EPC_OPERATION_STATUS,
            EPC_LIGHT_LEVEL,
            EPC_LIGHT_COLOR,
            EPC_LIGHTING_MODE,
        },
        set_epcs={
            EPC_OPERATION_STATUS,
            EPC_LIGHT_LEVEL,
            EPC_LIGHT_COLOR,
            EPC_LIGHTING_MODE,
        },
        properties=[],  # no property values stored yet
    )

    light_entity = hass.data[LIGHT_DOMAIN].get_entity(entity_id)
    assert light_entity is not None
    assert light_entity.is_on is None
    assert light_entity.brightness is None
    assert light_entity.color_temp_kelvin is None
    assert light_entity.effect is None


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_full_feature_light_effect_value(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """The effect attribute is decoded from the lighting mode value."""
    entry = init_integration

    entity_id = await _setup_light(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.GENERAL_LIGHTING,
        advertised_epcs={
            EPC_OPERATION_STATUS,
            EPC_LIGHT_LEVEL,
            EPC_LIGHT_COLOR,
            EPC_LIGHTING_MODE,
        },
        set_epcs={
            EPC_OPERATION_STATUS,
            EPC_LIGHT_LEVEL,
            EPC_LIGHT_COLOR,
            EPC_LIGHTING_MODE,
        },
        properties=[
            TestProperty(epc=EPC_OPERATION_STATUS, edt=_POWER_ON),
            TestProperty(epc=EPC_LIGHTING_MODE, edt=b"\x41"),  # auto
        ],
    )

    light_entity = hass.data[LIGHT_DOMAIN].get_entity(entity_id)
    assert light_entity is not None
    assert light_entity.effect is not None


@pytest.mark.usefixtures("mock_echonet_lite_client")
async def test_light_op_status_codec_is_binary(
    hass: HomeAssistant, init_integration
) -> None:
    """EPC 0x80 must return BinaryCodec for the general lighting class.

    This is an HA-side contract test: if pyhems changes its definitions in a
    way that breaks the light entity's on/off behaviour, this test catches it.
    """
    codec = get_codec_for_epc(DeviceClass.GENERAL_LIGHTING, EPC_OPERATION_STATUS)
    assert isinstance(codec, BinaryCodec)
