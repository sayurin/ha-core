"""Snapshot tests for ECHONET Lite platforms.

These tests exercise the platform entity setup for a representative device on
each platform and verify the resulting entity registry entries and states
against a snapshot. They complement the per-platform behavioural tests by
guarding against regressions in entity metadata (names, units, device classes,
supported features, options, ...).
"""

from collections.abc import Callable, Coroutine
from typing import Any

from pyhems import EOJ, DeviceClass
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.echonet_lite.const import (
    EPC_COVER_ANGLE,
    EPC_COVER_OPEN_CLOSE,
    EPC_COVER_OPEN_CLOSED_STATUS,
    EPC_COVER_POSITION,
    EPC_LOCK_SETTING_1,
)
from homeassistant.const import Platform
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

from tests.common import MockConfigEntry, snapshot_platform

# Each setup function configures the mocked client's GET response and calls
# device_manager.setup_device(). Helper functions intentionally keep the data
# minimal but representative so the resulting snapshots are deterministic.

SetupFn = Callable[[HomeAssistant, MockConfigEntry], Coroutine[Any, Any, None]]

EPC_CURRENT_LIMIT = 0x87


def _mfr_property() -> TestProperty:
    return TestProperty(
        epc=EPC_MANUFACTURER_CODE,
        edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
    )


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    node_hex: str,
    eoj_int: int,
    properties: list[TestProperty],
) -> None:
    """Configure the mocked client and discover one device."""
    entry.runtime_data.client.get.return_value = properties
    await entry.runtime_data.coordinator.device_manager.setup_device(
        bytes.fromhex(node_hex).hex(), EOJ(eoj_int)
    )
    await hass.async_block_till_done()


async def _setup_sensor(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    # Temperature sensor (class 0x0011) reporting 10.0 C.
    await _setup(
        hass,
        entry,
        node_hex="010102",
        eoj_int=0x001101,
        properties=[
            TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xE0)),
            _mfr_property(),
            TestProperty(epc=0xE0, edt=b"\x00d"),
        ],
    )


async def _setup_binary_sensor(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    # Human-body-presence sensor (class 0x0007) reporting "detected".
    await _setup(
        hass,
        entry,
        node_hex="010101",
        eoj_int=0x000701,
        properties=[
            TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xB1)),
            _mfr_property(),
            TestProperty(epc=0xB1, edt=b"\x41"),
        ],
    )


async def _setup_switch(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    # Air cleaner (class 0x0135) with operation status writable.
    await _setup(
        hass,
        entry,
        node_hex="010106",
        eoj_int=0x027901,
        properties=[
            TestProperty(epc=EPC_SET_PROPERTY_MAP, edt=b"\x01\x80"),
            TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=b"\x01\x80"),
            _mfr_property(),
            TestProperty(epc=0x80, edt=b"\x31"),
        ],
    )


