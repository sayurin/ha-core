"""Tests for the ECHONET Lite initialization helpers."""

import time
from unittest.mock import AsyncMock, patch

from pyhems import EOJ, ESV, GetAbortedError, HemsInstanceListEvent
import pytest

from homeassistant.components.echonet_lite import (
    _FAST_POLL_EPCS,
    _MONITORED_EPCS,
    async_remove_config_entry_device,
)
from homeassistant.components.echonet_lite.const import CONF_INTERFACE, DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import TestFrame, TestProperty, make_frame_event, make_property_map_edt

from tests.common import MockConfigEntry


@pytest.mark.parametrize("failure", ["missing_map", "empty_map", "timeout"])
@pytest.mark.parametrize("platforms", [[Platform.SENSOR]])
async def test_incomplete_device_setup_is_retried_on_discovery(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    failure: str,
) -> None:
    """Incomplete setup does not create entities and can recover on discovery."""
    entry = init_integration
    client = entry.runtime_data.client
    manager = entry.runtime_data.coordinator.device_manager
    node_id = "010102"
    eoj = EOJ(0x001101)
    properties = [
        TestProperty(epc=0x8A, edt=b"\x00\x00\x01"),
        TestProperty(epc=0x9E, edt=b"\x00"),
        TestProperty(epc=0x9F, edt=make_property_map_edt(0xE0)),
        TestProperty(epc=0xE0, edt=b"\x00d"),
    ]
    responses = {
        "missing_map": AsyncMock(return_value=properties),
        "empty_map": AsyncMock(
            return_value=[*properties, TestProperty(epc=0x9D, edt=b"")]
        ),
        "timeout": AsyncMock(side_effect=GetAbortedError("no response")),
    }
    client.setup_get = responses[failure]
    event = HemsInstanceListEvent(
        received_at=time.monotonic(),
        instances=[eoj],
        node_id=node_id,
        properties={},
    )

    assert await manager.process_instance_list_event(event) == []
    await hass.async_block_till_done()
    assert not manager.data
    assert (
        entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"{node_id}-{eoj:06x}-e0_0"
        )
        is None
    )

    client.setup_get = AsyncMock(
        return_value=[*properties, TestProperty(epc=0x9D, edt=b"\x00")]
    )
    assert await manager.process_instance_list_event(event) == [f"{node_id}-{eoj:06x}"]
    await hass.async_block_till_done()
    assert manager.data[f"{node_id}-{eoj:06x}"].properties[0xE0] == b"\x00d"
    assert (
        entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"{node_id}-{eoj:06x}-e0_0"
        )
        is not None
    )


