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


async def _setup_vendor_node(entry: MockConfigEntry, *get_epcs: int) -> AsyncMock:
    """Set up a node advertising vendor EPCs and return the mocked client."""
    coordinator = entry.runtime_data.coordinator
    client: AsyncMock = entry.runtime_data.client
    client.get.return_value = [
        TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(*get_epcs)),
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
    ]
    await coordinator.device_manager.setup_device(
        bytes.fromhex("010102").hex(), EOJ(0x001101)
    )
    client.get.reset_mock()
    return client


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_config_entry_diagnostics_reads_vendor_epcs(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """Vendor-specific EPCs are read live and listed with the properties."""
    entry = init_integration
    client = await _setup_vendor_node(entry, 0xE0, 0xF0, 0xF1, 0xF2)
    client.get.return_value = [
        TestProperty(epc=0xF0, edt=b"\x01\x02"),
        TestProperty(epc=0xF1, edt=b""),
        TestProperty(epc=0xF2, edt=b"\xff"),
    ]

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    client.get.assert_awaited_once_with(
        "010102", EOJ(0x001101), [0xF0, 0xF1, 0xF2], request_timeout=5.0
    )
    properties = diagnostics["devices"][0]["properties"]
    assert properties["0xF0"] == "01 02"
    assert "0xF1" not in properties
    assert properties["0xF2"] == "ff"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_device_diagnostics_reads_vendor_epcs(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Device diagnostics read vendor-specific EPCs live."""
    entry = init_integration
    client = await _setup_vendor_node(entry, 0xE0, 0xF0)
    client.get.return_value = [TestProperty(epc=0xF0, edt=b"\xab")]
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "010102-001101")},
    )

    diagnostics = await get_diagnostics_for_device(hass, hass_client, entry, device)

    client.get.assert_awaited_once()
    assert diagnostics["properties"]["0xF0"] == "ab"


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_diagnostics_skip_read_without_vendor_epcs(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """No request is sent when the node advertises no vendor-specific EPCs."""
    entry = init_integration
    client = await _setup_vendor_node(entry, 0xE0)

    await get_diagnostics_for_config_entry(hass, hass_client, entry)

    client.get.assert_not_awaited()


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_diagnostics_vendor_read_failure_falls_back_to_cache(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """An unanswered read does not fail diagnostics."""
    entry = init_integration
    client = await _setup_vendor_node(entry, 0xE0, 0xF0)
    client.get.return_value = [TestProperty(epc=0xF0, edt=b"")]

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert "0xF0" not in diagnostics["devices"][0]["properties"]


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_diagnostics_vendor_read_total_timeout(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """Diagnostics are still returned when the total read time is exceeded."""
    entry = init_integration
    client = await _setup_vendor_node(entry, 0xE0, 0xF0)

    # Frozen time keeps ``asyncio.timeout`` from expiring, so raise directly.
    client.get.side_effect = TimeoutError

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["runtime"]["device_count"] == 1
    assert "0xF0" not in diagnostics["devices"][0]["properties"]


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_diagnostics_reports_decode_errors(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """Diagnostics report how many received datagrams could not be decoded."""
    entry = init_integration
    entry.runtime_data.client.decode_error_count = 3

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["runtime"]["health"]["decode_error_count"] == 3
