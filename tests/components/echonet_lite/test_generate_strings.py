"""Tests for generate_strings.py - HA-specific strings.json generation."""

import json
from pathlib import Path
from typing import Any

from pyhems import REGISTRY, DefinitionsRegistry, EntityDefinition, EnumValue
import pytest

from homeassistant.components.echonet_lite.const import EXCLUDED_EPCS_BY_CLASS
from homeassistant.components.echonet_lite.entity import infer_platform
from homeassistant.components.echonet_lite.scripts import generate_strings as gs
from homeassistant.components.echonet_lite.scripts.generate_strings import (
    REPORTING_STATE_NAME_MAPPING,
    _add_entity_string,
    _build_common_section,
    _build_reverse_lookup,
    _build_text_to_ref,
    _canonicalize_common_text,
    _escape_html_brackets,
    _load_common_states,
    _process_entity,
    _replace_with_references,
    _resolve_by_key_or_name,
    _resolve_by_name,
    generate_strings,
)
from homeassistant.components.echonet_lite.switch import (
    EchonetLiteSwitchEntityDescription,
)

# Path to HA strings.json
STRINGS_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "homeassistant"
    / "components"
    / "echonet_lite"
    / "strings.json"
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def registry() -> DefinitionsRegistry:
    """Load pyhems DefinitionsRegistry."""
    return REGISTRY


@pytest.fixture
def common_states() -> dict[str, str]:
    """Load common::state translations."""
    return _load_common_states()


@pytest.fixture
def reverse_lookup(common_states: dict[str, str]) -> dict[str, str]:
    """Build reverse lookup from common states."""
    return _build_reverse_lookup(common_states)


@pytest.fixture
def reporting_reverse_lookup(reverse_lookup: dict[str, str]) -> dict[str, str]:
    """Build extended reverse lookup with reporting state name mappings."""
    return {**reverse_lookup, **REPORTING_STATE_NAME_MAPPING}


