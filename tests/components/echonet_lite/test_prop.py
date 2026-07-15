"""Tests for the ECHONET Lite prop module (BinaryProp, NumericProp, EnumProp)."""

from unittest.mock import MagicMock, patch

from pyhems import (
    EOJ,
    BinaryCodec,
    EntityDefinition,
    EnumCodec,
    NodeState,
    NumericCodec,
    Property,
    get_codec_for_epc,
)
import pytest

from homeassistant.components.echonet_lite.prop import BinaryProp, EnumProp, NumericProp
from homeassistant.const import PRECISION_HALVES, PRECISION_TENTHS, PRECISION_WHOLE

_BINARY_CODEC = BinaryCodec(on_edt=0x30, off_edt=0x31)
_NUMERIC_CODEC = NumericCodec(
    mra_format="uint8", scale=1.0, minimum=None, maximum=None, byte_offset=0
)
_NUMERIC_CODEC_BOUNDED = NumericCodec(
    mra_format="uint8", scale=1.0, minimum=16, maximum=30, byte_offset=0
)
_ENUM_CODEC = EnumCodec.from_mapping({"auto": 0x41, "cooling": 0x42, "heating": 0x43})


def _node_with_props(
    properties: dict[int, bytes], eoj: EOJ = EOJ(0x013001)
) -> NodeState:
    """Build a minimal NodeState with the given properties dict."""
    return NodeState(
        eoj=eoj,
        properties=properties,
        node_id="000000000001",
        manufacturer_code=0,
        get_epcs=frozenset(),
        set_epcs=frozenset(),
        inf_epcs=frozenset(),
        poll_epcs=frozenset(),
        fast_poll_epcs=frozenset(),
        product_code=None,
        serial_number=None,
    )


# --- get() -------------------------------------------------------


def test_base_prop_get_returns_decoded_value() -> None:
    """get() decodes and returns value when EPC is present."""
    prop = BinaryProp(epc=0x80, codec=_BINARY_CODEC)
    node = _node_with_props({0x80: b"\x30"})
    assert prop.get(node) is True


def test_base_prop_get_returns_false_for_off_edt() -> None:
    """get() returns False for the off EDT byte."""
    prop = BinaryProp(epc=0x80, codec=_BINARY_CODEC)
    node = _node_with_props({0x80: b"\x31"})
    assert prop.get(node) is False


def test_base_prop_get_returns_none_when_epc_absent() -> None:
    """get() returns None when the EPC is not in the node properties dict."""
    prop = BinaryProp(epc=0x80, codec=_BINARY_CODEC)
    node = _node_with_props({})
    assert prop.get(node) is None


def test_base_prop_get_numeric() -> None:
    """get() returns the decoded numeric value."""
    prop = NumericProp(epc=0xB0, codec=_NUMERIC_CODEC)
    node = _node_with_props({0xB0: b"\x1c"})  # 28 decimal
    assert prop.get(node) == 28


def test_numeric_prop_get_resolves_coefficient_via_node() -> None:
    """get() passes ``node`` through so coefficient-bearing EPCs resolve.

    Uses class 0x0287 EPC 0xC0 (cumulative energy), whose unit is looked up
    from the current value of sibling EPC 0xC2 rather than a static scale.
    """
    codec = get_codec_for_epc(0x0287, 0xC0)
    assert isinstance(codec, NumericCodec)
    assert codec.coefficient_epcs == (0xC2,)

    prop = NumericProp(epc=0xC0, codec=codec)
    node = _node_with_props(
        {0xC0: b"\x00\x00\x00\x0a", 0xC2: b"\x02"},  # 10 * unit(0.01) = 0.1
        eoj=EOJ(0x028701),
    )
    assert prop.get(node) == pytest.approx(0.1)


def test_numeric_prop_get_returns_none_when_coefficient_epc_unknown() -> None:
    """get() returns None when the coefficient EPC has not been read yet."""
    codec = get_codec_for_epc(0x0287, 0xC0)
    prop = NumericProp(epc=0xC0, codec=codec)
    node = _node_with_props({0xC0: b"\x00\x00\x00\x0a"}, eoj=EOJ(0x028701))
    assert prop.get(node) is None


def test_base_prop_get_enum() -> None:
    """get() returns the decoded enum key."""
    prop = EnumProp(epc=0xA0, codec=_ENUM_CODEC)
    node = _node_with_props({0xA0: b"\x41"})  # "auto"
    assert prop.get(node) == "auto"


# --- make_property() --------------------------------------------


def test_base_prop_make_property_binary_true() -> None:
    """make_property() encodes True to the correct EPC/EDT pair."""
    prop = BinaryProp(epc=0x80, codec=_BINARY_CODEC)
    result = prop.make_property(True)
    assert isinstance(result, Property)
    assert result.epc == 0x80
    assert result.edt == b"\x30"


