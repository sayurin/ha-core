"""Common fixtures for the ECHONET Lite integration tests."""

from collections.abc import Callable, Generator
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from pyhems import (
    CONTROLLER_INSTANCE,
    EOJ,
    ESV,
    REGISTRY,
    EntityDefinition,
    Frame,
    GetAbortedError,
    HemsClient,
    HemsFrameEvent,
    InfRequestResult,
    NodeState,
    Property,
    RequestPriority,
    RuntimeEvent,
    SetRequestResult,
)
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.echonet_lite.const import (
    CONF_INTERFACE,
    DEFAULT_INTERFACE,
    DOMAIN,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant


@dataclass(slots=True)
class TestProperty:
    """Minimal representation of an ECHONET Lite property for tests."""

    __test__ = False
    epc: int
    edt: bytes


@dataclass(slots=True)
class TestFrame:
    """Minimal representation of an ECHONET Lite frame for tests."""

    __test__ = False
    tid: int
    seoj: bytes
    deoj: bytes
    esv: ESV
    properties: list[TestProperty]

    def is_response_or_notification(self) -> bool:
        """Check if frame is a response (success or failure)."""
        return (0x70 <= self.esv <= 0x7F) or (0x50 <= self.esv <= 0x5F)


# Default manufacturer code for tests (arbitrary but consistent)
TEST_MANUFACTURER_CODE = 0x000005  # Sharp

# Common node profile EPCs used by the test fixtures.
EPC_GET_PROPERTY_MAP = 0x9F
EPC_INF_PROPERTY_MAP = 0x9D
EPC_MANUFACTURER_CODE = 0x8A
EPC_PRODUCT_CODE = 0x8C
EPC_SERIAL_NUMBER = 0x8D
EPC_SET_PROPERTY_MAP = 0x9E


def make_frame_event(
    frame,
    *,
    node_id: str,
    eoj: EOJ,
    received_at: float = 0.0,
) -> HemsFrameEvent:
    """Create a HemsFrameEvent for testing.

    All fields except received_at are required to match pyhems type guarantees.
    """
    return HemsFrameEvent(
        received_at=received_at,
        frame=frame,
        node_id=node_id,
        eoj=eoj,
    )


def make_property_map_edt(*epcs: int) -> bytes:
    """Create EDT bytes for a property map (0x9D/0x9E/0x9F).

    For property maps with 15 or fewer EPCs, the format is:
    - 1 byte: number of EPCs
    - N bytes: EPC values

    For property maps with 16+ EPCs, a bitmap format is used (not implemented here).
    """
    count = len(epcs)
    if count <= 15:
        return bytes([count, *epcs])

    # Bitmap format for 16+ EPCs covering EPCs 0x80-0xFF
    bitmap = [0] * 16
    for epc in epcs:
        if not 0x80 <= epc <= 0xFF:
            raise ValueError("EPC out of bitmap range (0x80-0xFF)")
        low = epc & 0x0F
        high = (epc >> 4) - 8  # 0 for 0x8x, 7 for 0xFx
        bitmap[low] |= 1 << high
    return bytes([count, *bitmap])


@pytest.fixture
def mock_async_validate_network() -> Generator[AsyncMock]:
    """Patch the interface check to avoid I/O during flows."""
    with patch(
        "homeassistant.components.echonet_lite.config_flow.check_interface",
        AsyncMock(return_value=None),
    ) as mock_check_interface:
        yield mock_check_interface


@pytest.fixture
def mock_echonet_lite_client() -> Generator[AsyncMock]:
    """Patch the runtime client used by the integration."""

    client = AsyncMock(spec=HemsClient)
    client.start = AsyncMock()
    client.stop = AsyncMock()
    client.get = AsyncMock(return_value=[])

    # The manufacturer code is read before the other setup EPCs; both reads
    # share one scripted ``client.get`` response, as before the split.
    manufacturer_reads: dict[tuple[str, EOJ], list[TestProperty]] = {}

    async def _setup_get(
        node_id: str, deoj: EOJ, epcs: list[int]
    ) -> list[TestProperty]:
        properties = manufacturer_reads.pop((node_id, deoj), None)
        if properties is None:
            properties = list(await client.get(node_id, deoj, epcs))
        returned_epcs = {prop.epc for prop in properties}
        # Devices default to valid, empty INF/SET maps unless overridden.
        properties.extend(
            TestProperty(epc=epc, edt=b"\x00")
            for epc in (EPC_INF_PROPERTY_MAP, EPC_SET_PROPERTY_MAP)
            if epc not in returned_epcs
        )
        return properties

    default_setup_get = client.setup_get = AsyncMock(side_effect=_setup_get)

    async def _read_manufacturer_code(node_id: str, deoj: EOJ) -> int:
        if client.setup_get is default_setup_get:
            properties = list(await client.get(node_id, deoj, [EPC_MANUFACTURER_CODE]))
            manufacturer_reads[node_id, deoj] = properties
        else:
            properties = list(
                await client.setup_get(node_id, deoj, [EPC_MANUFACTURER_CODE])
            )
        for prop in properties:
            if prop.epc == EPC_MANUFACTURER_CODE and len(prop.edt) >= 3:
                return int.from_bytes(prop.edt[:3], "big")
        manufacturer_reads.pop((node_id, deoj), None)
        raise GetAbortedError("Could not read manufacturer code (EPC 0x8A)")

    client.read_manufacturer_code = AsyncMock(side_effect=_read_manufacturer_code)
    client.request_get = AsyncMock(return_value=None)
    client.async_request = AsyncMock()
    client.send = MagicMock(return_value=True)
    client.probe_initial_nodes = MagicMock(return_value=True)
    client.probe_nodes = MagicMock(return_value=True)
    client.get_observed_batch_capacity = MagicMock(return_value=None)
    client.decode_error_count = 0

    async def _set_properties(
        *,
        node_id,
        deoj,
        properties,
        seoj=CONTROLLER_INSTANCE,
        request_timeout: float = 5.0,
        priority: RequestPriority = RequestPriority.SET,
    ) -> SetRequestResult:
        frame = Frame(seoj=seoj, deoj=deoj, esv=ESV.SETC, properties=properties)
        sent = client.send(node_id, frame)
        return SetRequestResult(
            sent=sent,
            response_esv=ESV.SET_RES if sent else None,
            accepted_epcs=frozenset(prop.epc for prop in properties)
            if sent
            else frozenset(),
            rejected_epcs=frozenset(),
            unanswered_epcs=frozenset()
            if sent
            else frozenset(prop.epc for prop in properties),
        )

    async def _set_property(
        *, node_id, deoj, epc, edt, seoj=CONTROLLER_INSTANCE
    ) -> SetRequestResult:
        return await _set_properties(
            node_id=node_id,
            deoj=deoj,
            properties=[Property(epc=epc, edt=edt)],
            seoj=seoj,
        )

    client.set_property = AsyncMock(side_effect=_set_property)
    client.set_properties = AsyncMock(side_effect=_set_properties)

    async def _async_request_notifications(
        node_id, deoj, epcs, seoj=CONTROLLER_INSTANCE, request_timeout=5.0
    ):
        frame = Frame(
            seoj=seoj,
            deoj=deoj,
            esv=ESV.INF_REQ,
            properties=[Property(epc=epc) for epc in epcs],
        )
        client.send(node_id, frame)
        # Simulate every requested EPC subscribing successfully by default;
        # tests that need to exercise partial success/failure override
        # ``client.request_notifications`` directly.
        return InfRequestResult(
            notified_epcs=frozenset(epcs),
            rejected_epcs=frozenset(),
            unanswered_epcs=frozenset(),
        )

    client.request_notifications = AsyncMock(side_effect=_async_request_notifications)

    callbacks: list[Callable[[RuntimeEvent], None]] = []

    def _subscribe(callback: Callable[[RuntimeEvent], None]) -> Callable[[], None]:
        callbacks.append(callback)

        async def _async_callback(event: RuntimeEvent) -> None:
            for subscribed_callback in callbacks.copy():
                subscribed_callback(event)

        client._listener = AsyncMock(side_effect=_async_callback)

        def _unsubscribe() -> None:
            if callback in callbacks:
                callbacks.remove(callback)

        return _unsubscribe

    client.subscribe = Mock(side_effect=_subscribe)

    with patch("homeassistant.components.echonet_lite.HemsClient", return_value=client):
        yield client


@pytest.fixture
def mock_definitions_registry() -> None:
    """Ensure the definitions registry is available for tests.

    Definitions are provided by pyhems as the pre-built ``REGISTRY`` constant,
    so no mocking is required.
    """


@pytest.fixture
def platforms() -> list[Platform]:
    """Return the list of platforms to set up for the integration."""

    return []


@pytest.fixture(autouse=True)
def patch_platforms(platforms: list[Platform]) -> Generator[None]:
    """Patch the PLATFORMS constant for each test."""

    with patch("homeassistant.components.echonet_lite.PLATFORMS", platforms):
        yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a default mocked config entry for the integration.

    Device classes are not filtered so non-stable device classes can be tested.
    """
    return MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: DEFAULT_INTERFACE})


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    mock_definitions_registry: None,
    platforms: list[Platform],
) -> MockConfigEntry:
    """Set up the integration in Home Assistant."""

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


def get_temporal_definition(class_code: int, epc: int) -> EntityDefinition:
    """Return a generic temporal entity definition."""
    return next(entity for entity in REGISTRY.entities[class_code] if entity.epc == epc)


@pytest.fixture
def temporal_node() -> NodeState:
    """A device with local values but no area or geographical zone."""
    return NodeState(
        eoj=EOJ(0x013001),
        node_id="abc",
        properties={
            0x97: b"\x08\x1e",
            0x98: bytes.fromhex("07ea0b03"),
            0x92: b"\xff\x3b",
            0xCD: bytes.fromhex("07ea0b030a2d"),
        },
        manufacturer_code=0,
        get_epcs=frozenset({0x97, 0x98, 0x92, 0xCD}),
        set_epcs=frozenset({0x97, 0x98, 0x92, 0xCD}),
        inf_epcs=frozenset(),
        poll_epcs=frozenset(),
        fast_poll_epcs=frozenset(),
        product_code=None,
        serial_number=None,
    )
