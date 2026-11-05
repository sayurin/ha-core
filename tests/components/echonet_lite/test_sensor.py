"""Tests for the ECHONET Lite sensor platform."""

import struct
import time
from unittest.mock import AsyncMock, patch

from pyhems import (
    EOJ,
    ESV,
    REGISTRY,
    EntityDefinition,
    NodeState,
    Quantity,
    ValueKind,
    decode_collection_page,
)
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.echonet_lite.catalog import infer_platform
from homeassistant.components.echonet_lite.const import DOMAIN
from homeassistant.components.echonet_lite.sensor import (
    EchonetLiteSensor,
    EchonetLiteSensorEntityDescription,
)
from homeassistant.components.sensor import (
    DEVICE_CLASS_STATE_CLASSES,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import (
    EPC_GET_PROPERTY_MAP,
    EPC_MANUFACTURER_CODE,
    TEST_MANUFACTURER_CODE,
    TestFrame,
    TestProperty,
    get_temporal_definition,
    make_frame_event,
    make_property_map_edt,
)


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable the sensor platform for these tests."""

    return [Platform.SENSOR]


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_temperature_sensor_state(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Ensure temperature sensors surface decoded values."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010102").hex()
    seoj = bytes.fromhex("001101")
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

    entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "010102-001101-e0_0"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "10.0"
    assert state.attributes["unit_of_measurement"] == UnitOfTemperature.CELSIUS

    entity_entry = entity_registry.async_get(entity_id)
    assert entity_entry is not None
    assert entity_entry.device_id is not None
    device = device_registry.async_get(entity_entry.device_id)
    assert device is not None
    # The device name is resolved from the ``class_XXXX`` translation key on
    # the DeviceInfo. For class 0x0011 this comes out as "Temperature sensor".
    assert device.name == "Temperature sensor"
    assert any(domain == DOMAIN for domain, _ in device.identifiers)


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_nichicon_multiple_input_pcs_f6_energy_sensors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Expose the Nichicon F6 cumulative bought and sold energy fields."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("01010a").hex()
    eoj = EOJ(0x02A501)

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xF6)),
        TestProperty(epc=EPC_MANUFACTURER_CODE, edt=(0x00006C).to_bytes(3, "big")),
        TestProperty(epc=0xF6, edt=bytes.fromhex("0000ed2900002841")),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    for offset, state_value in ((0, "60.713"), (4, "10.305")):
        entity_id = entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"01010a-02a501-f6_{offset}"
        )
        assert entity_id is not None
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == state_value
        assert state.attributes["unit_of_measurement"] == "kWh"
        assert state.attributes["state_class"] == "total_increasing"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_coefficient_epc_is_polled_via_dependent_sensor(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """A coefficient EPC is polled even though it never becomes its own entity.

    EPC 0xC2 ("Unit for cumulative amounts of electric energy") is a MRA
    ``numericValue`` property: ``infer_platform()`` skips it, so it never gets
    its own entity/subscription. Without folding ``coefficient_epcs`` into the
    dependent sensor's (EPC 0xC0) ``_subscribed_epcs``, 0xC2 would never be
    polled and ``NumericCodec.decode()``'s coefficient resolution for 0xC0
    would never see a value.
    """
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010107").hex()
    eoj = EOJ(0x028701)  # Power distribution board metering, instance 1

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xC0, 0xC2)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    device_manager = entry.runtime_data.coordinator.device_manager
    node = device_manager.data[f"{node_id}-{eoj:06x}"]
    assert 0xC0 in device_manager.effective_poll_epcs(node.device_key)
    assert 0xC2 in device_manager.effective_poll_epcs(node.device_key)


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_legacy_measurement_channel_epcs_are_never_polled(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Class 0x0287 EPCs 0xD0-0xEF are excluded even when the device advertises them.

    See EXCLUDED_EPCS_BY_CLASS: the legacy fixed 32-channel layout must never
    enter monitored/fast-poll EPC sets, regardless of what the device's Get
    property map contains.
    """
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010108").hex()
    eoj = EOJ(0x028701)

    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xD0, 0xD1, 0xEF)
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    device_manager = entry.runtime_data.coordinator.device_manager
    node = device_manager.data[f"{node_id}-{eoj:06x}"]
    for epc in (0xD0, 0xD1, 0xEF):
        assert epc not in device_manager.effective_poll_epcs(node.device_key)
        assert epc not in device_manager.effective_fast_poll_epcs(node.device_key)


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_temperature_sensor_immeasurable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure temperature sensors return unknown for special ECHONET values.

    ECHONET Lite defines special values for temperature:
    - 0x7E (126): Immeasurable
    - 0x7F (127): Overflow
    - 0x80 (-128 signed): Underflow
    """
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010106").hex()
    seoj = bytes.fromhex("013001")  # Home air conditioner
    eoj = EOJ(int.from_bytes(seoj, "big"))

    # 0xBE is outdoor temperature with signed_byte_temperature decoder
    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xBE)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xBE, edt=b"\x7e"),  # TEMP_IMMEASURABLE = 0x7E (126)
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "010106-013001-be_0"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    # Should be unknown/unavailable instead of "126"
    assert state.state == "unknown"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_humidity_sensor_updates(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure humidity sensors track subsequent updates."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010103").hex()
    seoj = bytes.fromhex("001201")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    entry.runtime_data.client.get.side_effect = [
        [
            TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xE0)),
            TestProperty(
                epc=EPC_MANUFACTURER_CODE,
                edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
            ),
            TestProperty(epc=0xE0, edt=b"\x32"),
        ],
        [
            TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xE0)),
            TestProperty(
                epc=EPC_MANUFACTURER_CODE,
                edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
            ),
            TestProperty(epc=0xE0, edt=b"\x28"),
        ],
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "010103-001201-e0_0"
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
            TestProperty(epc=0xE0, edt=b"\x28"),
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
    assert state2.state == "40"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_battery_instantaneous_charge_discharge_power(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure battery instantaneous charge/discharge power sensors work."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010104").hex()
    seoj = bytes.fromhex("027d01")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xD3, 0xE4)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xD3, edt=b"\x00\x00\x04\xb0"),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "010104-027d01-d3_0"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "1200"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_battery_remaining_capacity_percent(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure battery remaining capacity percent sensors work."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010105").hex()
    seoj = bytes.fromhex("027d01")
    eoj = EOJ(int.from_bytes(seoj, "big"))

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xD3, 0xE4)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xE4, edt=b"\x50"),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "010105-027d01-e4_0"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "80"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_enum_sensor_decodes_value(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Ensure read-only multi-value enum properties become ENUM sensors."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010107").hex()
    seoj = bytes.fromhex("05ff01")  # Controller (3+ value enum on EPC 0xC6)
    eoj = EOJ(int.from_bytes(seoj, "big"))

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xC6)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xC6, edt=b"\x41"),  # connected
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "010107-05ff01-c6"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "connected"


@pytest.mark.parametrize(
    ("quantity", "value_kind", "unit", "expected_device_class", "expected_state_class"),
    [
        pytest.param(
            Quantity.ENERGY,
            ValueKind.TOTAL,
            "Wh",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL,
            id="total",
        ),
        pytest.param(
            Quantity.ENERGY,
            ValueKind.CUMULATIVE,
            "kWh",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
            id="cumulative",
        ),
        pytest.param(
            Quantity.STORED_ENERGY,
            ValueKind.MEASUREMENT,
            "Wh",
            SensorDeviceClass.ENERGY_STORAGE,
            SensorStateClass.MEASUREMENT,
            id="measurement",
        ),
        pytest.param(
            Quantity.ENERGY,
            None,
            "Wh",
            SensorDeviceClass.ENERGY,
            None,
            id="static_value",
        ),
        pytest.param(
            None,
            ValueKind.MEASUREMENT,
            "degree",
            None,
            SensorStateClass.MEASUREMENT_ANGLE,
            id="angle",
        ),
        pytest.param(
            None,
            None,
            "L",
            None,
            None,
            id="unknown_quantity",
        ),
    ],
)
def test_sensor_classes_follow_pyhems_value_semantics(
    quantity: Quantity | None,
    value_kind: ValueKind | None,
    unit: str,
    expected_device_class: SensorDeviceClass | None,
    expected_state_class: SensorStateClass | None,
) -> None:
    """Device and state classes are mapped from the pyhems value semantics."""
    entity_def = EntityDefinition(
        id="class_0000_epc_a4",
        epc=0xA4,
        name_en="x",
        name_ja="x",
        readable=True,
        writable=False,
        format="uint32",
        unit=unit,
        minimum=0,
        maximum=999999999,
        multiple_of=1.0,
        quantity=quantity,
        value_kind=value_kind,
    )

    description = EchonetLiteSensorEntityDescription.build_from_entity_def(
        0x0000, entity_def
    )

    assert description.device_class is expected_device_class
    assert description.state_class is expected_state_class


def test_all_inferred_sensor_state_classes_are_valid() -> None:
    """All inferred state classes must be valid for their HA device class."""
    invalid_descriptions: list[str] = []

    for class_code, entities in REGISTRY.entities.items():
        for entity_def in entities:
            if infer_platform(entity_def) is not Platform.SENSOR:
                continue

            description = EchonetLiteSensorEntityDescription.build_from_entity_def(
                class_code, entity_def
            )
            if (
                description.device_class is None
                or description.state_class is None
                or (allowed := DEVICE_CLASS_STATE_CLASSES.get(description.device_class))
                is None
                or description.state_class in allowed
            ):
                continue

            invalid_descriptions.append(
                f"class_{class_code:04x} epc_{entity_def.epc:02x}: "
                f"{entity_def.name_en} uses {description.device_class}/"
                f"{description.state_class}"
            )

    assert not invalid_descriptions


# ============================================================================
# Collection (paged list) channel sensors — class 0x0287 branch circuit
# metering.
# ============================================================================

_POWER_DISTRIBUTION_EOJ = EOJ(0x028701)  # Power distribution board metering, instance 1


def _channel_header(start: int, count: int) -> bytes:
    return bytes([start, count])


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_simplex_instantaneous_power_channels_created(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """One sensor entity is created per channel, up to the declared count."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010201").hex()
    eoj = _POWER_DISTRIBUTION_EOJ

    power_edt = _channel_header(1, 2) + struct.pack(">i", 100) + struct.pack(">i", -20)
    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xB1, 0xB7)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xB1, edt=b"\x02"),
        TestProperty(epc=0xB7, edt=power_edt),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    device_key = f"{node_id}-{int(eoj):06x}"

    entity_id_1 = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{device_key}-simplex_channel_1_instantaneous_power"
    )
    entity_id_2 = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{device_key}-simplex_channel_2_instantaneous_power"
    )
    assert entity_id_1 is not None
    assert entity_id_2 is not None

    assert hass.states.get(entity_id_1).state == "100"
    assert hass.states.get(entity_id_2).state == "-20"

    # A third channel (beyond the declared count of 2) is never created.
    entity_id_3 = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{device_key}-simplex_channel_3_instantaneous_power"
    )
    assert entity_id_3 is None


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_simplex_cumulative_energy_resolves_coefficient(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Simplex energy channel values apply the 0xC2 coefficient."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010202").hex()
    eoj = _POWER_DISTRIBUTION_EOJ

    energy_edt = _channel_header(1, 1) + struct.pack(">I", 500)
    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xB1, 0xB3, 0xC2)
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xB1, edt=b"\x01"),
        TestProperty(epc=0xB3, edt=energy_edt),
        TestProperty(epc=0xC2, edt=b"\x02"),  # x0.01
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    device_key = f"{node_id}-{int(eoj):06x}"
    entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{device_key}-simplex_channel_1_cumulative_energy"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "5.0"

    # 0xC2 is polled even though it never becomes its own entity.
    device_manager = entry.runtime_data.coordinator.device_manager
    node = device_manager.data[device_key]
    assert 0xC2 in device_manager.effective_poll_epcs(node.device_key)


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_duplex_cumulative_energy_forward_and_reverse_channels(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Duplex energy channels create separate forward/reverse entities."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010203").hex()
    eoj = _POWER_DISTRIBUTION_EOJ

    energy_edt = (
        _channel_header(1, 1) + struct.pack(">I", 12) + struct.pack(">I", 0xFFFFFFFE)
    )
    entry.runtime_data.client.get.return_value = [
        TestProperty(
            epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xB8, 0xBA, 0xC2)
        ),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xB8, edt=b"\x01"),
        TestProperty(epc=0xBA, edt=energy_edt),
        TestProperty(epc=0xC2, edt=b"\x00"),  # x1
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    device_key = f"{node_id}-{int(eoj):06x}"

    forward_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{device_key}-duplex_channel_1_cumulative_energy_forward"
    )
    reverse_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{device_key}-duplex_channel_1_cumulative_energy_reverse"
    )
    assert forward_id is not None
    assert reverse_id is not None
    assert hass.states.get(forward_id).state == "12.0"
    # reverse direction is "noData" -> unknown
    assert hass.states.get(reverse_id).state == "unknown"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_channel_not_in_current_page_is_unknown(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A channel entity outside the currently-decoded page reports unknown."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010204").hex()
    eoj = _POWER_DISTRIBUTION_EOJ

    # Count says 2 channels, but the returned page only covers channel 1.
    power_edt = _channel_header(1, 1) + struct.pack(">i", 42)
    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xB1, 0xB7)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xB1, edt=b"\x02"),
        TestProperty(epc=0xB7, edt=power_edt),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    device_key = f"{node_id}-{int(eoj):06x}"
    entity_id_1 = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{device_key}-simplex_channel_1_instantaneous_power"
    )
    entity_id_2 = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{device_key}-simplex_channel_2_instantaneous_power"
    )
    assert entity_id_1 is not None
    assert entity_id_2 is not None
    assert hass.states.get(entity_id_1).state == "42"
    assert hass.states.get(entity_id_2).state == "unknown"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_no_channels_created_when_count_epc_missing(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """No collection channel entities are created when the count EPC is unknown."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010205").hex()
    eoj = _POWER_DISTRIBUTION_EOJ

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xB7)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xB7, edt=_channel_header(1, 1) + struct.pack(">i", 1)),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    device_key = f"{node_id}-{int(eoj):06x}"
    assert (
        entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"{device_key}-simplex_channel_1_instantaneous_power"
        )
        is None
    )


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_collection_page_is_decoded_once_per_change(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Channel entities of one list share a page decoded once per value."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator
    node_id = bytes.fromhex("010202").hex()
    eoj = _POWER_DISTRIBUTION_EOJ
    power_edt = _channel_header(1, 2) + struct.pack(">i", 100) + struct.pack(">i", -20)
    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xB1, 0xB7)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xB1, edt=b"\x02"),
        TestProperty(epc=0xB7, edt=power_edt),
    ]
    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()
    device_key = f"{node_id}-{int(eoj):06x}"
    entity_id_2 = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{device_key}-simplex_channel_2_instantaneous_power"
    )
    assert entity_id_2 is not None

    with patch(
        "homeassistant.components.echonet_lite.sensor.decode_collection_page",
        wraps=decode_collection_page,
    ) as decode:
        new_edt = _channel_header(1, 2) + struct.pack(">i", 100) + struct.pack(">i", 30)
        await coordinator.device_manager.process_frame_event(
            make_frame_event(
                TestFrame(
                    tid=2,
                    seoj=eoj.to_bytes(),
                    deoj=bytes.fromhex("05ff01"),
                    esv=ESV.GET_RES,
                    properties=[TestProperty(epc=0xB7, edt=new_edt)],
                ),
                received_at=time.monotonic(),
                node_id=node_id,
                eoj=eoj,
            )
        )
        await hass.async_block_till_done()

    assert decode.call_count == 1
    assert hass.states.get(entity_id_2).state == "30"


@pytest.mark.usefixtures("mock_echonet_lite_client")
@pytest.mark.parametrize(
    ("class_code", "epc", "edt", "expected"),
    [
        (0x03D3, 0xDB, "003b", "59"),
        (0x03D3, 0xDB, "ffff", "unknown"),
    ],
)
async def test_readonly_sensor(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    class_code: int,
    epc: int,
    edt: str,
    expected: str,
) -> None:
    """Readonly definitions use sensors, including unknown for explicit states."""
    entry = init_integration
    entry.runtime_data.client.get.return_value = [
        TestProperty(0x9F, make_property_map_edt(epc)),
        TestProperty(0x8A, b"\x00\x00\x05"),
        TestProperty(epc, bytes.fromhex(edt)),
    ]
    with patch(
        "homeassistant.components.echonet_lite.entity."
        "EchonetLiteDescribedEntity._async_listen_timezone_updates"
    ) as listen_timezone:
        await entry.runtime_data.coordinator.device_manager.setup_device(
            "010201", EOJ((class_code << 8) | 1)
        )
        await hass.async_block_till_done()
    listen_timezone.assert_not_called()
    entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"010201-{class_code:04x}01-{epc:02x}"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == expected


@pytest.mark.parametrize(
    ("class_code", "epc", "subscriptions"),
    [(0x0279, 0xB1, 1), (0x03D3, 0xDB, 0), (0x0011, 0x85, 0)],
)
async def test_sensor_timezone_subscription(
    init_integration: MockConfigEntry,
    temporal_node: NodeState,
    class_code: int,
    epc: int,
    subscriptions: int,
) -> None:
    """Only local datetime sensors subscribe to timezone changes."""
    description = EchonetLiteSensorEntityDescription.build_from_entity_def(
        class_code, get_temporal_definition(class_code, epc)
    )
    entity = EchonetLiteSensor(
        init_integration.runtime_data.coordinator, temporal_node, description
    )
    with (
        patch(
            "homeassistant.components.echonet_lite.entity."
            "EchonetLiteDescribedEntity.async_added_to_hass"
        ),
        patch.object(entity, "_async_listen_timezone_updates") as listen_timezone,
    ):
        await entity.async_added_to_hass()
    assert listen_timezone.call_count == subscriptions


async def test_node_manufacture_date_is_not_exposed(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Node manufacture dates create neither sensors nor profile GET requests."""
    entry = init_integration
    mock_echonet_lite_client.get.return_value = [
        TestProperty(0x9F, make_property_map_edt(0x8E)),
        TestProperty(0x8A, b"\x00\x00\x05"),
        TestProperty(0x8E, bytes.fromhex("07e8021d")),
    ]
    await entry.runtime_data.coordinator.device_manager.setup_device(
        "010201", EOJ(0x013001)
    )
    await entry.runtime_data.coordinator.device_manager.setup_device(
        "010201", EOJ(0x029001)
    )
    await hass.async_block_till_done()
    profile_entries = [
        entity
        for entity in entity_registry.entities.values()
        if entity.unique_id == "010201-0ef001-8e_0"
    ]
    assert not profile_entries
    assert all(
        call.args[1] != EOJ(0x0EF001)
        for call in mock_echonet_lite_client.get.call_args_list
    )