def test_base_prop_make_property_binary_false() -> None:
    """make_property() encodes False to the correct EPC/EDT pair."""
    prop = BinaryProp(epc=0x80, codec=_BINARY_CODEC)
    result = prop.make_property(False)
    assert result.epc == 0x80
    assert result.edt == b"\x31"


def test_base_prop_make_property_numeric() -> None:
    """make_property() encodes a numeric value."""
    prop = NumericProp(epc=0xB0, codec=_NUMERIC_CODEC)
    result = prop.make_property(28)
    assert result.epc == 0xB0
    assert result.edt == b"\x1c"


def test_base_prop_make_property_enum() -> None:
    """make_property() encodes an enum key."""
    prop = EnumProp(epc=0xA0, codec=_ENUM_CODEC)
    result = prop.make_property("cooling")
    assert result.epc == 0xA0
    assert result.edt == b"\x42"


# --- BinaryProp factory methods -------------------------------------------


def test_binary_prop_from_registry_success() -> None:
    """from_registry() builds a BinaryProp when get_codec_for_epc returns BinaryCodec."""
    with patch(
        "homeassistant.components.echonet_lite.prop.get_codec_for_epc",
        return_value=_BINARY_CODEC,
    ):
        prop = BinaryProp.from_registry(0x0130, 0x80)
    assert prop.epc == 0x80
    assert prop.codec is _BINARY_CODEC


def test_binary_prop_from_registry_wrong_type_raises() -> None:
    """from_registry() raises TypeError when the codec is not BinaryCodec."""
    with (
        patch(
            "homeassistant.components.echonet_lite.prop.get_codec_for_epc",
            return_value=_ENUM_CODEC,
        ),
        pytest.raises(TypeError, match="BinaryCodec"),
    ):
        BinaryProp.from_registry(0x0130, 0x80)


def test_binary_prop_from_entity_def_success() -> None:
    """from_entity_def() builds a BinaryProp when get_codec returns BinaryCodec."""
    entity_def = MagicMock(spec=EntityDefinition)
    entity_def.epc = 0x80
    with patch(
        "homeassistant.components.echonet_lite.prop.get_codec",
        return_value=_BINARY_CODEC,
    ):
        prop = BinaryProp.from_entity_def(entity_def)
    assert prop.epc == 0x80
    assert prop.codec is _BINARY_CODEC


def test_binary_prop_from_entity_def_wrong_type_raises() -> None:
    """from_entity_def() raises TypeError when the codec is not BinaryCodec."""
    entity_def = MagicMock(spec=EntityDefinition)
    entity_def.epc = 0x80
    with (
        patch(
            "homeassistant.components.echonet_lite.prop.get_codec",
            return_value=_NUMERIC_CODEC,
        ),
        pytest.raises(TypeError, match="BinaryCodec"),
    ):
        BinaryProp.from_entity_def(entity_def)


# --- NumericProp factory methods ------------------------------------------


def test_numeric_prop_from_registry_success() -> None:
    """from_registry() builds a NumericProp when get_codec_for_epc returns NumericCodec."""
    with patch(
        "homeassistant.components.echonet_lite.prop.get_codec_for_epc",
        return_value=_NUMERIC_CODEC,
    ):
        prop = NumericProp.from_registry(0x0130, 0xB0)
    assert prop.epc == 0xB0
    assert prop.codec is _NUMERIC_CODEC


def test_numeric_prop_from_registry_wrong_type_raises() -> None:
    """from_registry() raises TypeError when the codec is not NumericCodec."""
    with (
        patch(
            "homeassistant.components.echonet_lite.prop.get_codec_for_epc",
            return_value=_BINARY_CODEC,
        ),
        pytest.raises(TypeError, match="NumericCodec"),
    ):
        NumericProp.from_registry(0x0130, 0xB0)


def test_numeric_prop_from_entity_def_success() -> None:
    """from_entity_def() builds a NumericProp when get_codec returns NumericCodec."""
    entity_def = MagicMock(spec=EntityDefinition)
    entity_def.epc = 0xB0
    with patch(
        "homeassistant.components.echonet_lite.prop.get_codec",
        return_value=_NUMERIC_CODEC,
    ):
        prop = NumericProp.from_entity_def(entity_def)
    assert prop.epc == 0xB0
    assert prop.codec is _NUMERIC_CODEC


def test_numeric_prop_from_entity_def_wrong_type_raises() -> None:
    """from_entity_def() raises TypeError when the codec is not NumericCodec."""
    entity_def = MagicMock(spec=EntityDefinition)
    entity_def.epc = 0xB0
    with (
        patch(
            "homeassistant.components.echonet_lite.prop.get_codec",
            return_value=_ENUM_CODEC,
        ),
        pytest.raises(TypeError, match="NumericCodec"),
    ):
        NumericProp.from_entity_def(entity_def)


# --- EnumProp factory methods ---------------------------------------------


def test_enum_prop_from_mapping() -> None:
    """from_mapping() builds an EnumProp with the correct codec."""
    prop = EnumProp.from_mapping(0xA0, {"auto": 0x41, "cooling": 0x42})
    assert prop.epc == 0xA0
    assert prop.options == ["auto", "cooling"]


