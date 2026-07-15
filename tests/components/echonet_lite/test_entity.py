"""Tests for the ECHONET Lite base entity module.

These tests verify base entity error handling in _send_property,
_send_properties, and _send_prop methods. Uses switch platform
as a simple vehicle to exercise the shared code path.
"""

from unittest.mock import AsyncMock, patch

from pyhems import EOJ, ESV, EntityDefinition, EnumValue, NodeState, SetRequestResult
import pytest

from homeassistant.components.echonet_lite.const import DOMAIN
from homeassistant.components.echonet_lite.entity import (
    EchonetLiteEntityDescription,
    _get_or_build_device_info,
    can_process_enum_values,
    infer_platform,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from .conftest import EPC_MANUFACTURER_CODE, TEST_MANUFACTURER_CODE, TestProperty

from tests.common import MockConfigEntry

EPC_OPERATION_STATUS = 0x80


@pytest.fixture(name="platforms")
def platforms_fixture() -> list[Platform]:
    """Enable the switch platform for base entity tests."""
    return [Platform.SWITCH]


async def _setup_switch_device(
    hass: HomeAssistant,
    entry,
    entity_registry: er.EntityRegistry,
) -> str:
    """Set up a switch device (air cleaner) and return the entity_id."""
    coordinator = entry.runtime_data.coordinator

    node_id = "010106"
    eoj = EOJ(0x027901)  # Fuel cell instance 1 (not air cleaner to avoid fan platform)

    # EPC 0x80 in SET property map enables switch entity
    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=0x9E, edt=b"\x01\x80"),  # SET property map: 0x80
        TestProperty(epc=0x9F, edt=b"\x01\x80"),  # GET property map: 0x80
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x31"),  # off
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "switch", DOMAIN, "010106-027901-80"
    )
    assert entity_id is not None
    return entity_id


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_send_property_address_unknown_raises_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that error is raised when target node address is unknown."""
    entry = init_integration
    entity_id = await _setup_switch_device(hass, entry, entity_registry)

    # send returns False when address is unknown
    mock_echonet_lite_client.send.return_value = False

    with (
        pytest.raises(HomeAssistantError) as exc_info,
        patch.object(
            entry.runtime_data.property_poller,
            "schedule_immediate_poll",
        ),
    ):
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": entity_id},
            blocking=True,
        )
    assert exc_info.value.translation_key == "target_node_unknown"


async def _setup_readonly_switch_device(
    hass: HomeAssistant,
    entry,
    entity_registry: er.EntityRegistry,
) -> str:
    """Set up a switch device where EPC 0x80 is NOT in the SET property map."""
    coordinator = entry.runtime_data.coordinator

    node_id = "010108"
    eoj = EOJ(0x027901)  # Fuel cell instance 1 (not air cleaner)

    # EPC 0x80 NOT in SET property map (0x9E is empty), but in GET property map
    entry.runtime_data.client.get.return_value = [
        TestProperty(epc=0x9E, edt=b"\x00"),  # SET property map: empty
        TestProperty(epc=0x9F, edt=b"\x01\x80"),  # GET property map: 0x80
        TestProperty(
            epc=EPC_MANUFACTURER_CODE,
            edt=TEST_MANUFACTURER_CODE.to_bytes(3, "big"),
        ),
        TestProperty(epc=EPC_OPERATION_STATUS, edt=b"\x31"),  # off
    ]

    await coordinator.device_manager.setup_device(node_id, eoj)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(
        "switch", DOMAIN, "010108-027901-80"
    )
    assert entity_id is not None
    return entity_id


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_send_property_not_writable_raises_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that writing to a non-writable EPC raises HomeAssistantError."""
    entry = init_integration
    entity_id = await _setup_readonly_switch_device(hass, entry, entity_registry)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": entity_id},
            blocking=True,
        )
    assert exc_info.value.translation_key == "epc_not_writable"

    # Ensure no frame was actually sent
    mock_echonet_lite_client.send.assert_not_called()


