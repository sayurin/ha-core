"""Tests for the ECHONET Lite diagnostics platform."""

from unittest.mock import AsyncMock

from pyhems import EOJ
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.echonet_lite.const import (
    CONF_INTERFACE,
    DEFAULT_INTERFACE,
    DOMAIN,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import (
    EPC_GET_PROPERTY_MAP,
    EPC_MANUFACTURER_CODE,
    EPC_PRODUCT_CODE,
    EPC_SERIAL_NUMBER,
    TEST_MANUFACTURER_CODE,
    TestProperty,
    make_property_map_edt,
)

from tests.common import MockConfigEntry
from tests.components.diagnostics import (
    get_diagnostics_for_config_entry,
    get_diagnostics_for_device,
)
from tests.typing import ClientSessionGenerator

FIXED_MOCK_ENTRY_ID = "01JGFJJZ008DNE3BKJ7ZE14YFE"

pytestmark = pytest.mark.freeze_time("2025-01-01T00:00:00+00:00")


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable sensor platform for diagnostics tests."""
    return [Platform.SENSOR]


@pytest.fixture(name="mock_config_entry")
def mock_config_entry_fixture() -> MockConfigEntry:
    """Return a config entry with a fixed entry_id for deterministic snapshots."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_INTERFACE: DEFAULT_INTERFACE},
        entry_id=FIXED_MOCK_ENTRY_ID,
    )


async def _setup_test_node(entry: MockConfigEntry) -> str:
    """Create one discovered node for diagnostics tests."""
    coordinator = entry.runtime_data.coordinator
    client: AsyncMock = entry.runtime_data.client
    client.get_observed_batch_capacity.return_value = 4

    node_id = bytes.fromhex("010102").hex()
    eoj = EOJ(0x001101)

    client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xE0)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_PRODUCT_CODE, edt=b"ECHONETMODEL  "),
        TestProperty(epc=EPC_SERIAL_NUMBER, edt=b"SERIAL1234  "),
        TestProperty(epc=0xE0, edt=b"\x00d"),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    return node_id


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_config_entry_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test config entry diagnostics payload and redaction."""
    entry = init_integration
    await _setup_test_node(entry)
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["devices"][0]["observed_batch_capacity"] == 4
    assert diagnostics == snapshot


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_device_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    snapshot: SnapshotAssertion,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test device diagnostics payload and redaction."""
    entry = init_integration
    await _setup_test_node(entry)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "010102-001101-e0_0"
    )
    assert entity_id is not None
    entity_entry = entity_registry.async_get(entity_id)
    assert entity_entry is not None
    assert entity_entry.device_id is not None

    device = device_registry.async_get(entity_entry.device_id)
    assert device is not None

    diagnostics = await get_diagnostics_for_device(hass, hass_client, entry, device)

    assert diagnostics == snapshot


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_device_diagnostics_missing_identifier(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    device_registry: dr.DeviceRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """Return an error payload when the device has no ECHONET Lite identifier."""
    entry = init_integration
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("other_domain", "some-id")},
    )

    diagnostics = await get_diagnostics_for_device(hass, hass_client, entry, device)

    assert diagnostics == {
        "error": "device_not_found",
        "reason": "missing_identifier",
    }


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_device_diagnostics_for_missing_node(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    device_registry: dr.DeviceRegistry,
    init_integration: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Return a not-available payload when node state is missing."""
    entry = init_integration
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "missing-001101")},
    )

    diagnostics = await get_diagnostics_for_device(hass, hass_client, entry, device)

    assert diagnostics == snapshot


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_config_entry_diagnostics_reports_collection_pages(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """Diagnostics report per-EPC collection page decode status and suppressed EPCs."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator
    client: AsyncMock = entry.runtime_data.client

    node_id = bytes.fromhex("010107").hex()
    eoj = EOJ(0x028701)

    client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xB1, 0xB7)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=0xB1, edt=b"\x01"),
        TestProperty(
            epc=0xB7, edt=bytes([1, 1]) + (100).to_bytes(4, "big", signed=True)
        ),
    ]
    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    device = next(d for d in diagnostics["devices"] if d["class_code"] == "0x0287")
    assert device["collection"]["suppressed_epcs"] == " ".join(
        f"{epc:02X}" for epc in range(0xD0, 0xF0)
    )
    assert device["collection"]["pages"]["0xB7"] == {
        "ok": True,
        "start": 1,
        "count": 1,
    }
    # 0xBE was never advertised/received for this node.
    assert device["collection"]["pages"]["0xBE"] == {
        "ok": False,
        "start": None,
        "count": None,
    }