def test_enum_prop_options() -> None:
    """Options property returns list of keys in insertion order."""
    prop = EnumProp(epc=0xA0, codec=_ENUM_CODEC)
    assert prop.options == ["auto", "cooling", "heating"]


def test_enum_prop_from_registry_success() -> None:
    """from_registry() snake_cases keys and builds an EnumProp."""
    codec = EnumCodec.from_mapping({"coolMode": 0x42, "heatMode": 0x43})
    with patch(
        "homeassistant.components.echonet_lite.prop.get_codec_for_epc",
        return_value=codec,
    ):
        prop = EnumProp.from_registry(0x0130, 0xA0)
    assert prop.epc == 0xA0
    # Keys are snake_cased by camel_to_snake
    assert "cool_mode" in prop.options
    assert "heat_mode" in prop.options


def test_enum_prop_from_registry_wrong_type_raises() -> None:
    """from_registry() raises TypeError when the codec is not EnumCodec."""
    with (
        patch(
            "homeassistant.components.echonet_lite.prop.get_codec_for_epc",
            return_value=_BINARY_CODEC,
        ),
        pytest.raises(TypeError, match="EnumCodec"),
    ):
        EnumProp.from_registry(0x0130, 0xA0)


def test_enum_prop_from_entity_def_success() -> None:
    """from_entity_def() builds an EnumProp from EntityDefinition."""
    entity_def = MagicMock(spec=EntityDefinition)
    entity_def.epc = 0xA0
    codec = EnumCodec.from_mapping({"on": 0x30, "off": 0x31})
    with patch(
        "homeassistant.components.echonet_lite.prop.get_codec",
        return_value=codec,
    ):
        prop = EnumProp.from_entity_def(entity_def)
    assert prop.epc == 0xA0
    assert "on" in prop.options


def test_enum_prop_from_entity_def_wrong_type_raises() -> None:
    """from_entity_def() raises TypeError when the codec is not EnumCodec."""
    entity_def = MagicMock(spec=EntityDefinition)
    entity_def.epc = 0xA0
    with (
        patch(
            "homeassistant.components.echonet_lite.prop.get_codec",
            return_value=_NUMERIC_CODEC,
        ),
        pytest.raises(TypeError, match="EnumCodec"),
    ):
        EnumProp.from_entity_def(entity_def)


# --- NumericProp derived properties ---------------------------------------


@pytest.mark.parametrize(
    ("codec", "expected"),
    [
        pytest.param(_NUMERIC_CODEC, None, id="unbounded"),
        pytest.param(_NUMERIC_CODEC_BOUNDED, 16.0, id="bounded"),
        pytest.param(
            NumericCodec(
                mra_format="uint8", scale=0.5, minimum=10, maximum=None, byte_offset=0
            ),
            5.0,
            id="scaled",
        ),
    ],
)
def test_numeric_prop_min_value(codec: NumericCodec, expected: float | None) -> None:
    """min_value returns minimum * scale, or None when unbounded."""
    prop = NumericProp(epc=0xB3, codec=codec)
    assert prop.min_value == expected


@pytest.mark.parametrize(
    ("codec", "expected"),
    [
        pytest.param(_NUMERIC_CODEC, None, id="unbounded"),
        pytest.param(_NUMERIC_CODEC_BOUNDED, 30.0, id="bounded"),
        pytest.param(
            NumericCodec(
                mra_format="uint8", scale=0.5, minimum=None, maximum=60, byte_offset=0
            ),
            30.0,
            id="scaled",
        ),
    ],
)
def test_numeric_prop_max_value(codec: NumericCodec, expected: float | None) -> None:
    """max_value returns maximum * scale, or None when unbounded."""
    prop = NumericProp(epc=0xB3, codec=codec)
    assert prop.max_value == expected


def test_numeric_prop_step() -> None:
    """Step returns the codec scale."""
    codec = NumericCodec(
        mra_format="uint8", scale=0.5, minimum=None, maximum=None, byte_offset=0
    )
    prop = NumericProp(epc=0xB3, codec=codec)
    assert prop.step == 0.5


@pytest.mark.parametrize(
    ("scale", "expected"),
    [
        pytest.param(1.0, PRECISION_WHOLE, id="whole"),
        pytest.param(0.5, PRECISION_HALVES, id="halves"),
        pytest.param(0.1, PRECISION_TENTHS, id="tenths"),
        pytest.param(0.05, PRECISION_TENTHS, id="sub-tenths rounds to tenths"),
    ],
)
def test_numeric_prop_precision(scale: float, expected: float) -> None:
    """Precision returns the HA constant closest to the codec scale."""
    codec = NumericCodec(
        mra_format="uint8", scale=scale, minimum=None, maximum=None, byte_offset=0
    )
    prop = NumericProp(epc=0xB3, codec=codec)
    assert prop.precision == expected