@pytest.mark.usefixtures("mock_definitions_registry")
@pytest.mark.parametrize(
    "result",
    [
        pytest.param(
            SetRequestResult(
                sent=True,
                response_esv=ESV.SETC_SNA,
                accepted_epcs=frozenset(),
                rejected_epcs=frozenset({EPC_OPERATION_STATUS}),
                unanswered_epcs=frozenset(),
            ),
            id="rejected",
        ),
        pytest.param(
            SetRequestResult(
                sent=True,
                response_esv=None,
                accepted_epcs=frozenset(),
                rejected_epcs=frozenset(),
                unanswered_epcs=frozenset({EPC_OPERATION_STATUS}),
            ),
            id="unanswered",
        ),
    ],
)
async def test_send_property_set_result_failure_raises_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
    result: SetRequestResult,
) -> None:
    """Test that rejected and unanswered SetC results raise an error."""
    entry = init_integration
    entity_id = await _setup_switch_device(hass, entry, entity_registry)
    mock_echonet_lite_client.set_properties.side_effect = None
    mock_echonet_lite_client.set_properties.return_value = result

    with (
        pytest.raises(HomeAssistantError) as exc_info,
        patch.object(
            entry.runtime_data.property_poller,
            "schedule_immediate_poll",
        ) as schedule_immediate_poll,
    ):
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": entity_id},
            blocking=True,
        )

    assert exc_info.value.translation_key == "set_failed"
    schedule_immediate_poll.assert_not_called()


def test_can_process_enum_values_with_no_duplicates() -> None:
    """Test that clean enum_values returns True."""
    entity = EntityDefinition(
        id="test_entity",
        epc=0x80,
        name_en="Test Entity",
        name_ja="テストエンティティ",
        get="required",
        set="optional",
        enum_values=(
            EnumValue(edt=0x30, key="on", name_en="ON", name_ja="オン"),
            EnumValue(edt=0x31, key="off", name_en="OFF", name_ja="オフ"),
        ),
    )

    assert can_process_enum_values(entity) is True


def test_can_process_enum_values_with_duplicate_key() -> None:
    """Test that duplicate keys returns False."""
    entity = EntityDefinition(
        id="test_entity",
        epc=0x80,
        name_en="Test Entity",
        name_ja="テストエンティティ",
        get="required",
        set="optional",
        enum_values=(
            EnumValue(edt=0x30, key="mode", name_en="Mode A", name_ja="モード A"),
            EnumValue(edt=0x31, key="mode", name_en="Mode B", name_ja="モード B"),
        ),
    )

    assert can_process_enum_values(entity) is False


def test_can_process_enum_values_with_no_enum_values() -> None:
    """Test that numeric entities (no enum_values) return True."""
    entity = EntityDefinition(
        id="test_entity",
        epc=0xB0,
        name_en="Temperature",
        name_ja="温度",
        get="required",
        set="optional",
        enum_values=(),
    )

    assert can_process_enum_values(entity) is True


def test_can_process_enum_values_with_many_unique_values() -> None:
    """Test that many unique enum values returns True."""
    entity = EntityDefinition(
        id="test_entity",
        epc=0xA0,
        name_en="Mode",
        name_ja="モード",
        get="required",
        set="optional",
        enum_values=(
            EnumValue(edt=0x41, key="auto", name_en="Auto", name_ja="自動"),
            EnumValue(edt=0x42, key="cooling", name_en="Cooling", name_ja="冷房"),
            EnumValue(edt=0x43, key="heating", name_en="Heating", name_ja="暖房"),
            EnumValue(edt=0x44, key="dehumidify", name_en="Dehumidify", name_ja="除湿"),
        ),
    )

    assert can_process_enum_values(entity) is True


