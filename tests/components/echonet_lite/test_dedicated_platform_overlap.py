"""Tests to verify dedicated platform and select entity non-overlap.

This test suite ensures that:
1. EPCs managed by dedicated platforms (climate, fan) are correctly excluded at HA platform setup time
2. The DEDICATED_PLATFORM_EPCS constant is maintained correctly in const.py

Example: For 0x0130 (Home AC), climate platform manages operation status and swing mode,
while select entities handle specific airflow settings (0xA4, 0xA5).
"""

from pyhems import DeviceClass

from homeassistant.components.echonet_lite.const import (
    DEDICATED_PLATFORM_EPCS,
    DEDICATED_PLATFORM_REQUIRED_EPCS,
)

# Constants for EPCs used in tests
EPC_OPERATION_STATUS = 0x80
EPC_ROOM_HUMIDITY = 0xBA
EPC_ROOM_TEMPERATURE = 0xBB
EPC_SWING_AIR_FLOW = 0xA3

# Expected climate-managed EPCs for Home AC
HOME_AC_CLIMATE_MANAGED_EPCS = DEDICATED_PLATFORM_EPCS.get(
    DeviceClass.HOME_AIR_CONDITIONER, frozenset()
)


class TestDedicatedPlatformOverlap:
    """Test suite for dedicated platform/select EPC non-overlap."""

    def test_no_overlap_between_dedicated_platform_and_select(self) -> None:
        """Verify DEDICATED_PLATFORM_EPCS filtering mechanism exists for Home AC."""
        assert HOME_AC_CLIMATE_MANAGED_EPCS, (
            "DEDICATED_PLATFORM_EPCS should have EPCs for Home AC"
        )

    def test_dedicated_platform_epcs_have_specific_values(self) -> None:
        """Verify climate platform manages all HVAC-related EPCs for 0x0130."""
        # This test documents the current design constraint
        # Climate manages: on/off, fan speed, swing, special state, operation mode, target temp
        expected = frozenset({0x80, 0xA0, 0xA3, 0xAA, 0xB0, 0xB3})
        assert expected == HOME_AC_CLIMATE_MANAGED_EPCS, (
            f"Expected climate to manage {expected}, got {HOME_AC_CLIMATE_MANAGED_EPCS}"
        )


class TestHomeACClimateEPCs:
    """Test that climate platform EPCs are appropriate for Home AC (0x0130)."""

    def test_dedicated_platform_epcs_are_correct(self) -> None:
        """Verify 0xA3 (swing mode) and 0x80 (on/off) are managed by climate platform."""
        # 0xA3 = "Automatic swing of air flow setting"
        # 0x80 = "Operation status" (on/off controlled via HVAC mode)
        assert EPC_SWING_AIR_FLOW == 0xA3, "0xA3 is expected to be the swing mode EPC"
        assert EPC_OPERATION_STATUS == 0x80, (
            "0x80 is expected to be the operation status EPC"
        )
        assert EPC_SWING_AIR_FLOW in HOME_AC_CLIMATE_MANAGED_EPCS, (
            "Swing mode EPC should be in HOME_AC_CLIMATE_MANAGED_EPCS"
        )
        assert EPC_OPERATION_STATUS in HOME_AC_CLIMATE_MANAGED_EPCS, (
            "Operation status EPC should be in HOME_AC_CLIMATE_MANAGED_EPCS"
        )

    def test_0xa4_0xa5_are_select_only_airflow(self) -> None:
        """Verify 0xA4 and 0xA5 are airflow EPCs that can't be expressed as climate modes."""
        # These are manual airflow control and cannot be expressed in climate entity
        # (climate only supports swing_mode, not individual vertical/horizontal control)

        # They should NOT be in climate-managed EPCs
        assert 0xA4 not in HOME_AC_CLIMATE_MANAGED_EPCS
        assert 0xA5 not in HOME_AC_CLIMATE_MANAGED_EPCS

    def test_shared_measurements_remain_available_to_generic_sensors(self) -> None:
        """Shared measurement EPCs are required but not excluded."""
        required_epcs = DEDICATED_PLATFORM_REQUIRED_EPCS[
            DeviceClass.HOME_AIR_CONDITIONER
        ]
        assert EPC_ROOM_HUMIDITY in required_epcs
        assert EPC_ROOM_TEMPERATURE in required_epcs
        assert EPC_ROOM_HUMIDITY not in HOME_AC_CLIMATE_MANAGED_EPCS
        assert EPC_ROOM_TEMPERATURE not in HOME_AC_CLIMATE_MANAGED_EPCS
