"""Tests for the ECHONET Lite coordinator."""

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

from pyhems import EOJ, ESV, DeviceManager, HemsClient

from homeassistant.components.echonet_lite.const import DOMAIN
from homeassistant.components.echonet_lite.coordinator import EchonetLiteCoordinator
from homeassistant.components.echonet_lite.runtime import RuntimeHealth
from homeassistant.core import HomeAssistant

from .conftest import make_frame_event

from tests.common import MockConfigEntry


@dataclass(slots=True)
class FrameProperty:
    """Minimal representation of an ECHONET Lite property."""

    epc: int
    edt: bytes


@dataclass(slots=True)
class FrameMessage:
    """Minimal representation of an ECHONET Lite frame."""

    tid: int
    seoj: bytes
    deoj: bytes
    esv: ESV
    properties: list[FrameProperty]

    def is_response_frame(self) -> bool:
        """Check if frame is a response (success or failure)."""
        return (0x70 <= self.esv <= 0x7F) or (0x50 <= self.esv <= 0x5F)


def _make_coordinator(
    hass: HomeAssistant,
    client: AsyncMock,
    monitored_epcs: dict | None = None,
    class_code_filter: frozenset[int] | None = None,
) -> tuple[EchonetLiteCoordinator, DeviceManager]:
    """Create a coordinator and device manager pair for testing."""
    client.setup_get = AsyncMock(side_effect=client.get)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    dm = DeviceManager(
        client=client,
        monitored_epcs=monitored_epcs or {},
        class_code_filter=class_code_filter,
    )
    coordinator = EchonetLiteCoordinator(
        hass,
        config_entry=entry,
        device_manager=dm,
        health=RuntimeHealth(),
    )
    return coordinator, dm


async def test_process_frame_registers_node(hass: HomeAssistant) -> None:
    """Ensure frames with property maps populate the coordinator data snapshot."""

    client = AsyncMock(spec=HemsClient)
    # Property map format: count byte + EPC list
    get_property_map = bytes([3, 0x80, 0x8A, 0xE0])  # 3 EPCs: 0x80, 0x8A, 0xE0
    client.get.return_value = [
        FrameProperty(epc=0x9F, edt=get_property_map),
        FrameProperty(epc=0xE0, edt=b"\x00d"),
        FrameProperty(epc=0x8A, edt=b"\x00\x00\x01"),
    ]
    coordinator, dm = _make_coordinator(hass, client)

    # Wire device_added callback
    def _on_added(device_key: str) -> None:
        coordinator.data = dict(dm.data)

    dm.on_device_added(_on_added)

    node_hex = bytes.fromhex("010203").hex()
    eoj = EOJ(0x001101)

    with patch(
        "pyhems.device_manager.time.monotonic",
        return_value=10.0,
    ):
        await dm.setup_device(node_hex, eoj)

    # Stable ID: uid(hex)-eoj(hex) (0x83 + EOJ preferred)
    node_id = f"{node_hex}-{int(eoj):06x}"
    node = coordinator.data[node_id]
    assert node.eoj == eoj
    assert node.properties[0xE0] == b"\x00d"

    # Frames update existing nodes
    frame2 = FrameMessage(
        tid=2,
        seoj=int(eoj).to_bytes(3, "big"),
        deoj=bytes.fromhex("0ef001"),
        esv=ESV.INF,
        properties=[FrameProperty(epc=0xE0, edt=b"\x00e")],
    )

    await coordinator.device_manager.process_frame_event(
        make_frame_event(
            frame2,
            received_at=11.0,
            node_id=node_hex,
            eoj=eoj,
        )
    )
    node2 = coordinator.data[node_id]
    node2 = coordinator.data[node_id]
    assert node2.properties[0xE0] == b"\x00e"


async def test_set_response_does_not_overwrite_properties(hass: HomeAssistant) -> None:
    """Ensure Set responses (0x71) do not clobber stored property values."""

    client = AsyncMock(spec=HemsClient)

    get_property_map = bytes([1, 0xB0])
    client.get.return_value = [
        FrameProperty(epc=0x9F, edt=get_property_map),
        FrameProperty(epc=0xB0, edt=b"E"),
        FrameProperty(epc=0x8A, edt=b"\x00\x00\x01"),
    ]

    coordinator, dm = _make_coordinator(
        hass, client, monitored_epcs={0x0011: frozenset({0xB0})}
    )

    def _on_added(device_key: str) -> None:
        coordinator.data = dict(dm.data)

    dm.on_device_added(_on_added)

    node_hex = bytes.fromhex("010203").hex()
    eoj = EOJ(0x001101)

    with patch(
        "pyhems.device_manager.time.monotonic",
        return_value=10.0,
    ):
        await dm.setup_device(node_hex, eoj)

    device_key = f"{node_hex}-{int(eoj):06x}"
    assert coordinator.data[device_key].properties[0xB0] == b"E"

    # Simulate a successful Set_Res for EPC 0xB0: empty EDT is an acknowledgement
    # and must not be interpreted as a new value.
    frame = FrameMessage(
        tid=4,
        seoj=int(eoj).to_bytes(3, "big"),
        deoj=bytes.fromhex("0ef001"),
        esv=ESV.SET_RES,
        properties=[FrameProperty(epc=0xB0, edt=b"")],
    )

    await coordinator.device_manager.process_frame_event(
        make_frame_event(
            frame,
            received_at=11.0,
            node_id=node_hex,
            eoj=eoj,
        )
    )

    assert coordinator.data[device_key].properties[0xB0] == b"E"


async def test_process_frame_registers_instance_list(hass: HomeAssistant) -> None:
    """Ensure EPC 0xD6 instance lists trigger identification.

    (but don't create nodes directly).
    """

    client = AsyncMock()
    coordinator, _dm = _make_coordinator(hass, client)

    frame = FrameMessage(
        tid=3,
        seoj=bytes.fromhex("0ef001"),
        deoj=bytes.fromhex("05ff01"),
        esv=ESV.GET_RES,
        properties=[
            FrameProperty(epc=0xD6, edt=bytes.fromhex("02001101013001")),
        ],
    )

    await coordinator.device_manager.process_frame_event(
        make_frame_event(
            frame,
            received_at=20.0,
            node_id="",
            eoj=EOJ(int.from_bytes(frame.seoj, "big")),
        )
    )

    # Nodes should NOT be created yet because we haven't received identification
    assert "001101" not in coordinator.data
    assert "013001" not in coordinator.data