def test_infer_platform_write_only_with_enum() -> None:
    """Test that write-only entities with enum values map to button platform."""
    entity = EntityDefinition(
        id="class_0002_epc_bf",
        epc=0xBF,
        name_en="Invasion occurrence status resetting",
        name_ja="侵入発生状態リセット設定",
        get="notApplicable",
        set="optional",
        enum_values=(
            EnumValue(edt=0x00, key="reset", name_en="Reset", name_ja="リセット"),
        ),
    )

    assert infer_platform(entity) == "button"


def test_infer_platform_write_only_numeric() -> None:
    """Test that write-only numeric entities are skipped (no button).

    Button platform requires exactly 1 enum value (action command).
    Write-only numeric properties without enum_values are skipped.
    """
    entity = EntityDefinition(
        id="test_write_only_numeric",
        epc=0xD0,
        name_en="Test write-only numeric",
        name_ja="テスト書き込み専用数値",
        get="notApplicable",
        set="optional",
        format="uint8",
        enum_values=(),
    )

    assert infer_platform(entity) is None


def test_infer_platform_readable_with_single_enum() -> None:
    """Test that readable entities with single enum value are skipped.

    Single enum values on readable properties are not processable for any
    platform (binary_sensor needs 2, select needs 3+).
    """
    entity = EntityDefinition(
        id="test_readable_single_enum",
        epc=0xE0,
        name_en="Test readable with single enum",
        name_ja="テスト読み取り単一列挙",
        get="required",
        set="optional",
        enum_values=(EnumValue(edt=0x00, key="value", name_en="Value", name_ja="値"),),
    )

    assert infer_platform(entity) is None


def test_infer_platform_two_value_non_boolean_is_select() -> None:
    """Two enum values without true/false keys remain a select."""
    entity = EntityDefinition(
        id="test_two_value_mode",
        epc=0xE0,
        name_en="Test mode",
        name_ja="テストモード",
        get="required",
        set="optional",
        enum_values=(
            EnumValue(edt=0x41, key="heating", name_en="Heating", name_ja="暖房"),
            EnumValue(edt=0x42, key="cooling", name_en="Cooling", name_ja="冷房"),
        ),
    )

    assert infer_platform(entity) is Platform.SELECT


