"""Tests for the ECHONET Lite runtime lifecycle (RuntimeIssueMonitor, RuntimeController)."""

from collections.abc import Callable
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from pyhems import EOJ, ESV, HemsErrorEvent, HemsInstanceListEvent
import pytest

from homeassistant.components.echonet_lite.const import (
    CONF_INTERFACE,
    DOMAIN,
    ISSUE_RUNTIME_CLIENT_ERROR,
    ISSUE_RUNTIME_INACTIVE,
    RUNTIME_MONITOR_MAX_SILENCE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .conftest import TestFrame, TestProperty, make_frame_event

from tests.common import MockConfigEntry


async def test_runtime_inactivity_issue_raised_and_cleared(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test that the runtime monitor raises and clears repair issues."""

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    callbacks: list = []

    def _track_time_interval(
        hass: HomeAssistant,
        callback: Callable[[datetime], None],
        interval: timedelta,
    ) -> Callable[[], None]:
        callbacks.append(callback)
        return lambda: None

    with patch(
        "homeassistant.components.echonet_lite.runtime.async_track_time_interval",
        side_effect=_track_time_interval,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        monitor = entry.runtime_data.issue_monitor
        coordinator = entry.runtime_data.coordinator
        assert monitor is not None
        assert coordinator is not None
        timeline = {"value": 0.0}
        monitor._monotonic = lambda: timeline["value"]
        monitor.record_activity(0.0)
        assert coordinator.last_runtime_activity_at == 0.0

        assert not issue_registry.async_get_issue(DOMAIN, ISSUE_RUNTIME_INACTIVE)

        timeline["value"] = RUNTIME_MONITOR_MAX_SILENCE.total_seconds() + 30
        assert callbacks
        callbacks[0](dt_util.utcnow())
        issue = issue_registry.async_get_issue(DOMAIN, ISSUE_RUNTIME_INACTIVE)
        assert issue is not None

        timeline["value"] = 0.0
        monitor.record_activity(0.0)
        assert coordinator.last_runtime_activity_at == 0.0
        assert not issue_registry.async_get_issue(DOMAIN, ISSUE_RUNTIME_INACTIVE)

        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_runtime_inactivity_issue_raised_when_no_frames_ever_received(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Initial silence must also trip the inactivity monitor.

    Without a startup baseline, ``last_runtime_activity_at`` stayed ``None``
    indefinitely when no frames arrived after setup, so the monitor never
    surfaced a repair issue in that scenario.
    """

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    callbacks: list = []

    def _track_time_interval(
        hass: HomeAssistant,
        callback: Callable[[datetime], None],
        interval: timedelta,
    ) -> Callable[[], None]:
        callbacks.append(callback)
        return lambda: None

    with patch(
        "homeassistant.components.echonet_lite.runtime.async_track_time_interval",
        side_effect=_track_time_interval,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        monitor = entry.runtime_data.issue_monitor
        coordinator = entry.runtime_data.coordinator
        assert monitor is not None
        assert coordinator is not None

        # Setup must have seeded a baseline so the monitor is actually armed.
        baseline = coordinator.last_runtime_activity_at
        assert baseline is not None

        timeline = {"value": baseline}
        monitor._monotonic = lambda: timeline["value"]

        # No frames have ever arrived: advance past the silence threshold
        # and confirm the issue is raised purely from the startup baseline.
        timeline["value"] = baseline + RUNTIME_MONITOR_MAX_SILENCE.total_seconds() + 30
        assert callbacks
        callbacks[0](dt_util.utcnow())
        assert issue_registry.async_get_issue(DOMAIN, ISSUE_RUNTIME_INACTIVE)

        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_runtime_error_restart_success_updates_health(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test that runtime errors trigger a restart and update health metadata."""

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    error_event = HemsErrorEvent(received_at=1.0, error=RuntimeError("boom"))
    await mock_echonet_lite_client._listener(error_event)
    await hass.async_block_till_done()

    health = entry.runtime_data.health
    assert health is not None
    assert health.restart_attempts == 1
    assert health.last_client_error == "boom"
    assert health.last_client_error_at == 1.0
    assert health.last_restart_at is not None
    assert mock_echonet_lite_client.start.await_count == 2
    assert mock_echonet_lite_client.stop.await_count == 1

    assert not issue_registry.async_get_issue(DOMAIN, ISSUE_RUNTIME_CLIENT_ERROR)

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_runtime_error_restart_preserves_known_devices(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Known devices must remain in coordinator.data after a restart.

    Regression: a previous implementation reset ``coordinator.data`` to an
    empty dict on successful restart, which made entities for already-known
    devices disappear silently because ``DeviceManager.data`` is retained
    across ``client.stop()``/``client.start()`` and therefore never re-fires
    ``on_device_added``.
    """

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

    error_event = HemsErrorEvent(received_at=1.0, error=RuntimeError("boom"))
    await mock_echonet_lite_client._listener(error_event)
    await hass.async_block_till_done()

    # After a successful restart, the device must still be known so that
    # existing entities do not go unavailable forever.
    assert node_id in coordinator.data

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_runtime_error_restart_clears_inactivity_issue(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Successful runtime restart should also clear the inactivity issue."""

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    callbacks: list = []

    def _track_time_interval(
        hass: HomeAssistant,
        callback: Callable[[datetime], None],
        interval: timedelta,
    ) -> Callable[[], None]:
        callbacks.append(callback)
        return lambda: None

    with patch(
        "homeassistant.components.echonet_lite.runtime.async_track_time_interval",
        side_effect=_track_time_interval,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        monitor = entry.runtime_data.issue_monitor
        timeline = {"value": 0.0}
        monitor._monotonic = lambda: timeline["value"]
        monitor.record_activity(0.0)

        # Drive the monitor past the inactivity threshold to raise the issue.
        timeline["value"] = RUNTIME_MONITOR_MAX_SILENCE.total_seconds() + 30
        assert callbacks
        callbacks[0](dt_util.utcnow())
        assert issue_registry.async_get_issue(DOMAIN, ISSUE_RUNTIME_INACTIVE)

        # A runtime error triggers a successful restart, which should clear
        # the inactivity issue without waiting for the next frame.
        error_event = HemsErrorEvent(received_at=1.0, error=RuntimeError("boom"))
        await mock_echonet_lite_client._listener(error_event)
        await hass.async_block_till_done()

        assert not issue_registry.async_get_issue(DOMAIN, ISSUE_RUNTIME_INACTIVE)

        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_runtime_error_restart_failure_creates_issue(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test runtime errors raise a repair issue when restart fails."""

    mock_echonet_lite_client.start.side_effect = [None, OSError("socket busy")]

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    error_event = HemsErrorEvent(received_at=1.0, error=RuntimeError("boom"))
    await mock_echonet_lite_client._listener(error_event)
    await hass.async_block_till_done()

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_RUNTIME_CLIENT_ERROR)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert "socket busy" in issue.translation_placeholders["error"]

    health = entry.runtime_data.health
    assert health is not None
    assert health.restart_attempts == 1
    assert health.last_restart_at is None

    assert mock_echonet_lite_client.start.await_count == 2
    assert mock_echonet_lite_client.stop.await_count == 1

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_device_manager_event_consumer_swallows_expected_exceptions(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Frame-handling failures in pyhems' realistic fault classes are logged.

    DeviceManager's event consumer swallows transient I/O / parsing errors so
    the consumer task keeps running. Programmer errors are intentionally not
    caught and would crash the task instead.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator

    with patch.object(
        coordinator.device_manager,
        "process_instance_list_event",
        AsyncMock(side_effect=ValueError("malformed")),
    ):
        instance_event = HemsInstanceListEvent(
            received_at=1.0,
            node_id="00000000000001",
            instances=[EOJ(0x013001)],
            properties={},
        )
        await mock_echonet_lite_client._listener(instance_event)
        await hass.async_block_till_done()

    assert "Failed to process ECHONET Lite runtime event" in caplog.text
    # Consumer task is still running and able to take the next event.
    assert not coordinator.device_manager.event_consumer_task_done

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_runtime_monitor_check_branches(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Exercise the early-return branches of the runtime inactivity check."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    callbacks: list[Callable[[datetime], None]] = []

    def _track_time_interval(
        hass: HomeAssistant,
        callback: Callable[[datetime], None],
        interval: timedelta,
    ) -> Callable[[], None]:
        callbacks.append(callback)
        return lambda: None

    with patch(
        "homeassistant.components.echonet_lite.runtime.async_track_time_interval",
        side_effect=_track_time_interval,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        monitor = entry.runtime_data.issue_monitor
        timeline = {"value": 100.0}
        monitor._monotonic = lambda: timeline["value"]

        # Starting again while already running is a no-op.
        monitor.start()

        assert callbacks

        # Recent activity keeps the issue cleared.
        monitor.record_activity(100.0)
        timeline["value"] = 100.0
        callbacks[0](dt_util.utcnow())
        assert not issue_registry.async_get_issue(DOMAIN, ISSUE_RUNTIME_INACTIVE)

        # Crossing the silence threshold raises the issue.
        timeline["value"] = 100.0 + RUNTIME_MONITOR_MAX_SILENCE.total_seconds() + 30
        callbacks[0](dt_util.utcnow())
        assert issue_registry.async_get_issue(DOMAIN, ISSUE_RUNTIME_INACTIVE)

        # A subsequent tick with the issue already active returns early.
        callbacks[0](dt_util.utcnow())
        assert issue_registry.async_get_issue(DOMAIN, ISSUE_RUNTIME_INACTIVE)

        # Without any recorded activity the check returns before evaluating.
        entry.runtime_data.health.last_runtime_activity_at = None
        callbacks[0](dt_util.utcnow())

        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_device_manager_records_activity_for_frame_and_instance_events(
    hass: HomeAssistant,
    mock_definitions_registry: None,
    mock_echonet_lite_client: AsyncMock,
) -> None:
    """Frame and instance-list events record runtime activity."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_INTERFACE: "0.0.0.0"})
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator

    # A frame event for an unknown node is processed and records activity.
    frame = TestFrame(
        tid=1,
        seoj=bytes.fromhex("013001"),
        deoj=bytes.fromhex("05ff01"),
        esv=ESV.GET_RES,
        properties=[TestProperty(epc=0x80, edt=b"\x30")],
    )
    frame_event = make_frame_event(
        frame,
        node_id=bytes.fromhex("00000000000009").hex(),
        eoj=EOJ(0x013001),
        received_at=5.0,
    )
    await mock_echonet_lite_client._listener(frame_event)
    await hass.async_block_till_done()
    assert coordinator.last_runtime_activity_at == 5.0

    # An instance-list event is processed and records activity.
    instance_event = HemsInstanceListEvent(
        received_at=7.0,
        node_id=bytes.fromhex("00000000000009").hex(),
        instances=[EOJ(0x0EF001)],
        properties={},
    )
    await mock_echonet_lite_client._listener(instance_event)
    await hass.async_block_till_done()
    assert coordinator.last_runtime_activity_at == 7.0

    assert await hass.config_entries.async_unload(entry.entry_id)
