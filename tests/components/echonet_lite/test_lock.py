"""Tests for the ECHONET Lite lock platform."""

from unittest.mock import patch

from pyhems import EOJ, BinaryCodec, DeviceClass, get_codec_for_epc
import pytest

from homeassistant.components.echonet_lite.const import (
    DOMAIN,
    EPC_LOCK_ALARM_STATUS,
    EPC_LOCK_SETTING_1,
    EPC_LOCK_SETTING_2,
)
from homeassistant.components.lock import SERVICE_LOCK, SERVICE_UNLOCK, LockState
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN, Platform
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

_LOCKED = b"\x41"
_UNLOCKED = b"\x42"
_ALARM_NORMAL = b"\x40"
_ALARM_BREAK_OPEN = b"\x41"


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable the lock platform for these tests."""
    return [Platform.LOCK]


def _eoj_hex(class_code: int, instance: int = 1) -> str:
    return f"{class_code:04x}{instance:02x}"


async def _setup_lock(
    hass: HomeAssistant,
    entry,
    entity_registry: er.EntityRegistry,
    *,
    advertised_epcs: set[int],
    set_epcs: set[int],
    properties: list[TestProperty],
    node_hex: str = "010104",
    instance: int = 1,
) -> str:
    """Set up an electric lock device and return its entity_id."""
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex(node_hex).hex()
    seoj_hex = _eoj_hex(DeviceClass.ELECTRIC_LOCK, instance)
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

    unique_id = f"{node_id}-{seoj_hex}-lock"
    entity_id = entity_registry.async_get_entity_id("lock", DOMAIN, unique_id)
    assert entity_id is not None, f"lock entity {unique_id} not found"
    return entity_id


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_locked_state(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Reports locked when primary lock is locked and no sub-lock is advertised."""
    entry = init_integration

    advertised_epcs = {EPC_LOCK_SETTING_1}
    set_epcs = {EPC_LOCK_SETTING_1}
    entity_id = await _setup_lock(
        hass,
        entry,
        entity_registry,
        advertised_epcs=advertised_epcs,
        set_epcs=set_epcs,
        properties=[TestProperty(epc=EPC_LOCK_SETTING_1, edt=_LOCKED)],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == LockState.LOCKED


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_unlocked_state(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Reports unlocked when primary lock is unlocked."""
    entry = init_integration

    entity_id = await _setup_lock(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_LOCK_SETTING_1},
        set_epcs={EPC_LOCK_SETTING_1},
        properties=[TestProperty(epc=EPC_LOCK_SETTING_1, edt=_UNLOCKED)],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == LockState.UNLOCKED


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_sub_lock_partial(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Reports unlocked when main is locked but sub-lock is unlocked."""
    entry = init_integration

    entity_id = await _setup_lock(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_LOCK_SETTING_1, EPC_LOCK_SETTING_2},
        set_epcs={EPC_LOCK_SETTING_1, EPC_LOCK_SETTING_2},
        properties=[
            TestProperty(epc=EPC_LOCK_SETTING_1, edt=_LOCKED),
            TestProperty(epc=EPC_LOCK_SETTING_2, edt=_UNLOCKED),
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == LockState.UNLOCKED


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_sub_lock_both_locked(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Reports locked when both main and sub-lock are locked."""
    entry = init_integration

    entity_id = await _setup_lock(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_LOCK_SETTING_1, EPC_LOCK_SETTING_2},
        set_epcs={EPC_LOCK_SETTING_1, EPC_LOCK_SETTING_2},
        properties=[
            TestProperty(epc=EPC_LOCK_SETTING_1, edt=_LOCKED),
            TestProperty(epc=EPC_LOCK_SETTING_2, edt=_LOCKED),
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == LockState.LOCKED


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_jammed_when_alarm_set(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Reports jammed when alarm EPC is any non-normal value."""
    entry = init_integration

    entity_id = await _setup_lock(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_LOCK_SETTING_1, EPC_LOCK_ALARM_STATUS},
        set_epcs={EPC_LOCK_SETTING_1},
        properties=[
            TestProperty(epc=EPC_LOCK_SETTING_1, edt=_LOCKED),
            TestProperty(epc=EPC_LOCK_ALARM_STATUS, edt=_ALARM_BREAK_OPEN),
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == LockState.JAMMED


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_alarm_normal_not_jammed(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Alarm value 0x40 (normal) leaves the lock state unchanged."""
    entry = init_integration

    entity_id = await _setup_lock(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_LOCK_SETTING_1, EPC_LOCK_ALARM_STATUS},
        set_epcs={EPC_LOCK_SETTING_1},
        properties=[
            TestProperty(epc=EPC_LOCK_SETTING_1, edt=_LOCKED),
            TestProperty(epc=EPC_LOCK_ALARM_STATUS, edt=_ALARM_NORMAL),
        ],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == LockState.LOCKED


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_lock_service_writes_primary_only(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """Lock service sends only EPC 0xE0 = 0x41, even when sub-lock exists."""
    entry = init_integration

    entity_id = await _setup_lock(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_LOCK_SETTING_1, EPC_LOCK_SETTING_2},
        set_epcs={EPC_LOCK_SETTING_1, EPC_LOCK_SETTING_2},
        properties=[
            TestProperty(epc=EPC_LOCK_SETTING_1, edt=_UNLOCKED),
            TestProperty(epc=EPC_LOCK_SETTING_2, edt=_UNLOCKED),
        ],
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            "lock",
            SERVICE_LOCK,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    props = call.args[1].properties
    assert len(props) == 1
    assert props[0].epc == EPC_LOCK_SETTING_1
    assert props[0].edt == _LOCKED


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_unlock_service(
    hass: HomeAssistant,
    init_integration,
    mock_echonet_lite_client,
    entity_registry: er.EntityRegistry,
) -> None:
    """Unlock service sends EPC 0xE0 = 0x42."""
    entry = init_integration

    entity_id = await _setup_lock(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_LOCK_SETTING_1},
        set_epcs={EPC_LOCK_SETTING_1},
        properties=[TestProperty(epc=EPC_LOCK_SETTING_1, edt=_LOCKED)],
    )

    mock_echonet_lite_client.send.reset_mock()
    with patch.object(entry.runtime_data.property_poller, "schedule_immediate_poll"):
        await hass.services.async_call(
            "lock",
            SERVICE_UNLOCK,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert mock_echonet_lite_client.send.call_count == 1
    call = mock_echonet_lite_client.send.call_args_list[0]
    props = call.args[1].properties
    assert len(props) == 1
    assert props[0].epc == EPC_LOCK_SETTING_1
    assert props[0].edt == _UNLOCKED


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_no_lock_entity_for_other_class(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """No lock entity is created for non electric-lock devices."""
    entry = init_integration
    coordinator = entry.runtime_data.coordinator

    node_id = bytes.fromhex("010109").hex()
    seoj_hex = "001101"  # Temperature sensor, not an electric lock
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
            "lock", DOMAIN, f"{node_id}-{seoj_hex}-lock"
        )
        is None
    )


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_lock_state_unknown_without_value(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Reports unknown when the primary lock setting has no value."""
    entry = init_integration

    entity_id = await _setup_lock(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_LOCK_SETTING_1},
        set_epcs={EPC_LOCK_SETTING_1},
        properties=[],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_UNKNOWN


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_lock_state_unknown_for_unexpected_value(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Reports unknown when the primary lock reports an unexpected value."""
    entry = init_integration

    entity_id = await _setup_lock(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_LOCK_SETTING_1},
        set_epcs={EPC_LOCK_SETTING_1},
        properties=[TestProperty(epc=EPC_LOCK_SETTING_1, edt=b"\x00")],
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_UNKNOWN


@pytest.mark.usefixtures("mock_echonet_lite_client")
async def test_lock_setting_codec_is_binary(
    hass: HomeAssistant, init_integration
) -> None:
    """EPC 0xE0 must return BinaryCodec for the electric lock class.

    This is an HA-side contract test: if pyhems changes its definitions in a
    way that breaks the lock entity's locked/unlocked decoding, this test
    catches it.
    """
    codec = get_codec_for_epc(DeviceClass.ELECTRIC_LOCK, EPC_LOCK_SETTING_1)
    assert isinstance(codec, BinaryCodec)


@pytest.mark.usefixtures("mock_definitions_registry", "mock_echonet_lite_client")
async def test_disabling_lock_excludes_dedicated_epcs_from_polling(
    hass: HomeAssistant,
    init_integration,
    entity_registry: er.EntityRegistry,
) -> None:
    """Disabling the lock entity excludes all its EPCs from polling (Step 6).

    Dedicated-platform entities (lock/climate/fan/cover/light/water_heater)
    subscribe to their whole DEDICATED_PLATFORM_EPCS set as a single unit,
    since a single entity instance represents the entire device class.
    """
    entry = init_integration

    entity_id = await _setup_lock(
        hass,
        entry,
        entity_registry,
        advertised_epcs={EPC_LOCK_SETTING_1, EPC_LOCK_SETTING_2, EPC_LOCK_ALARM_STATUS},
        set_epcs={EPC_LOCK_SETTING_1, EPC_LOCK_SETTING_2},
        properties=[
            TestProperty(epc=EPC_LOCK_SETTING_1, edt=_LOCKED),
            TestProperty(epc=EPC_LOCK_SETTING_2, edt=_LOCKED),
            TestProperty(epc=EPC_LOCK_ALARM_STATUS, edt=_ALARM_NORMAL),
        ],
    )

    device_manager = entry.runtime_data.coordinator.device_manager
    device_key = f"010104-{_eoj_hex(DeviceClass.ELECTRIC_LOCK)}"
    effective = device_manager.effective_poll_epcs(device_key)
    assert EPC_LOCK_SETTING_1 in effective
    assert EPC_LOCK_SETTING_2 in effective
    assert EPC_LOCK_ALARM_STATUS in effective

    entity_registry.async_update_entity(
        entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()

    assert hass.states.get(entity_id) is None
    effective = device_manager.effective_poll_epcs(device_key)
    assert EPC_LOCK_SETTING_1 not in effective
    assert EPC_LOCK_SETTING_2 not in effective
    assert EPC_LOCK_ALARM_STATUS not in effective
