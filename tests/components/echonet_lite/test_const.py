"""Tests for the HEMS Echonet Lite integration constants module."""

from pyhems import REGISTRY, DefinitionsRegistry, DeviceClass, PropertyRole
import pytest

from homeassistant.components.echonet_lite import (
    binary_sensor,
    button,
    climate,
    cover,
    fan,
    light,
    lock,
    number,
    select,
    sensor,
    switch,
    water_heater,
)
from homeassistant.components.echonet_lite.const import (
    _ROLE_TO_ENTITY_CATEGORY,
    COLLECTION_SENSOR_PROJECTIONS,
    DEDICATED_PLATFORM_EPCS,
    DEDICATED_PLATFORM_REQUIRED_EPCS,
    EXCLUDED_EPCS_BY_CLASS,
    MRA_UNIT_TO_HA_UNIT,
    get_entity_category,
)
from homeassistant.const import EntityCategory


@pytest.fixture
def registry() -> DefinitionsRegistry:
    """Load pyhems DefinitionsRegistry."""
    return REGISTRY


def test_all_pyhems_units_are_handled(registry: DefinitionsRegistry) -> None:
    """Every unit produced by pyhems must be listed in ``MRA_UNIT_TO_HA_UNIT``.

    Units mapped to ``None`` are intentionally emitted unchanged (no HA
    equivalent, e.g. ``klux``). Units mapped to an HA constant are emitted as
    their HA equivalent.
    """
    pyhems_units: set[str] = {
        entity.unit
        for entities in registry.entities.values()
        for entity in entities
        if entity.unit
    }
    assert not (pyhems_units - MRA_UNIT_TO_HA_UNIT.keys()), (
        "pyhems introduced new units that are not listed in "
        "MRA_UNIT_TO_HA_UNIT. Add an entry (map to an HA constant, or to "
        "None for passthrough) in const.py."
    )


def test_power_saving_operation_is_not_a_configuration_entity() -> None:
    """Power-saving operation is a user-facing control."""
    entity_def = next(e for e in REGISTRY.entities[0x0130] if e.epc == 0x8F)
    assert get_entity_category(entity_def) is None


def test_class_specific_entity_category() -> None:
    """A curated STATUS role maps to the DIAGNOSTIC entity category."""
    entity_def = next(e for e in REGISTRY.entities[0x026F] if e.epc == 0xE7)
    assert get_entity_category(entity_def) is EntityCategory.DIAGNOSTIC


def test_entity_registry_enabled_default_always_true() -> None:
    """All generated generic descriptions are enabled-by-default."""
    generic_modules = (binary_sensor, button, number, select, sensor, switch)
    for module in generic_modules:
        for descriptions in module._DESCRIPTIONS.values():
            for description in descriptions:
                assert description.entity_registry_enabled_default is True


def test_role_to_entity_category_covers_every_property_role() -> None:
    """Every PropertyRole member must resolve to an EntityCategory decision."""
    assert set(_ROLE_TO_ENTITY_CATEGORY) == set(PropertyRole)


def test_dedicated_platform_epcs_matches_descriptions() -> None:
    """DEDICATED_PLATFORM_EPCS keys must exactly match the union of all dedicated platform _DESCRIPTIONS keys."""
    dedicated_modules = (climate, cover, fan, light, lock, water_heater)
    all_description_class_codes: set[int] = set()
    for module in dedicated_modules:
        all_description_class_codes |= module._DESCRIPTIONS.keys()

    dedicated_epcs_keys = set(DEDICATED_PLATFORM_EPCS.keys())
    assert all_description_class_codes == dedicated_epcs_keys, (
        "Mismatch between DEDICATED_PLATFORM_EPCS and dedicated platform _DESCRIPTIONS.\n"
        f"In DEDICATED_PLATFORM_EPCS but missing from _DESCRIPTIONS: "
        f"{[hex(c) for c in sorted(dedicated_epcs_keys - all_description_class_codes)]}\n"
        f"In _DESCRIPTIONS but missing from DEDICATED_PLATFORM_EPCS: "
        f"{[hex(c) for c in sorted(all_description_class_codes - dedicated_epcs_keys)]}"
    )


def test_dedicated_platform_required_epcs_include_shared_measurements() -> None:
    """Dedicated platforms retain polling for values also exposed generically."""
    assert set(DEDICATED_PLATFORM_REQUIRED_EPCS) == set(DEDICATED_PLATFORM_EPCS)
    for class_code, epcs in DEDICATED_PLATFORM_EPCS.items():
        assert epcs <= DEDICATED_PLATFORM_REQUIRED_EPCS[class_code]

    assert {
        0xBA,
        0xBB,
    } <= DEDICATED_PLATFORM_REQUIRED_EPCS[DeviceClass.HOME_AIR_CONDITIONER]
    assert 0xC1 in DEDICATED_PLATFORM_REQUIRED_EPCS[DeviceClass.ELECTRIC_WATER_HEATER]


def test_excluded_epcs_by_class_covers_legacy_measurement_channels() -> None:
    """Class 0x0287's legacy 0xD0-0xEF channel EPCs are all excluded."""
    excluded = EXCLUDED_EPCS_BY_CLASS[DeviceClass.POWER_DISTRIBUTION_BOARD_METERING]
    assert excluded == frozenset(range(0xD0, 0xF0))


def test_collection_sensor_projections_scoped_to_v2() -> None:
    """Only 0x0287 B3/B7/BA/BE are curated (see the v2 implementation report)."""
    result_epcs = {(p.class_code, p.result_epc) for p in COLLECTION_SENSOR_PROJECTIONS}
    assert result_epcs == {
        (DeviceClass.POWER_DISTRIBUTION_BOARD_METERING, 0xB3),
        (DeviceClass.POWER_DISTRIBUTION_BOARD_METERING, 0xB7),
        (DeviceClass.POWER_DISTRIBUTION_BOARD_METERING, 0xBA),
        (DeviceClass.POWER_DISTRIBUTION_BOARD_METERING, 0xBE),
    }


def test_collection_sensor_projections_fast_poll_only_for_instantaneous_power() -> None:
    """Only the instantaneous power lists (0xB7/0xBE) are marked fast_poll."""
    fast_poll_epcs = {
        p.result_epc for p in COLLECTION_SENSOR_PROJECTIONS if p.fast_poll
    }
    assert fast_poll_epcs == {0xB7, 0xBE}


def test_duplex_energy_projection_has_forward_and_reverse_fields() -> None:
    """0xBA's projection has exactly two item fields (forward/reverse)."""
    projection = next(p for p in COLLECTION_SENSOR_PROJECTIONS if p.result_epc == 0xBA)
    assert {f.item_field for f in projection.fields} == {
        "normalDirectionElectricEnergy",
        "reverseDirectionElectricEnergy",
    }
    assert projection.max_exposed_items == 30


def test_simplex_power_and_energy_projections_have_no_item_field() -> None:
    """0xB3/0xB7 items decode to a bare scalar, so item_field is None."""
    for epc in (0xB3, 0xB7):
        projection = next(
            p for p in COLLECTION_SENSOR_PROJECTIONS if p.result_epc == epc
        )
        assert projection.max_exposed_items == 60
        assert all(f.item_field is None for f in projection.fields)