async def _setup_button(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    # Crime prevention sensor (class 0x0002) with write-only reset EPC 0xBF.
    await _setup(
        hass,
        entry,
        node_hex="010201",
        eoj_int=0x000201,
        properties=[
            TestProperty(epc=EPC_SET_PROPERTY_MAP, edt=b"\x01\xbf"),
            TestProperty(epc=EPC_GET_PROPERTY_MAP, edt=b"\x02\xb0\xb1"),
            _mfr_property(),
            TestProperty(epc=0xB0, edt=b"\x41"),
            TestProperty(epc=0xB1, edt=b"\x42"),
        ],
    )


async def _setup_number(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    # Generic device exposing current limit (EPC 0x87) as a number entity.
    await _setup(
        hass,
        entry,
        node_hex="010201",
        eoj_int=0x001101,
        properties=[
            TestProperty(
                epc=EPC_GET_PROPERTY_MAP,
                edt=make_property_map_edt(EPC_CURRENT_LIMIT),
            ),
            TestProperty(
                epc=EPC_SET_PROPERTY_MAP,
                edt=make_property_map_edt(EPC_CURRENT_LIMIT),
            ),
            _mfr_property(),
            TestProperty(epc=EPC_CURRENT_LIMIT, edt=b"\x32"),
        ],
    )


async def _setup_select(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    # Home air conditioner (class 0x0130) with airflow direction selects.
    await _setup(
        hass,
        entry,
        node_hex="010106",
        eoj_int=0x013001,
        properties=[
            TestProperty(
                epc=EPC_SET_PROPERTY_MAP, edt=make_property_map_edt(0xA1, 0xA4, 0xA5)
            ),
            TestProperty(
                epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(0xA1, 0xA4, 0xA5)
            ),
            _mfr_property(),
            TestProperty(epc=0xA1, edt=b"\x41"),
            TestProperty(epc=0xA4, edt=b"\x41"),
            TestProperty(epc=0xA5, edt=b"\x51"),
        ],
    )


async def _setup_cover(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    # Electrically operated blind (class 0x0260) with full position/angle support.
    advertised = (
        EPC_COVER_OPEN_CLOSE,
        EPC_COVER_POSITION,
        EPC_COVER_ANGLE,
        EPC_COVER_OPEN_CLOSED_STATUS,
    )
    set_epcs = (EPC_COVER_OPEN_CLOSE, EPC_COVER_POSITION, EPC_COVER_ANGLE)
    eoj_int = (DeviceClass.ELECTRIC_BLIND_SHADE << 8) | 0x01
    await _setup(
        hass,
        entry,
        node_hex="010104",
        eoj_int=eoj_int,
        properties=[
            TestProperty(
                epc=EPC_GET_PROPERTY_MAP, edt=make_property_map_edt(*advertised)
            ),
            TestProperty(
                epc=EPC_SET_PROPERTY_MAP, edt=make_property_map_edt(*set_epcs)
            ),
            _mfr_property(),
            TestProperty(epc=EPC_COVER_OPEN_CLOSED_STATUS, edt=b"\x41"),
            TestProperty(epc=EPC_COVER_POSITION, edt=b"\x64"),
            TestProperty(epc=EPC_COVER_ANGLE, edt=b"\x5a"),
        ],
    )


async def _setup_lock(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    # Electric lock (class 0x026F) in the locked state.
    eoj_int = (DeviceClass.ELECTRIC_LOCK << 8) | 0x01
    await _setup(
        hass,
        entry,
        node_hex="010104",
        eoj_int=eoj_int,
        properties=[
            TestProperty(
                epc=EPC_GET_PROPERTY_MAP,
                edt=make_property_map_edt(EPC_LOCK_SETTING_1),
            ),
            TestProperty(
                epc=EPC_SET_PROPERTY_MAP,
                edt=make_property_map_edt(EPC_LOCK_SETTING_1),
            ),
            _mfr_property(),
            TestProperty(epc=EPC_LOCK_SETTING_1, edt=b"\x41"),
        ],
    )


_SETUPS: dict[Platform, SetupFn] = {
    Platform.SENSOR: _setup_sensor,
    Platform.BINARY_SENSOR: _setup_binary_sensor,
    Platform.SWITCH: _setup_switch,
    Platform.BUTTON: _setup_button,
    Platform.NUMBER: _setup_number,
    Platform.SELECT: _setup_select,
    Platform.COVER: _setup_cover,
    Platform.LOCK: _setup_lock,
}


@pytest.fixture(name="platforms")
def platforms_fixture(request: pytest.FixtureRequest) -> list[Platform]:
    """Limit the integration to the platform under test."""
    return [request.param]


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
@pytest.mark.parametrize(
    "platforms",
    list(_SETUPS),
    indirect=True,
    ids=lambda platform: platform.value,
)
async def test_platform_snapshot(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
    snapshot: SnapshotAssertion,
    platforms: list[Platform],
) -> None:
    """Set up a representative device per platform and snapshot the entities."""
    await _SETUPS[platforms[0]](hass, init_integration)
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)
