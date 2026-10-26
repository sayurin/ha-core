"""Tests for the ECHONET Lite cover platform."""

from unittest.mock import patch

from pyhems import EOJ, DeviceClass
import pytest

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    DOMAIN as COVER_DOMAIN,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
    SERVICE_SET_COVER_TILT_POSITION,
    SERVICE_STOP_COVER,
    CoverDeviceClass,
    CoverEntityFeature,
    CoverState,
)
from homeassistant.components.echonet_lite.const import (
    DOMAIN,
    EPC_COVER_ANGLE,
    EPC_COVER_OPEN_CLOSE,
    EPC_COVER_OPEN_CLOSED_STATUS,
    EPC_COVER_POSITION,
)
from homeassistant.const import ATTR_ENTITY_ID, Platform
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

_STATUS_OPEN = b"\x41"
_STATUS_CLOSED = b"\x42"
_STATUS_OPENING = b"\x43"
_STATUS_CLOSING = b"\x44"
_STATUS_STOPPED = b"\x45"


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable the cover platform for these tests."""
    return [Platform.COVER]


def _eoj_hex(class_code: int, instance: int = 1) -> str:
    return f"{class_code:04x}{instance:02x}"


async def _setup_cover(
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
    """Set up a cover device and return its entity_id."""
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

    unique_id = f"{node_id}-{seoj_hex}-cover"
    entity_id = entity_registry.async_get_entity_id("cover", DOMAIN, unique_id)
    assert entity_id is not None, f"cover entity {unique_id} not found"
    return entity_id


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_blind_state_open(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """0x0260 with status=open reports OPEN and BLIND device class."""
    entry = init_integration

    entity_id = await _setup_cover(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.ELECTRIC_BLIND_SHADE,
        advertised_epcs={
            EPC_COVER_OPEN_CLOSE,
            EPC_COVER_POSITION,
            EPC_COVER_ANGLE,
            EPC_COVER_OPEN_CLOSED_STATUS,
        },
        set_epcs={EPC_COVER_OPEN_CLOSE, EPC_COVER_POSITION, EPC_COVER_ANGLE},
        properties=[
            TestProperty(epc=EPC_COVER_OPEN_CLOSED_STATUS, edt=_STATUS_OPEN),
            TestProperty(epc=EPC_COVER_POSITION, edt=b"\x64"),  # 100%
            TestProperty(epc=EPC_COVER_ANGLE, edt=b"\x5a"),  # 90 deg
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == CoverState.OPEN
    assert state.attributes["device_class"] == CoverDeviceClass.BLIND
    assert state.attributes["current_position"] == 100
    assert state.attributes["current_tilt_position"] == 50  # 90/180*100
    features = state.attributes["supported_features"]
    assert features & CoverEntityFeature.SET_POSITION
    assert features & CoverEntityFeature.SET_TILT_POSITION


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_shutter_state_closed(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """0x0263 with status=closed reports CLOSED and SHUTTER device class."""
    entry = init_integration

    entity_id = await _setup_cover(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.ELECTRIC_RAIN_DOOR,
        advertised_epcs={EPC_COVER_OPEN_CLOSE, EPC_COVER_OPEN_CLOSED_STATUS},
        set_epcs={EPC_COVER_OPEN_CLOSE},
        properties=[
            TestProperty(epc=EPC_COVER_OPEN_CLOSED_STATUS, edt=_STATUS_CLOSED),
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == CoverState.CLOSED
    assert state.attributes["device_class"] == CoverDeviceClass.SHUTTER
    features = state.attributes["supported_features"]
    # Only OPEN/CLOSE/STOP supported when position/angle not writable.
    assert features & CoverEntityFeature.OPEN
    assert features & CoverEntityFeature.CLOSE
    assert features & CoverEntityFeature.STOP
    assert not features & CoverEntityFeature.SET_POSITION
    assert not features & CoverEntityFeature.SET_TILT_POSITION


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_cover_hides_open_close_stop_without_open_close_writability(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """OPEN/CLOSE/STOP are hidden when EPC 0xE0 is not writable."""
    entry = init_integration

    entity_id = await _setup_cover(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.ELECTRIC_BLIND_SHADE,
        advertised_epcs={EPC_COVER_OPEN_CLOSE, EPC_COVER_POSITION},
        set_epcs={EPC_COVER_POSITION},
        properties=[TestProperty(epc=EPC_COVER_POSITION, edt=b"\x32")],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    features = state.attributes["supported_features"]
    assert not features & CoverEntityFeature.OPEN
    assert not features & CoverEntityFeature.CLOSE
    assert not features & CoverEntityFeature.STOP
    assert features & CoverEntityFeature.SET_POSITION


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_cover_opening(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Status=opening reports OPENING."""
    entry = init_integration

    entity_id = await _setup_cover(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.ELECTRIC_BLIND_SHADE,
        advertised_epcs={EPC_COVER_OPEN_CLOSE, EPC_COVER_OPEN_CLOSED_STATUS},
        set_epcs={EPC_COVER_OPEN_CLOSE},
        properties=[
            TestProperty(epc=EPC_COVER_OPEN_CLOSED_STATUS, edt=_STATUS_OPENING),
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == CoverState.OPENING


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_cover_closed_from_position(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Without status EPC, position=0 is interpreted as closed."""
    entry = init_integration

    entity_id = await _setup_cover(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.ELECTRIC_BLIND_SHADE,
        advertised_epcs={EPC_COVER_OPEN_CLOSE, EPC_COVER_POSITION},
        set_epcs={EPC_COVER_OPEN_CLOSE, EPC_COVER_POSITION},
        properties=[TestProperty(epc=EPC_COVER_POSITION, edt=b"\x00")],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == CoverState.CLOSED


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_open_close_stop_services(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """open/close/stop services send the proper command bytes on EPC 0xE0."""
    entry = init_integration

    entity_id = await _setup_cover(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.ELECTRIC_BLIND_SHADE,
        advertised_epcs={EPC_COVER_OPEN_CLOSE, EPC_COVER_OPEN_CLOSED_STATUS},
        set_epcs={EPC_COVER_OPEN_CLOSE},
        properties=[
            TestProperty(epc=EPC_COVER_OPEN_CLOSED_STATUS, edt=_STATUS_STOPPED),
        ],
    )

    for service, expected in (
        (SERVICE_OPEN_COVER, b"\x41"),
        (SERVICE_CLOSE_COVER, b"\x42"),
        (SERVICE_STOP_COVER, b"\x43"),
    ):
        mock_echonet_lite_client.send.reset_mock()
        with patch.object(
            entry.runtime_data.property_poller, "schedule_immediate_poll"
        ):
            await hass.services.async_call(
                COVER_DOMAIN,
                service,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
        assert mock_echonet_lite_client.send.call_count == 1
        call = mock_echonet_lite_client.send.call_args_list[0]
        props = call.args[1].properties
        assert len(props) == 1
        assert props[0].epc == EPC_COVER_OPEN_CLOSE
        assert props[0].edt == expected


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_position(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """set_cover_position writes EPC 0xE1 = percentage."""
    entry = init_integration

    entity_id = await _setup_cover(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.ELECTRIC_BLIND_SHADE,
        advertised_epcs={EPC_COVER_OPEN_CLOSE, EPC_COVER_POSITION},
        set_epcs={EPC_COVER_OPEN_CLOSE, EPC_COVER_POSITION},
        properties=[TestProperty(epc=EPC_COVER_POSITION, edt=b"\x32")],  # 50%
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_SET_COVER_POSITION,
            {ATTR_ENTITY_ID: entity_id, ATTR_POSITION: 75},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    props = mock_echonet_lite_client.send.call_args_list[0].args[1].properties
    assert len(props) == 1
    assert props[0].epc == EPC_COVER_POSITION
    assert props[0].edt == b"\x4b"  # 75


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_set_tilt_position_scales_to_degrees(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """set_cover_tilt_position scales 0-100 to 0-180 degrees on EPC 0xE2."""
    entry = init_integration

    entity_id = await _setup_cover(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.ELECTRIC_BLIND_SHADE,
        advertised_epcs={EPC_COVER_OPEN_CLOSE, EPC_COVER_ANGLE},
        set_epcs={EPC_COVER_OPEN_CLOSE, EPC_COVER_ANGLE},
        properties=[TestProperty(epc=EPC_COVER_ANGLE, edt=b"\x00")],
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_SET_COVER_TILT_POSITION,
            {ATTR_ENTITY_ID: entity_id, ATTR_TILT_POSITION: 50},
            blocking=True,
        )

    props = mock_echonet_lite_client.send.call_args_list[0].args[1].properties
    assert len(props) == 1
    assert props[0].epc == EPC_COVER_ANGLE
    # 50% of 180 = 90 deg
    assert props[0].edt == bytes([90])


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_open_close_tilt(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """open_cover_tilt sends 180 deg and close_cover_tilt sends 0 deg."""
    entry = init_integration

    entity_id = await _setup_cover(
        hass,
        entry,
        entity_registry,
        class_code=DeviceClass.ELECTRIC_BLIND_SHADE,
        advertised_epcs={EPC_COVER_OPEN_CLOSE, EPC_COVER_ANGLE},
        set_epcs={EPC_COVER_OPEN_CLOSE, EPC_COVER_ANGLE},
        properties=[TestProperty(epc=EPC_COVER_ANGLE, edt=b"\x5a")],
    )

    for service, expected_deg in (
        ("open_cover_tilt", 180),
        ("close_cover_tilt", 0),
    ):
        mock_echonet_lite_client.send.reset_mock()
        with patch.object(
            entry.runtime_data.property_poller, "schedule_immediate_poll"
        ):
            await hass.services.async_call(
                COVER_DOMAIN,
                service,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
        props = mock_echonet_lite_client.send.call_args_list[0].args[1].properties
        assert len(props) == 1
        assert props[0].epc == EPC_COVER_ANGLE
        assert props[0].edt == bytes([expected_deg])


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_unknown_class_code_not_added(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Devices with a class code not in descriptions are ignored."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    # 0x0261 is in the "window covering" class group but is not BLIND (0x0260)
    # nor SHUTTER (0x0263), so it is absent from the cover descriptions dict.
    unknown_class_code = 0x0261
    node_id = bytes.fromhex("010107").hex()
    eoj = EOJ((unknown_class_code << 8) | 0x01)

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP,
            edt=make_property_map_edt(EPC_COVER_OPEN_CLOSE),
        ),
        TestProperty(
            epc=EPC_SET_PROPERTY_MAP,
            edt=make_property_map_edt(EPC_COVER_OPEN_CLOSE),
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entities = entity_registry.entities.get_entries_for_config_entry_id(entry.entry_id)
    cover_entities = [e for e in entities if e.domain == "cover"]
    assert not cover_entities