@pytest.fixture
def strings() -> dict[str, Any]:
    """Load strings.json."""
    with open(STRINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


# ============================================================================
# Tests for helper functions
# ============================================================================


class TestLoadCommonStates:
    """Test _load_common_states helper function."""

    def test_load_common_states_returns_dict(self) -> None:
        """Test that common states are loaded as a dictionary."""
        states = _load_common_states()
        assert isinstance(states, dict)
        assert len(states) > 0

    def test_load_common_states_has_expected_keys(self) -> None:
        """Test that common states contain expected keys."""
        states = _load_common_states()
        for key in ("on", "off", "auto", "high", "low", "medium", "normal"):
            assert key in states


class TestResolveByKeyOrName:
    """Test _resolve_by_key_or_name helper function."""

    def test_key_match_returns_reference(self) -> None:
        """Test key match (key in common_states AND text matches)."""
        common_states = {"auto": "Auto", "on": "On", "off": "Off"}
        reverse = _build_reverse_lookup(common_states)
        assert _resolve_by_key_or_name("auto", "Auto", common_states, reverse) == (
            "[%key:common::state::auto%]"
        )

    def test_case_insensitive_key_match(self) -> None:
        """Test case-insensitive text matching via key."""
        common_states = {"on": "On", "off": "Off"}
        reverse = _build_reverse_lookup(common_states)
        assert _resolve_by_key_or_name("on", "ON", common_states, reverse) == (
            "[%key:common::state::on%]"
        )
        assert _resolve_by_key_or_name("off", "off", common_states, reverse) == (
            "[%key:common::state::off%]"
        )

    def test_key_exists_but_text_mismatch_uses_key(self) -> None:
        """Test key match (Strategy 2) when name_en doesn't match."""
        common_states = {"auto": "Auto"}
        reverse = _build_reverse_lookup(common_states)
        # key "auto" exists in common_states → Strategy 2 matches
        # even though text "Automatic" != "Auto" (Strategy 1 fails)
        assert (
            _resolve_by_key_or_name("auto", "Automatic", common_states, reverse)
            == "[%key:common::state::auto%]"
        )

    def test_name_en_match_when_key_missing(self) -> None:
        """Test name_en reverse lookup when key is not in common_states."""
        common_states = {"stopped": "Stopped"}
        reverse = _build_reverse_lookup(common_states)
        # key "no_operation" not in common_states, but name_en "Stopped" matches
        assert (
            _resolve_by_key_or_name("no_operation", "Stopped", common_states, reverse)
            == "[%key:common::state::stopped%]"
        )

    def test_no_match_returns_raw(self) -> None:
        """Test no match returns raw text."""
        common_states = {"auto": "Auto"}
        reverse = _build_reverse_lookup(common_states)
        assert (
            _resolve_by_key_or_name("cooling", "Cooling", common_states, reverse)
            == "Cooling"
        )


class TestResolveByName:
    """Test _resolve_by_name helper function."""

    def test_name_match_returns_reference(self) -> None:
        """Test name_en match returns key reference."""
        common_states = {"open": "Open", "closed": "Closed"}
        reverse = _build_reverse_lookup(common_states)
        assert _resolve_by_name("Open", reverse) == "[%key:common::state::open%]"
        assert _resolve_by_name("Closed", reverse) == ("[%key:common::state::closed%]")

    def test_case_insensitive_name_match(self) -> None:
        """Test case-insensitive name_en matching."""
        common_states = {"yes": "Yes", "no": "No"}
        reverse = _build_reverse_lookup(common_states)
        assert _resolve_by_name("YES", reverse) == "[%key:common::state::yes%]"
        assert _resolve_by_name("NO", reverse) == "[%key:common::state::no%]"

    def test_no_match_returns_raw(self) -> None:
        """Test no match returns raw text."""
        common_states = {"on": "On"}
        reverse = _build_reverse_lookup(common_states)
        assert _resolve_by_name("Detected", reverse) == "Detected"


class TestBuildReverseLookup:
    """Test _build_reverse_lookup helper function."""

    def test_builds_lowercase_mapping(self) -> None:
        """Test reverse lookup maps lowercase text to key."""
        common_states = {"auto": "Auto", "on": "On", "off": "Off"}
        reverse = _build_reverse_lookup(common_states)
        assert reverse["auto"] == "auto"
        assert reverse["on"] == "on"
        assert reverse["off"] == "off"

    def test_last_key_wins_on_duplicate(self) -> None:
        """Test last key wins when values collide in dict comprehension."""
        common_states = {"first": "Same", "second": "Same"}
        reverse = _build_reverse_lookup(common_states)
        assert reverse["same"] == "second"


class TestEscapeHtmlBrackets:
    """Test _escape_html_brackets helper function."""

    def test_escape_angle_brackets(self) -> None:
        """Test that angle brackets are escaped to square brackets."""
        assert _escape_html_brackets("<test>") == "[test]"
        assert _escape_html_brackets("<Drying course>") == "[Drying course]"

    def test_no_brackets(self) -> None:
        """Test that text without brackets is unchanged."""
        assert _escape_html_brackets("Normal text") == "Normal text"

    def test_mixed_content(self) -> None:
        """Test text with mixed content."""
        assert _escape_html_brackets("Value <min> to <max>") == "Value [min] to [max]"


class TestCommonKeyDeduplication:
    """Tests for common-key canonicalization and collision handling."""

    def test_canonicalize_common_text(self) -> None:
        """Case and hyphen/space differences are canonicalized."""
        assert _canonicalize_common_text("Reservation OFF") == "reservation off"
        assert _canonicalize_common_text("Reservation off") == "reservation off"
        assert (
            _canonicalize_common_text("System-interconnected type")
            == "system interconnected type"
        )
        assert (
            _canonicalize_common_text("System interconnected type")
            == "system interconnected type"
        )

    def test_build_common_section_merges_case_and_hyphen_space_variants(self) -> None:
        """Equivalent variants should produce one common key without _2 suffix."""
        value_counts = {
            "Reservation OFF": 5,
            "Reservation off": 4,
            "Reservation ON": 5,
            "Reservation on": 4,
            "System interconnected type": 2,
            "System-interconnected type": 2,
        }

        result = _build_common_section(value_counts)

        assert result["reservation_off"] == "Reservation OFF"
        assert result["reservation_on"] == "Reservation ON"
        assert result["system_interconnected_type"] == "System interconnected type"
        assert "reservation_off_2" not in result
        assert "reservation_on_2" not in result
        assert "system_interconnected_type_2" not in result

    def test_build_common_section_raises_on_true_key_collision(self) -> None:
        """Distinct canonical buckets that map to same key should fail fast."""
        value_counts = {
            "Charge (AA)": 2,
            "Charge AA": 2,
        }

        with pytest.raises(
            ValueError,
            match="Common key collision after canonical grouping",
        ):
            _build_common_section(value_counts)

    def test_replace_with_references_uses_canonical_text(self) -> None:
        """Canonical-equivalent variants should resolve to one common ref."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {
            "switch": {
                "entity_1": {
                    "name": "On timer reservation setting 1",
                    "state": {
                        "off": "Reservation off",
                        "on": "Reservation on",
                    },
                }
            }
        }
        common_section = {
            "reservation_off": "Reservation OFF",
            "reservation_on": "Reservation ON",
        }

        replacements = _replace_with_references(
            entity_strings,
            _build_text_to_ref(common_section),
        )

        assert replacements == 2
        assert entity_strings["switch"]["entity_1"]["state"] == {
            "off": "[%key:component::echonet_lite::common::reservation_off%]",
            "on": "[%key:component::echonet_lite::common::reservation_on%]",
        }


class TestAddEntityString:
    """Test _add_entity_string helper function."""

    def test_add_entity_without_state(self) -> None:
        """Test adding entity string without state translations."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {}
        entity = EntityDefinition(
            id="test_sensor",
            epc=0x80,
            name_en="Test Sensor",
            name_ja="テストセンサー",
            get="required",
            set="notApplicable",
            format="int16",
            unit="Celsius",
            minimum=None,
            maximum=None,
            multiple_of=0.1,
            enum_values=(),
        )
        _add_entity_string(entity_strings, "sensor", entity, None)

        assert "sensor" in entity_strings
        assert "test_sensor" in entity_strings["sensor"]
        assert entity_strings["sensor"]["test_sensor"]["name"] == "Test Sensor"
        assert "state" not in entity_strings["sensor"]["test_sensor"]

    def test_add_entity_with_state(self) -> None:
        """Test adding entity string with state translations."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {}
        entity = EntityDefinition(
            id="test_switch",
            epc=0x80,
            name_en="Test Switch",
            name_ja="テストスイッチ",
            get="required",
            set="optional",
            enum_values=(
                EnumValue(edt=0x30, key="false", name_en="OFF", name_ja="オフ"),
                EnumValue(edt=0x31, key="true", name_en="ON", name_ja="オン"),
            ),
        )
        state = {"on": "ON", "off": "OFF"}
        _add_entity_string(entity_strings, "switch", entity, state)

        assert "switch" in entity_strings
        assert "test_switch" in entity_strings["switch"]
        assert entity_strings["switch"]["test_switch"]["name"] == "Test Switch"
        assert entity_strings["switch"]["test_switch"]["state"] == {
            "on": "ON",
            "off": "OFF",
        }


class TestProcessEntity:
    """Test _process_entity helper function."""

    def test_process_binary_entity_writable_creates_switch(
        self,
        common_states: dict[str, str],
        reverse_lookup: dict[str, str],
        reporting_reverse_lookup: dict[str, str],
    ) -> None:
        """Test processing writable binary entity creates switch strings only."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {}
        entity = EntityDefinition(
            id="operation_status",
            epc=0x80,
            name_en="Operation status",
            name_ja="動作状態",
            get="required",
            set="optional",
            enum_values=(
                EnumValue(edt=0x30, key="true", name_en="ON", name_ja="オン"),
                EnumValue(edt=0x31, key="false", name_en="OFF", name_ja="オフ"),
            ),
        )
        _process_entity(
            entity_strings,
            entity,
            common_states,
            reverse_lookup,
            reporting_reverse_lookup,
        )

        # Writable binary entity creates only switch strings
        assert "switch" in entity_strings
        assert "binary_sensor" not in entity_strings
        assert "operation_status" in entity_strings["switch"]
        # ON/OFF match common::state::on/off (case-insensitive)
        assert entity_strings["switch"]["operation_status"]["state"]["on"] == (
            "[%key:common::state::on%]"
        )
        assert entity_strings["switch"]["operation_status"]["state"]["off"] == (
            "[%key:common::state::off%]"
        )

    def test_process_binary_entity_readonly_creates_binary_sensor(
        self,
        common_states: dict[str, str],
        reverse_lookup: dict[str, str],
        reporting_reverse_lookup: dict[str, str],
    ) -> None:
        """Test processing read-only binary entity creates binary_sensor strings."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {}
        entity = EntityDefinition(
            id="fault_status",
            epc=0x88,
            name_en="Fault status",
            name_ja="異常発生状態",
            get="required",
            set="notApplicable",
            enum_values=(
                EnumValue(
                    edt=0x41,
                    key="true",
                    name_en="Fault occurred",
                    name_ja="異常あり",
                ),
                EnumValue(
                    edt=0x42,
                    key="false",
                    name_en="No fault",
                    name_ja="異常なし",
                ),
            ),
        )
        _process_entity(
            entity_strings,
            entity,
            common_states,
            reverse_lookup,
            reporting_reverse_lookup,
        )

        # Read-only binary entity creates only binary_sensor strings
        assert "binary_sensor" in entity_strings
        assert "switch" not in entity_strings
        assert "fault_status" in entity_strings["binary_sensor"]
        # No common::state match → raw text
        state = entity_strings["binary_sensor"]["fault_status"]["state"]
        assert state["on"] == "Fault occurred"
        assert state["off"] == "No fault"

    def test_custom_ventilation_function_state_strings(
        self, strings: dict[str, Any]
    ) -> None:
        """The custom ventilation switch maps OFF/ON EDTs to off/on state strings."""
        state = strings["entity"]["switch"]["class_0f01_epc_c0_000006"]["state"]
        assert state["on"] == "Ventilation function ON"
        assert state["off"] == "Ventilation function OFF"

    def test_process_sensor_entity(
        self,
        common_states: dict[str, str],
        reverse_lookup: dict[str, str],
        reporting_reverse_lookup: dict[str, str],
    ) -> None:
        """Test that read-only numeric entities create sensor strings."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {}
        entity = EntityDefinition(
            id="temperature",
            epc=0xE0,
            name_en="Temperature",
            name_ja="温度",
            get="required",
            set="notApplicable",
            format="int16",
            unit="Celsius",
            multiple_of=0.1,
            enum_values=(),
        )
        _process_entity(
            entity_strings,
            entity,
            common_states,
            reverse_lookup,
            reporting_reverse_lookup,
        )

        assert "sensor" in entity_strings
        assert "temperature" in entity_strings["sensor"]
        assert entity_strings["sensor"]["temperature"]["name"] == "Temperature"

    def test_process_number_entity(
        self,
        common_states: dict[str, str],
        reverse_lookup: dict[str, str],
        reporting_reverse_lookup: dict[str, str],
    ) -> None:
        """Test that writable numeric entities create number strings."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {}
        entity = EntityDefinition(
            id="current_limit",
            epc=0x87,
            name_en="Current limit setting",
            name_ja="電流制限設定",
            get="optional",
            set="optional",
            format="uint8",
            unit="%",
            minimum=0,
            maximum=100,
            enum_values=(),
        )
        _process_entity(
            entity_strings,
            entity,
            common_states,
            reverse_lookup,
            reporting_reverse_lookup,
        )

        assert "number" in entity_strings
        assert "current_limit" in entity_strings["number"]
        assert entity_strings["number"]["current_limit"]["name"] == (
            "Current limit setting"
        )
        assert "state" not in entity_strings["number"]["current_limit"]

    def test_process_select_entity(
        self,
        common_states: dict[str, str],
        reverse_lookup: dict[str, str],
        reporting_reverse_lookup: dict[str, str],
    ) -> None:
        """Test that writable entities with >2 enum_values create select strings."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {}
        entity = EntityDefinition(
            id="operation_mode",
            epc=0xB0,
            name_en="Operation mode",
            name_ja="動作モード",
            get="required",
            set="required",
            enum_values=(
                EnumValue(
                    edt=0x41, key="automatic", name_en="Automatic", name_ja="自動"
                ),
                EnumValue(edt=0x42, key="cooling", name_en="Cooling", name_ja="冷房"),
                EnumValue(edt=0x43, key="heating", name_en="Heating", name_ja="暖房"),
            ),
        )
        _process_entity(
            entity_strings,
            entity,
            common_states,
            reverse_lookup,
            reporting_reverse_lookup,
        )

        assert "select" in entity_strings
        assert "operation_mode" in entity_strings["select"]

    def test_process_button_entity(
        self,
        common_states: dict[str, str],
        reverse_lookup: dict[str, str],
        reporting_reverse_lookup: dict[str, str],
    ) -> None:
        """Test that write-only entities create button strings."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {}
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
        _process_entity(
            entity_strings,
            entity,
            common_states,
            reverse_lookup,
            reporting_reverse_lookup,
        )

        assert "button" in entity_strings
        assert "class_0002_epc_bf" in entity_strings["button"]
        assert entity_strings["button"]["class_0002_epc_bf"]["name"] == (
            "Invasion occurrence status resetting"
        )
        # Button entities don't have state translations
        assert "state" not in entity_strings["button"]["class_0002_epc_bf"]

    def test_process_readonly_enum_creates_sensor(
        self,
        common_states: dict[str, str],
        reverse_lookup: dict[str, str],
        reporting_reverse_lookup: dict[str, str],
    ) -> None:
        """Test that read-only multi-value enum creates sensor strings with state."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {}
        entity = EntityDefinition(
            id="special_state",
            epc=0xAA,
            name_en="Special state",
            name_ja="特殊状態",
            get="required",
            set="notApplicable",
            enum_values=(
                EnumValue(edt=0x40, key="normal", name_en="Normal", name_ja="通常"),
                EnumValue(
                    edt=0x41, key="defrosting", name_en="Defrosting", name_ja="除霜"
                ),
                EnumValue(
                    edt=0x42, key="preheating", name_en="Preheating", name_ja="予熱"
                ),
            ),
        )
        _process_entity(
            entity_strings,
            entity,
            common_states,
            reverse_lookup,
            reporting_reverse_lookup,
        )

        assert "sensor" in entity_strings
        assert "select" not in entity_strings
        assert "special_state" in entity_strings["sensor"]
        state = entity_strings["sensor"]["special_state"]["state"]
        # "normal" matches common::state::normal
        assert state["normal"] == "[%key:common::state::normal%]"
        assert state["defrosting"] == "Defrosting"
        assert state["preheating"] == "Preheating"

    def test_process_entity_with_duplicate_key_skipped(
        self,
        common_states: dict[str, str],
        reverse_lookup: dict[str, str],
        reporting_reverse_lookup: dict[str, str],
    ) -> None:
        """Test that entities with duplicate keys are skipped."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {}
        entity = EntityDefinition(
            id="bad_entity_duplicate_key",
            epc=0x93,
            name_en="Bad Entity",
            name_ja="不正なエンティティ",
            get="required",
            set="optional",
            enum_values=(
                EnumValue(
                    edt=0x41,
                    key="true",
                    name_en="Not through public",
                    name_ja="公衆網未経由",
                ),
                EnumValue(
                    edt=0x42,
                    key="false",
                    name_en="Through public",
                    name_ja="公衆網経由",
                ),
                EnumValue(
                    edt=0x61,
                    key="true",
                    name_en="Communication OK (no public)",
                    name_ja="通信OK（公衆網なし）",
                ),
                EnumValue(
                    edt=0x62,
                    key="false",
                    name_en="Communication OK (possible)",
                    name_ja="通信OK（公衆網可）",
                ),
            ),
        )
        _process_entity(
            entity_strings,
            entity,
            common_states,
            reverse_lookup,
            reporting_reverse_lookup,
        )

        # Entity should be skipped - no strings added
        assert entity_strings == {}

    def test_process_binary_sensor_name_en_match(
        self,
        common_states: dict[str, str],
        reverse_lookup: dict[str, str],
        reporting_reverse_lookup: dict[str, str],
    ) -> None:
        """Test binary sensor uses name_en matching for common::state."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {}
        entity = EntityDefinition(
            id="door_status",
            epc=0xE0,
            name_en="Door open/close status",
            name_ja="ドア開閉状態",
            get="required",
            set="notApplicable",
            enum_values=(
                EnumValue(
                    edt=0x41,
                    key="true",
                    name_en="Open",
                    name_ja="開",
                ),
                EnumValue(
                    edt=0x42,
                    key="false",
                    name_en="Closed",
                    name_ja="閉",
                ),
            ),
        )
        _process_entity(
            entity_strings,
            entity,
            common_states,
            reverse_lookup,
            reporting_reverse_lookup,
        )

        assert "binary_sensor" in entity_strings
        state = entity_strings["binary_sensor"]["door_status"]["state"]
        # name_en "Open"/"Closed" match common::state::open/closed
        assert state["on"] == "[%key:common::state::open%]"
        assert state["off"] == "[%key:common::state::closed%]"

    def test_process_select_with_common_state_match(
        self,
        common_states: dict[str, str],
        reverse_lookup: dict[str, str],
        reporting_reverse_lookup: dict[str, str],
    ) -> None:
        """Test select enum values matching common::state use key references."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {}
        entity = EntityDefinition(
            id="air_flow_level",
            epc=0xA0,
            name_en="Air flow rate setting",
            name_ja="風量設定",
            get="required",
            set="required",
            enum_values=(
                EnumValue(edt=0x41, key="auto", name_en="Auto", name_ja="自動"),
                EnumValue(edt=0x42, key="low", name_en="Low", name_ja="弱"),
                EnumValue(edt=0x43, key="medium", name_en="Medium", name_ja="中"),
                EnumValue(edt=0x44, key="high", name_en="High", name_ja="強"),
            ),
        )
        _process_entity(
            entity_strings,
            entity,
            common_states,
            reverse_lookup,
            reporting_reverse_lookup,
        )

        state = entity_strings["select"]["air_flow_level"]["state"]
        assert state["auto"] == "[%key:common::state::auto%]"
        assert state["low"] == "[%key:common::state::low%]"
        assert state["medium"] == "[%key:common::state::medium%]"
        assert state["high"] == "[%key:common::state::high%]"

    def test_process_select_key_match_uses_common_state(
        self,
        common_states: dict[str, str],
        reverse_lookup: dict[str, str],
        reporting_reverse_lookup: dict[str, str],
    ) -> None:
        """Test select key match (Strategy 2) uses common::state reference."""
        entity_strings: dict[str, dict[str, dict[str, Any]]] = {}
        entity = EntityDefinition(
            id="mode_setting",
            epc=0xB0,
            name_en="Mode",
            name_ja="モード",
            get="required",
            set="required",
            enum_values=(
                EnumValue(
                    edt=0x41,
                    key="auto",
                    name_en="Automatic",
                    name_ja="自動",
                ),
                EnumValue(
                    edt=0x42,
                    key="cooling",
                    name_en="Cooling",
                    name_ja="冷房",
                ),
                EnumValue(
                    edt=0x43,
                    key="heating",
                    name_en="Heating",
                    name_ja="暖房",
                ),
            ),
        )
        _process_entity(
            entity_strings,
            entity,
            common_states,
            reverse_lookup,
            reporting_reverse_lookup,
        )

        state = entity_strings["select"]["mode_setting"]["state"]
        # "auto" key exists in common::state → Strategy 2 matches
        # even though text "Automatic" != "Auto" (Strategy 1 fails)
        assert state["auto"] == "[%key:common::state::auto%]"
        # "cooling" not in common::state at all → raw text
        assert state["cooling"] == "Cooling"


# ============================================================================
# Tests for generate_strings function
# ============================================================================


class TestGenerateStrings:
    """Test generate_strings function."""

    def test_generate_strings_has_required_sections(
        self, registry: DefinitionsRegistry
    ) -> None:
        """Test that generated strings has required sections."""
        result = generate_strings(registry)

        assert "config" in result
        assert "issues" in result
        assert "entity" in result

    def test_generate_strings_creates_switch_entities(
        self, registry: DefinitionsRegistry
    ) -> None:
        """Test that switch entities are created from binary definitions."""
        result = generate_strings(registry)

        # Should have switch section with entities
        assert "switch" in result.get("entity", {})
        switch_entities = result["entity"]["switch"]
        assert len(switch_entities) > 0

    def test_generate_strings_preserves_static_sections(
        self, registry: DefinitionsRegistry
    ) -> None:
        """Test that static sections from strings_static.json are preserved."""
        result = generate_strings(registry)

        # Check config section has expected structure from static file
        assert "step" in result.get("config", {})
        assert "user" in result["config"]["step"]


# ============================================================================
# Tests for strings.json validation
# ============================================================================


class TestStringsJsonValidation:
    """Test that strings.json has correct structure."""

    def test_strings_has_required_sections(self, strings: dict[str, Any]) -> None:
        """Test that strings.json has required sections."""
        assert "config" in strings
        assert "issues" in strings
        assert "entity" in strings

    def test_strings_entity_has_switch_section(self, strings: dict[str, Any]) -> None:
        """Test that entity section has switch platform."""
        entity_section = strings.get("entity", {})
        assert "switch" in entity_section, "Should have switch in entity strings"

    def test_strings_matches_generated_output(
        self, registry: DefinitionsRegistry, strings: dict[str, Any]
    ) -> None:
        """Test that strings.json matches what generate_strings would produce."""
        generated = generate_strings(registry)

        # Compare entity sections
        for platform in (
            "binary_sensor",
            "number",
            "select",
            "sensor",
            "switch",
        ):
            generated_entities = generated.get("entity", {}).get(platform, {})
            current_entities = strings.get("entity", {}).get(platform, {})

            assert generated_entities == current_entities, (
                f"strings.json {platform} section does not match generated output. "
                "Run 'python -m homeassistant.components.echonet_lite.scripts.generate_strings' "
                "to regenerate."
            )


# ============================================================================
# Tests for definitions-strings consistency
# ============================================================================


class TestDefinitionsStringsConsistency:
    """Test that all definitions have corresponding strings entries.

    This is the regression guard that justifies the absence of a
    ``fallback_name`` path in :class:`EchonetLiteEntityDescription`. Every
    entity definition that the runtime platforms turn into a description
    (i.e. passes both ``infer_platform`` and ``can_process_enum_values``)
    MUST have a matching ``translation_key`` entry under the corresponding
    platform in ``strings.json``. If this invariant ever breaks, the
    affected entities would surface without a name, which is exactly what
    ``fallback_name`` previously masked.
    """

    @pytest.mark.parametrize(
        "platform",
        ["binary_sensor", "button", "number", "select", "sensor", "switch"],
    )
    def test_all_definitions_have_strings(
        self,
        registry: DefinitionsRegistry,
        strings: dict[str, Any],
        platform: str,
    ) -> None:
        """Every definition routed to a platform has a strings.json entry.

        EXCLUDED_EPCS_BY_CLASS entities (e.g. class 0x0287's legacy 0xD0-0xEF)
        are intentionally skipped: they never become HA entities, so they
        have no strings entry either.
        """
        platform_strings = strings.get("entity", {}).get(platform, {})

        for class_code, entities in registry.entities.items():
            excluded = EXCLUDED_EPCS_BY_CLASS.get(class_code, frozenset())
            for entity in entities:
                if entity.epc in excluded:
                    continue
                if infer_platform(entity) != platform:
                    continue
                if not gs.can_process_enum_values(entity):
                    continue

                assert entity.id in platform_strings, (
                    f"Entity for class 0x{class_code:04X} EPC 0x{entity.epc:02X} "
                    f"id '{entity.id}' missing {platform} string entry"
                )


# ============================================================================
# Tests for entity description creation
# ============================================================================


class TestSwitchDescriptionCreation:
    """Test that switch entity descriptions can be created from definitions."""

    def test_all_switch_entities_create_valid_descriptions(
        self, registry: DefinitionsRegistry
    ) -> None:
        """Test that all switch entities can create switch descriptions."""
        for entities in registry.entities.values():
            for entity in entities:
                if infer_platform(entity) != "switch":
                    continue

                # Should not raise
                desc = EchonetLiteSwitchEntityDescription.build_from_entity_def(entity)
                assert desc is not None
                assert desc.epc == entity.epc
                assert desc.translation_key == entity.id


class TestMainEntryPoint:
    """Smoke tests for the ``main()`` CLI entry point."""

    def test_main_writes_strings_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``main()`` should write a non-empty strings.json under ECHONET_LITE_DIR."""
        monkeypatch.setattr(gs, "ECHONET_LITE_DIR", tmp_path)

        gs.main()

        output = tmp_path / "strings.json"
        assert output.is_file()
        data = json.loads(output.read_text(encoding="utf-8"))
        # Top-level keys are produced by ``generate_strings``; presence is
        # enough to detect catastrophic regressions in the CLI wiring.
        assert "common" in data
        assert "entity" in data