async def test_migrate_entry_v1_0_to_v1_1(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Test migration from version 1.0 to 1.1 moves interface to data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={CONF_INTERFACE: "192.168.1.50"},
        version=1,
        minor_version=0,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.data == {CONF_INTERFACE: "192.168.1.50"}
    assert entry.minor_version == 1


async def test_migrate_entry_v1_1_is_noop(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Test migration is a no-op for already-migrated entries."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_INTERFACE: "192.168.1.50"},
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.data == {CONF_INTERFACE: "192.168.1.50"}


async def test_async_setup_and_unload_entry(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Test runtime data is stored and platforms unload cleanly."""

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "192.168.1.100"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data is not None
    assert entry.runtime_data.coordinator is not None
    assert entry.runtime_data.client is mock_echonet_lite_client
    assert entry.runtime_data.property_poller is not None
    mock_echonet_lite_client.start.assert_awaited_once()

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    mock_echonet_lite_client.stop.assert_awaited_once()


async def test_property_poller_requests_polled_properties(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Ensure the property poller requests EPC values for new nodes."""

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    poller = entry.runtime_data.property_poller

    now = time.monotonic()
    node_id = bytes.fromhex("00000000000002").hex()
    node_profile_eoj = EOJ(0x0EF001)
    sensor_eoj = EOJ(0x001101)

    def _async_get(
        node_id_arg: str, eoj_arg: int, _epcs: list[int]
    ) -> list[TestProperty]:
        assert node_id_arg == node_id
        if eoj_arg == node_profile_eoj:
            return [
                TestProperty(epc=0x9F, edt=bytes.fromhex("018A")),
                TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
                TestProperty(epc=0xD6, edt=bytes.fromhex("01001101")),
            ]
        if eoj_arg == sensor_eoj:
            return [
                TestProperty(epc=0x9F, edt=bytes.fromhex("02E08A")),
                TestProperty(epc=0x9D, edt=bytes.fromhex("00")),
                TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
                TestProperty(epc=0xE0, edt=b"\x00d"),
            ]
        raise AssertionError(f"Unexpected EOJ {eoj_arg:06x}")

    mock_echonet_lite_client.get.side_effect = _async_get

    instance_event = HemsInstanceListEvent(
        received_at=now,
        instances=[node_profile_eoj, sensor_eoj],
        node_id=node_id,
        properties={},
    )

    await coordinator.device_manager.process_instance_list_event(instance_event)
    await hass.async_block_till_done()

    # Verify nodes were created
    assert len(coordinator.data) == 2

    # The sensor node now has property maps; trigger a poll cycle manually
    prev_count = mock_echonet_lite_client.send.call_count
    poller._schedule_polls()
    await hass.async_block_till_done()
    assert mock_echonet_lite_client.send.call_count > prev_count

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_collection_range_recovery_batches_unset_ranges(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Unset 0x0287 collection ranges are initialized in one SetC request."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    node_id = bytes.fromhex("00000000000002").hex()
    eoj = EOJ(0x028701)
    get_epcs = (0x8A, 0xB1, 0xB3, 0xB7, 0xB8, 0xBA, 0xBE)
    mock_echonet_lite_client.get.return_value = [
        TestProperty(epc=0x9D, edt=make_property_map_edt()),
        TestProperty(epc=0x9E, edt=make_property_map_edt()),
        TestProperty(epc=0x9F, edt=make_property_map_edt(*get_epcs)),
        TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
        TestProperty(epc=0xB1, edt=b"\x1c"),
        TestProperty(epc=0xB3, edt=bytes.fromhex("fd fd ff ff ff fe")),
        TestProperty(epc=0xB7, edt=bytes.fromhex("fd fd 7f ff ff fe")),
        TestProperty(epc=0xB8, edt=b"\x07"),
        TestProperty(
            epc=0xBA,
            edt=bytes.fromhex("fd fd ff ff ff fe ff ff ff fe"),
        ),
        TestProperty(epc=0xBE, edt=bytes.fromhex("fd fd 7f ff ff fe")),
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)

    set_frames = [
        call.args[1]
        for call in mock_echonet_lite_client.send.call_args_list
        if call.args[1].esv == ESV.SETC
    ]
    assert len(set_frames) == 1
    assert [(prop.epc, prop.edt) for prop in set_frames[0].properties] == [
        (0xB2, b"\x01\x1c"),
        (0xB6, b"\x01\x1c"),
        (0xB9, b"\x01\x07"),
        (0xBD, b"\x01\x07"),
    ]

    await hass.config_entries.async_unload(entry.entry_id)


async def test_property_poller_falls_back_to_poll_when_notifications_disabled(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Confirm the poller falls back to 0x62 when notifications are not supported."""

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    poller = entry.runtime_data.property_poller
    node_id = bytes.fromhex("00000000000002").hex()
    node_profile_eoj = EOJ(0x0EF001)
    sensor_eoj = EOJ(0x001101)

    def _async_get(
        node_id_arg: str, eoj_arg: int, _epcs: list[int]
    ) -> list[TestProperty]:
        assert node_id_arg == node_id
        if eoj_arg == node_profile_eoj:
            return [
                TestProperty(epc=0x9F, edt=bytes.fromhex("01D6")),
                TestProperty(epc=0xD6, edt=bytes.fromhex("01001101")),
                TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
            ]
        if eoj_arg == sensor_eoj:
            return [
                TestProperty(epc=0x9F, edt=bytes.fromhex("02E08A")),
                TestProperty(epc=0x9D, edt=bytes.fromhex("00")),
                TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
                TestProperty(epc=0xE0, edt=b"\x00d"),
            ]
        raise AssertionError(f"Unexpected EOJ {eoj_arg:06x}")

    mock_echonet_lite_client.get.side_effect = _async_get

    instance_event = HemsInstanceListEvent(
        received_at=30.0,
        instances=[node_profile_eoj, sensor_eoj],
        node_id=node_id,
        properties={},
    )

    await coordinator.device_manager.process_instance_list_event(instance_event)
    await hass.async_block_till_done()

    assert len(coordinator.data) == 2

    # With notifications disabled, poller should still poll via 0x62
    poller._schedule_polls()
    await hass.async_block_till_done()

    assert any(
        call.args[1].esv == 0x62
        and any(prop.epc == 0xE0 for prop in call.args[1].properties)
        for call in mock_echonet_lite_client.send.call_args_list
    )


async def test_node_created_with_property_maps(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test that nodes are created when property maps are received.

    This integration-level test ensures nodes are created when property maps are
    received, and subsequent frames update the existing node correctly.
    """

    entry = init_integration
    assert entry is not None

    coordinator = entry.runtime_data.coordinator
    node_hex = bytes.fromhex("00000000000001").hex()
    node_profile_eoj = EOJ(0x0EF001)
    sensor_eoj = EOJ(0x001101)

    def _async_get(
        node_id_arg: str, eoj_arg: int, _epcs: list[int]
    ) -> list[TestProperty]:
        assert node_id_arg == node_hex
        if eoj_arg == node_profile_eoj:
            return [
                TestProperty(epc=0x9F, edt=bytes.fromhex("028AD6")),
                TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
                TestProperty(epc=0xD6, edt=bytes.fromhex("01001101")),
            ]
        if eoj_arg == sensor_eoj:
            return [
                TestProperty(epc=0x9F, edt=bytes.fromhex("018A")),
                TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
            ]
        raise AssertionError(f"Unexpected EOJ {eoj_arg:06x}")

    entry.runtime_data.client.get.side_effect = _async_get

    with patch(
        "pyhems.device_manager.time.monotonic",
        side_effect=[30.0, 31.0],
    ):
        await coordinator.device_manager.setup_device(node_hex, node_profile_eoj)
        await coordinator.device_manager.setup_device(node_hex, sensor_eoj)

    # Parent node should be created
    parent_id = f"{node_hex}-{int(node_profile_eoj):06x}"
    assert parent_id in coordinator.data

    # Instance node should be created
    node_id = f"{node_hex}-{int(sensor_eoj):06x}"
    assert node_id in coordinator.data

    # Verify node state properties
    node_state = coordinator.data[node_id]
    assert node_state.get_epcs == frozenset({0x8A})  # From get map


async def test_node_updated_by_subsequent_frames(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test that existing nodes are updated by subsequent frames.

    This integration-level test ensures that when a node is created with property maps,
    subsequent frames update the existing node state correctly.
    """

    entry = init_integration
    assert entry is not None

    coordinator = entry.runtime_data.coordinator
    node_hex = bytes.fromhex("00000000000001").hex()
    node_profile_eoj = EOJ(0x0EF001)

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=0x9F, edt=bytes.fromhex("028AD6")),
        TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
    ]

    with patch(
        "pyhems.device_manager.time.monotonic",
        return_value=30.0,
    ):
        await coordinator.device_manager.setup_device(node_hex, node_profile_eoj)

    # Node should be created
    node_id = f"{node_hex}-{int(node_profile_eoj):06x}"
    assert node_id in coordinator.data
    node_state = coordinator.data[node_id]
    assert node_state.manufacturer_code == 0x000001  # stored as int
    assert 0xD6 not in node_state.properties  # Not yet received

    # Frames no longer update existing nodes
    update_frame = TestFrame(
        tid=2,
        seoj=int(node_profile_eoj).to_bytes(3, "big"),
        deoj=bytes.fromhex("05ff01"),
        esv=ESV.GET_RES,
        properties=[
            TestProperty(epc=0xD6, edt=bytes.fromhex("01001101")),
        ],
    )
    await coordinator.device_manager.process_frame_event(
        make_frame_event(
            update_frame,
            received_at=31.0,
            node_id=node_hex,
            eoj=node_profile_eoj,
        ),
    )
    await hass.async_block_till_done()

    node_state = coordinator.data[node_id]
    assert 0xD6 in node_state.properties
    assert node_state.properties[0xD6] == bytes.fromhex("01001101")


async def test_property_poller_respects_get_property_map(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Ensure the poller only requests properties listed in the Get property map."""

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    now = time.monotonic()

    node_hex = bytes.fromhex("00000000000002").hex()

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=0x9F, edt=bytes.fromhex("018A")),  # Get map: 1 EPC (0x8A)
        TestProperty(epc=0x9D, edt=bytes.fromhex("00")),  # INF map: empty
        TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
    ]

    with patch(
        "pyhems.device_manager.time.monotonic",
        return_value=now,
    ):
        await coordinator.device_manager.setup_device(node_hex, EOJ(0x001101))

    await hass.async_block_till_done()

    # Verify that NO requests were sent for 0xE0
    # Check all calls to send
    for call in mock_echonet_lite_client.send.call_args_list:
        frame = call.args[1]
        # Ensure 0xE0 is NOT in any request
        assert not any(p.epc == 0xE0 for p in frame.properties), (
            f"Found request for 0xE0 in {frame}"
        )

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_property_poller_requests_notifications_for_required_epcs(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Ensure the poller polls required EPCs not in INF map."""

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    now = time.monotonic()

    node_hex = bytes.fromhex("00000000000002").hex()

    entry.runtime_data.client.get.return_value = [
        # Get map: 0x8A (ID) and 0xB3 (required temp setpoint)
        TestProperty(epc=0x9F, edt=bytes.fromhex("028AB3")),
        # INF map announces 0xB3 so 0x63 can be used
        TestProperty(epc=0x9D, edt=bytes.fromhex("01B3")),
        TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
        TestProperty(epc=0xB3, edt=bytes.fromhex("19")),
    ]

    with patch(
        "pyhems.device_manager.time.monotonic",
        return_value=now,
    ):
        await coordinator.device_manager.setup_device(node_hex, EOJ(0x013001))

    await hass.async_block_till_done()

    # Initial 0x63 should be sent once after device creation (since INF map includes 0xB3)
    calls = mock_echonet_lite_client.send.call_args_list
    assert any(
        call.args[1].esv == 0x63
        and any(prop.epc == 0xB3 for prop in call.args[1].properties)
        for call in calls
    ), "Should request notifications for 0xB3 via 0x63"


async def test_climate_poller_requests_fan_and_swing(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Ensure poller includes fan/swing EPCs for climate devices."""

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    poller = entry.runtime_data.property_poller
    now = time.monotonic()

    node_hex = bytes.fromhex("00000000000002").hex()

    # Get map includes required climate EPCs plus fan/swing; INF map empty
    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=0x9F, edt=bytes.fromhex("0680B0B3BBA0A3")),
        TestProperty(epc=0x9D, edt=bytes.fromhex("00")),
        TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
        TestProperty(epc=0x80, edt=bytes.fromhex("30")),
    ]

    with patch(
        "pyhems.device_manager.time.monotonic",
        return_value=now,
    ):
        await coordinator.device_manager.setup_device(node_hex, EOJ(0x013001))

    await hass.async_block_till_done()

    # Trigger a poll cycle manually
    poller._schedule_polls()
    await hass.async_block_till_done()

    # Expect a 0x62 poll including both fan speed (0xA0) and swing (0xA3)
    assert any(
        call.args[1].esv == 0x62
        and {prop.epc for prop in call.args[1].properties}.issuperset({0xA0, 0xA3})
        for call in mock_echonet_lite_client.send.call_args_list
    ), "Poller should request fan and swing EPCs for climate devices"

    # Trigger polling interval again
    poller._schedule_polls()
    await hass.async_block_till_done()

    # Verify 0x62 polling continues for 0xB3
    calls = mock_echonet_lite_client.send.call_args_list
    assert any(
        call.args[1].esv == 0x62
        and any(prop.epc == 0xB3 for prop in call.args[1].properties)
        for call in calls
    ), "Should poll 0xB3 via 0x62"


async def test_property_poller_handles_sna_notification_response(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Ensure the poller keeps polling when device does not announce values."""

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    poller = entry.runtime_data.property_poller
    now = time.monotonic()

    node_hex = bytes.fromhex("00000000000002").hex()

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=0x9F, edt=bytes.fromhex("028AB3")),
        # INF map is empty: device does not announce B3
        TestProperty(epc=0x9D, edt=bytes.fromhex("00")),
        TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
        TestProperty(epc=0xB3, edt=bytes.fromhex("19")),
    ]

    with patch(
        "pyhems.device_manager.time.monotonic",
        return_value=now,
    ):
        await coordinator.device_manager.setup_device(node_hex, EOJ(0x013001))

    await hass.async_block_till_done()

    # No 0x63 should be sent because INF map does not include 0xB3
    assert not any(
        call.args[1].esv == 0x63
        for call in mock_echonet_lite_client.send.call_args_list
    ), "Should not request notifications when device does not announce properties"

    # Trigger polling interval manually
    poller._schedule_polls()
    await hass.async_block_till_done()

    # Should poll 0xB3
    assert any(
        call.args[1].esv == 0x62
        and any(prop.epc == 0xB3 for prop in call.args[1].properties)
        for call in mock_echonet_lite_client.send.call_args_list
    )


async def test_async_setup_raises_on_client_start_error(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """``OSError`` from ``client.start`` must surface as ``ConfigEntryNotReady``."""

    mock_echonet_lite_client.start.side_effect = OSError("socket busy")

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert "socket busy" in (entry.reason or "")


async def test_remove_config_entry_device_allows_when_not_discovered(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Device not in coordinator data can be removed."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "00000000000001-013001")},
    )

    # coordinator.data is empty (device not currently discovered) → allow removal
    assert await async_remove_config_entry_device(hass, entry, device_entry)

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_remove_config_entry_device_rejects_when_active(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Device currently in coordinator data cannot be removed."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    node_hex = bytes.fromhex("00000000000001").hex()
    node_profile_eoj = EOJ(0x0EF001)

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=0x9F, edt=bytes.fromhex("018A")),
        TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
    ]

    with patch(
        "pyhems.device_manager.time.monotonic",
        return_value=30.0,
    ):
        await coordinator.device_manager.setup_device(node_hex, node_profile_eoj)

    node_id = f"{node_hex}-{int(node_profile_eoj):06x}"
    assert node_id in coordinator.data

    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, node_id)},
    )

    # device is still active in coordinator.data → reject removal
    assert not await async_remove_config_entry_device(hass, entry, device_entry)

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_remove_config_entry_device_allows_when_polling_unavailable(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A device with failed liveness polling can be removed."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    node_hex = bytes.fromhex("00000000000001").hex()
    node_profile_eoj = EOJ(0x0EF001)

    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=0x9F, edt=bytes.fromhex("018A")),
        TestProperty(epc=0x8A, edt=bytes.fromhex("000001")),
    ]

    with patch(
        "pyhems.device_manager.time.monotonic",
        return_value=30.0,
    ):
        await coordinator.device_manager.setup_device(node_hex, node_profile_eoj)

    node_id = f"{node_hex}-{int(node_profile_eoj):06x}"
    coordinator.data[node_id].polling_available = False

    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, node_id)},
    )

    assert await async_remove_config_entry_device(hass, entry, device_entry)

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_update_listener_reloads_entry(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Changing options reloads the config entry."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.config_entries.async_update_entry(entry)
    await hass.async_block_till_done()

    # Entry is reloaded and remains loaded with the new options applied.
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_entry_returns_false_when_platforms_fail(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Unload returns False (and leaves runtime intact) when platforms fail."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        AsyncMock(return_value=False),
    ):
        assert not await hass.config_entries.async_unload(entry.entry_id)

    assert entry.state is ConfigEntryState.FAILED_UNLOAD

    # The runtime client must not have been stopped on a failed unload.
    mock_echonet_lite_client.stop.assert_not_called()

    runtime = entry.runtime_data
    await runtime.async_stop()


def test_legacy_measurement_channel_epcs_excluded_from_monitored_sets() -> None:
    """Class 0x0287's 0xD0-0xEF never enter the monitored/fast-poll EPC sets.

    Regression guard for docs/ha-0287-epc-be-implementation-report-v2.md
    section 8: this must hold at module level (before any device is even
    discovered), independent of what a real device's Get property map
    contains.
    """
    legacy_epcs = frozenset(range(0xD0, 0xF0))
    assert not (_MONITORED_EPCS[0x0287] & legacy_epcs)
    assert not (_FAST_POLL_EPCS.get(0x0287, frozenset()) & legacy_epcs)


def test_collection_epcs_are_monitored_for_power_distribution_board() -> None:
    """The paged list EPCs (no scalar EntityDefinition) are still monitored."""
    monitored = _MONITORED_EPCS[0x0287]
    assert {0xB1, 0xB3, 0xB7, 0xB8, 0xBA, 0xBE, 0xC2} <= monitored
    # Only the instantaneous power lists are fast-polled.
    fast = _FAST_POLL_EPCS[0x0287]
    assert 0xB7 in fast
    assert 0xBE in fast
    assert 0xB3 not in fast
    assert 0xBA not in fast