def test_infer_platform_numeric_value_only_is_skipped() -> None:
    """Readable MRA ``numericValue`` properties (no format, no enum_values) are skipped.

    E.g. EPC 0xC2 "Unit for cumulative amounts of electric energy": its EDT
    byte selects a multiplier consumed internally via other entities'
    ``coefficient_epcs``, not surfaced as its own entity.
    """
    entity = EntityDefinition(
        id="class_0287_epc_c2",
        epc=0xC2,
        name_en="Unit for cumulative amounts of electric energy",
        name_ja="積算電力量単位",
        get="required",
        set="notApplicable",
    )

    assert infer_platform(entity) is None


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_entity_becomes_unavailable_on_liveness_poll_failure(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Entities go unavailable after their liveness poll is abandoned."""
    entry = init_integration
    entity_id = await _setup_switch_device(hass, entry, entity_registry)

    coordinator = entry.runtime_data.coordinator
    monitor = entry.runtime_data.issue_monitor
    node = coordinator.data["010106-027901"]

    assert node.polling_available is None
    node.polling_available = False
    monitor._async_check_runtime(dt_util.utcnow())
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "unavailable"

    node.polling_available = True
    monitor._async_check_runtime(dt_util.utcnow())
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state != "unavailable"


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_entity_subscribes_its_epc_on_add(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """An added entity's EPC is included in effective_poll_epcs (Step 6)."""
    entry = init_integration
    await _setup_switch_device(hass, entry, entity_registry)

    device_manager = entry.runtime_data.coordinator.device_manager
    device_key = "010106-027901"
    assert EPC_OPERATION_STATUS in device_manager.effective_poll_epcs(device_key)


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_disabling_entity_excludes_epc_from_polling(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Disabling an entity removes its EPC from effective_poll_epcs (Step 6)."""
    entry = init_integration
    entity_id = await _setup_switch_device(hass, entry, entity_registry)

    device_manager = entry.runtime_data.coordinator.device_manager
    device_key = "010106-027901"
    assert EPC_OPERATION_STATUS in device_manager.effective_poll_epcs(device_key)

    entity_registry.async_update_entity(
        entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()

    assert hass.states.get(entity_id) is None
    assert EPC_OPERATION_STATUS not in device_manager.effective_poll_epcs(device_key)


@pytest.mark.usefixtures("mock_definitions_registry")
async def test_entity_disabled_before_add_excludes_epc_from_polling(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_echonet_lite_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """A pre-disabled entity still confirms an empty subscription for the device."""
    entry = init_integration

    entity_registry.async_get_or_create(
        "switch",
        DOMAIN,
        "010106-027901-80",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )

    await _setup_switch_device(hass, entry, entity_registry)

    device_manager = entry.runtime_data.coordinator.device_manager
    device_key = "010106-027901"
    assert EPC_OPERATION_STATUS not in device_manager.effective_poll_epcs(device_key)


def _make_node_state(
    *,
    manufacturer_code: int,
    get_epcs: frozenset[int] = frozenset({0xF1}),
    set_epcs: frozenset[int] = frozenset(),
):
    """Build a minimal ``NodeState`` for ``should_create`` exercises."""
    return NodeState(
        eoj=EOJ(0x013501),
        properties={},
        node_id="00000000000001",
        manufacturer_code=manufacturer_code,
        get_epcs=get_epcs,
        set_epcs=set_epcs,
        inf_epcs=frozenset(),
        poll_epcs=frozenset(),
        fast_poll_epcs=frozenset(),
        product_code=None,
        serial_number=None,
    )


def test_should_create_respects_manufacturer_code() -> None:
    """``should_create`` must honour vendor-locked entity definitions.

    pyhems definitions include vendor-specific entities (e.g. EPCs 0xF1-0xF4
    on class 0x0135 / manufacturer 0x000005) that must only materialise on
    matching nodes. Locked entities for other manufacturers must be skipped.
    """
    locked = EchonetLiteEntityDescription(
        key="f1",
        epc=0xF1,
        manufacturer_code=0x000005,
    )
    unlocked = EchonetLiteEntityDescription(
        key="f1",
        epc=0xF1,
    )

    matching = _make_node_state(manufacturer_code=0x000005)
    other = _make_node_state(manufacturer_code=0x000006)

    assert locked.should_create(matching) is True
    assert locked.should_create(other) is False
    # Without a manufacturer constraint the entity is always created when
    # the EPC is reachable.
    assert unlocked.should_create(matching) is True
    assert unlocked.should_create(other) is True

    # Missing EPC always disables creation regardless of manufacturer.
    missing_epc = _make_node_state(
        manufacturer_code=0x000005, get_epcs=frozenset(), set_epcs=frozenset()
    )
    assert locked.should_create(missing_epc) is False
    assert unlocked.should_create(missing_epc) is False


# ---------------------------------------------------------------------------
# _get_or_build_device_info
# ---------------------------------------------------------------------------


def _make_device_info_node(
    *,
    properties: dict[int, bytes] | None = None,
    product_code: str | None = "AC-1234",
    serial_number: str | None = "SN-9999",
    manufacturer_code: int = TEST_MANUFACTURER_CODE,
    class_code: int = 0x0130,
    node_id: str = "00000000000001",
) -> NodeState:
    """Build a ``NodeState`` for ``_get_or_build_device_info`` exercises."""
    return NodeState(
        eoj=EOJ((class_code << 8) | 1),
        properties=properties or {},
        node_id=node_id,
        manufacturer_code=manufacturer_code,
        get_epcs=frozenset(),
        set_epcs=frozenset(),
        inf_epcs=frozenset(),
        poll_epcs=frozenset(),
        fast_poll_epcs=frozenset(),
        product_code=product_code,
        serial_number=serial_number,
    )


def _make_coordinator_stub() -> object:
    """Minimal coordinator stub exposing only ``device_info_cache``."""

    class _CoordinatorStub:
        def __init__(self) -> None:
            self.device_info_cache: dict[str, DeviceInfo] = {}

    return _CoordinatorStub()


def test_device_info_without_installation_location() -> None:
    """No EPC 0x81: device uses class translation key, no suggested_area."""
    coordinator = _make_coordinator_stub()
    node = _make_device_info_node()

    info = _get_or_build_device_info(coordinator, node)

    assert info["identifiers"] == {(DOMAIN, node.device_key)}
    assert info["translation_key"] == "class_0130"
    assert info["translation_placeholders"] is None
    assert "name" not in info
    assert info["manufacturer"] == "SHARP CORPORATION"
    assert info["model"] == "AC-1234"
    assert info["serial_number"] == "SN-9999"
    assert info.get("suggested_area") is None


def test_device_info_class_name_independent_of_product_code() -> None:
    """``translation_key`` is based on class code regardless of ``product_code``.

    A single node exposing multiple class instances shares one product code
    but distinct class translation keys, so naming by class keeps the HA
    devices distinguishable in the UI.
    """
    coordinator = _make_coordinator_stub()
    node = _make_device_info_node(product_code=None)

    info = _get_or_build_device_info(coordinator, node)

    assert info["translation_key"] == "class_0130"
    assert info["model"] is None


def test_device_info_class_name_uses_pyhems_fallback() -> None:
    """Unknown class code falls back to ``unknown_class`` translation key."""
    coordinator = _make_coordinator_stub()
    node = _make_device_info_node(class_code=0xFFFF)

    info = _get_or_build_device_info(coordinator, node)

    assert info["translation_key"] == "unknown_class"
    assert info["translation_placeholders"] == {"class_code": "0xFFFF"}
    assert "name" not in info


def test_device_info_manufacturer_uses_pyhems_fallback() -> None:
    """Unknown manufacturer falls back to the hex code via pyhems."""
    coordinator = _make_coordinator_stub()
    node = _make_device_info_node(manufacturer_code=0xABCDEF)

    info = _get_or_build_device_info(coordinator, node)

    assert info["manufacturer"] == "0xABCDEF"


def test_device_info_with_installation_location_no_instance() -> None:
    """EPC 0x81 sets ``suggested_area`` only; translation key is unchanged."""
    coordinator = _make_coordinator_stub()
    # 0b0000_1000 -> LLLL=1 (living_room), NNN=0
    node = _make_device_info_node(properties={0x81: b"\x08"})

    info = _get_or_build_device_info(coordinator, node)

    assert info["translation_key"] == "class_0130"
    assert info["suggested_area"] == "Living room"


def test_device_info_with_installation_location_and_instance() -> None:
    """NNN>0 does not affect translation key; ``suggested_area`` is area only.

    The live LLLL/NNN values are exposed through the dedicated
    ``installation_location_code`` / ``installation_location_number`` config
    select entities, so the device name does not need to encode them.
    """
    coordinator = _make_coordinator_stub()
    # 0b0001_1011 -> LLLL=3 (kitchen), NNN=3
    node = _make_device_info_node(properties={0x81: b"\x1b"})

    info = _get_or_build_device_info(coordinator, node)

    assert info["translation_key"] == "class_0130"
    assert info["suggested_area"] == "Kitchen"


def test_device_info_is_cached_by_device_key() -> None:
    """A second call returns the exact same ``DeviceInfo`` instance."""
    coordinator = _make_coordinator_stub()
    node = _make_device_info_node()

    first = _get_or_build_device_info(coordinator, node)
    second = _get_or_build_device_info(coordinator, node)

    assert first is second
    # Mutating the node's properties after caching must not rebuild the entry.
    node.properties[0x81] = b"\x08"
    third = _get_or_build_device_info(coordinator, node)
    assert third is first
    assert third.get("suggested_area") is None
