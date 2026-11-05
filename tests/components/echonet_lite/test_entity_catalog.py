"""Snapshot of the entities created from pyhems definitions.

Guards the unique_id suffix (description key) and translation_key of every
generic-platform entity for every device class, so refactoring how the
entity catalog is built cannot silently rename entities.
"""

from collections import Counter
from dataclasses import replace

from pyhems import (
    REGISTRY,
    DefinitionsRegistry,
    EntityDefinition,
    EnumValue,
    TemporalKind,
)
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.echonet_lite import (
    binary_sensor,
    button,
    date,
    datetime,
    number,
    select,
    sensor,
    switch,
    time,
)
from homeassistant.components.echonet_lite.catalog import (
    CLASS_EPCS,
    build_catalog,
    infer_platform,
)
from homeassistant.const import Platform

from .conftest import get_temporal_definition


def test_generic_entity_identifiers(snapshot: SnapshotAssertion) -> None:
    """Every generic entity keeps its unique_id suffix and translation key."""
    identifiers = sorted(
        f"0x{class_code:04X} {platform.__name__.rsplit('.', 1)[1]} "
        f"{description.key} {description.translation_key} "
        f"{description.manufacturer_code}"
        for platform in (
            binary_sensor,
            button,
            date,
            datetime,
            number,
            select,
            sensor,
            switch,
            time,
        )
        for class_code, descriptions in platform._DESCRIPTIONS.items()
        for description in descriptions
    )
    assert identifiers == snapshot


def test_catalog_excludes_definitions_without_generic_entity() -> None:
    """Dedicated, duplicate-key and writable packed definitions are left out."""

    def _definition(epc: int, **kwargs: object) -> EntityDefinition:
        defaults: dict[str, object] = {
            "id": f"class_0130_epc_{epc:02x}",
            "epc": epc,
            "name_en": "x",
            "name_ja": "x",
            "readable": True,
            "writable": False,
            "format": "uint8",
        }
        return EntityDefinition(**(defaults | kwargs))  # type: ignore[arg-type]

    duplicate_keys = (
        EnumValue(edt=0x41, key="a", name_en="A", name_ja="A"),
        EnumValue(edt=0x42, key="a", name_en="B", name_ja="B"),
        EnumValue(edt=0x43, key="c", name_en="C", name_ja="C"),
    )
    registry = DefinitionsRegistry(
        version="test",
        mra_version="test",
        devices={},
        entities={
            0x0130: (
                _definition(0xB3),  # dedicated to climate
                _definition(0xC0, format=None, enum_values=duplicate_keys),
                _definition(0xC1, writable=True, byte_offset=1),
                _definition(0xC2, writable=True),
                _definition(0xC3),
            )
        },
        manufacturers={},
    )

    catalog = build_catalog(registry)

    assert [(entry.entity_def.epc, entry.platform) for entry in catalog[0x0130]] == [
        (0xC2, Platform.NUMBER),
        (0xC3, Platform.SENSOR),
    ]


@pytest.mark.parametrize(
    ("class_code", "epc", "platform"),
    [
        (0x0130, 0x97, Platform.TIME),
        (0x0130, 0x98, Platform.DATE),
        (0x0130, 0x92, Platform.NUMBER),
        (0x03D3, 0xDB, Platform.SENSOR),
        (0x0279, 0xB1, Platform.SENSOR),
        (0x0272, 0xDA, None),
        (0x02A7, 0xCD, Platform.DATETIME),
    ],
)
def test_mapping(class_code: int, epc: int, platform: Platform | None) -> None:
    """Writable unions are excluded even when the real device cannot SET."""
    entity = get_temporal_definition(class_code, epc)
    assert infer_platform(entity) == platform


def test_complete_scalar_inventory() -> None:
    """All 50 device scalar temporal definitions follow the agreed mapping."""
    definitions = {
        entity.id: entity
        for entities in REGISTRY.entities.values()
        for entity in entities
        if entity.temporal is not None
    }
    assert len(definitions) == 50
    assert Counter(infer_platform(entity) for entity in definitions.values()) == {
        Platform.TIME: 21,
        Platform.NUMBER: 13,
        Platform.DATE: 5,
        Platform.DATETIME: 1,
        Platform.SENSOR: 9,
        None: 1,
    }
    assert 0xDA not in CLASS_EPCS[0x0272]
    assert not {0xE1, 0xE5, 0xE9} & CLASS_EPCS[0x03D3]
    sensors = [
        entity
        for entity in definitions.values()
        if infer_platform(entity) is Platform.SENSOR
    ]
    assert Counter(
        temporal.kind for entity in sensors if (temporal := entity.temporal) is not None
    ) == {TemporalKind.DURATION: 5, TemporalKind.DATE: 3, TemporalKind.DATETIME: 1}


def test_get_only_clock_and_structures_are_excluded() -> None:
    """Readonly clocks are not strings, timestamps, or readonly setters."""
    assert (
        infer_platform(replace(get_temporal_definition(0x0130, 0x97), writable=False))
        is None
    )
    for class_code, epcs in (
        (0x02A6, (0xB9,)),
        (0x027C, (0xD1,)),
        (0x02A7, (0xCB, 0xCE, 0xD7)),
    ):
        assert not {entity.epc for entity in REGISTRY.entities[class_code]} & set(epcs)
