"""Fan platform for the RF Generic integration.

Supports on/off fans and optional speed control, configurable per fan:

* **On/off only** -- just ``on``/``off`` codes; no speed features advertised.

* **Direct speed control** -- ``speed_steps`` maps each hardware level to a
  specific RF code. Setting a percentage sends the code for the matching
  level directly.

* **Calibration-based control** -- ``calibrate`` provides a ``plus`` code
  (required) and an optional ``minus`` code, plus a ``speed_count``. Setting a
  level first snaps the device to a known end of the range (via optional
  ``reset_high``/``reset_low`` codes, or ``speed_count`` minus presses as a
  legacy fallback) then climbs to the target. When ``minus`` is omitted the
  device is assumed to wrap around (pressing ``plus`` past the top returns
  to 0), so absolute targeting requires a reset code. Increase/decrease
  services send relative presses without recalibration.

When speed control is configured, on/off is handled via dedicated
``on``/``off`` codes when provided, otherwise derived from the speed codes
(level 0 = off, level 1 = on).
"""

import logging
import math
from typing import Any, override

from rf_protocols import RadioFrequencyCommand
import voluptuous as vol

from homeassistant.components.fan import ATTR_PERCENTAGE, FanEntity, FanEntityFeature
from homeassistant.components.radio_frequency import async_send_command
from homeassistant.components.rf_generic.util import parse_code
from homeassistant.const import CONF_NAME, CONF_UNIQUE_ID, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from .calibration import CalibrationConfig, run_calibration
from .const import (
    CONF_OFF_CODE,
    CONF_ON_CODE,
    CONF_SPEED_CALIBRATE,
    CONF_SPEED_COUNT,
    CONF_SPEED_MINUS,
    CONF_SPEED_PLUS,
    CONF_SPEED_RESET_HIGH,
    CONF_SPEED_RESET_LOW,
    CONF_SPEED_STEPS,
    CONF_TRANSMITTER,
    DEFAULT_SPEED_COUNT,
)
from .entity import RFGenericEntity

_LOGGER = logging.getLogger(__name__)


# Schema for an optional calibration block. ``plus`` is required and
# drives wrap-around speed cycling when ``minus`` is omitted; ``minus``
# enables down-climbing from a ``reset_high``. ``reset_high`` and
# ``reset_low`` are optional single codes that snap the device to a known
# end of the range before climbing to the target level.
SPEED_CALIBRATE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SPEED_PLUS): cv.string,
        vol.Optional(CONF_SPEED_MINUS): cv.string,
        vol.Optional(CONF_SPEED_COUNT, default=DEFAULT_SPEED_COUNT): cv.positive_int,
        vol.Optional(CONF_SPEED_RESET_HIGH): cv.string,
        vol.Optional(CONF_SPEED_RESET_LOW): cv.string,
    }
)

# Fan platform entry.
FAN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TRANSMITTER): cv.string,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        # Direct speed control: ordered list of RF codes for speed levels.
        vol.Optional(CONF_SPEED_STEPS): [cv.string],
        # Calibration-based control: relative +/- codes.
        vol.Optional(CONF_SPEED_CALIBRATE): SPEED_CALIBRATE_SCHEMA,
        # On/off codes (optional; on/off can also be derived from speed 0/1).
        vol.Optional(CONF_ON_CODE): cv.string,
        vol.Optional(CONF_OFF_CODE): cv.string,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: list[ConfigType] | None = None,
) -> None:
    """Set up the RF Generic fan platform from YAML discovery info."""
    if discovery_info is None:
        _LOGGER.error(
            "rf_generic fan platform requires YAML configuration; "
            "config entries are not supported"
        )
        return
    async_add_entities([RFGenericFan(fan_config) for fan_config in discovery_info])


class RFGenericFan(RFGenericEntity, FanEntity, RestoreEntity):
    """RF-controlled fan supporting on/off and optional speed control."""

    def __init__(self, config: ConfigType) -> None:
        """Initialize the fan."""
        transmitter: str = config[CONF_TRANSMITTER]
        name: str = config.get(CONF_NAME, "RF Fan")
        unique_id: str | None = config.get(CONF_UNIQUE_ID)
        if unique_id is None:
            unique_id = f"{transmitter}_{name}"

        super().__init__(transmitter, unique_id, name)

        self._attr_supported_features = 0

        # Direct speed control: level -> RF command.
        self._speed_commands: list[RadioFrequencyCommand] | None = None
        if speed_steps := config.get(CONF_SPEED_STEPS):
            self._speed_commands = [parse_code(code) for code in speed_steps]

        # Calibration-based control: relative +/- commands. ``plus`` is
        # required; ``minus`` is optional. When ``minus`` is omitted the
        # device is assumed to wrap around (pressing plus past the top
        # returns to 0), so absolute targeting requires a reset code.
        self._plus_command: RadioFrequencyCommand | None = None
        self._minus_command: RadioFrequencyCommand | None = None
        self._reset_high_command: RadioFrequencyCommand | None = None
        self._reset_low_command: RadioFrequencyCommand | None = None
        self._speed_count: int = DEFAULT_SPEED_COUNT
        self._speed_calibration: CalibrationConfig | None = None
        if calibrate := config.get(CONF_SPEED_CALIBRATE):
            if self._speed_commands:
                raise ValueError("Cannot use both speed steps and calibrate mode")
            self._speed_calibration = CalibrationConfig.from_config(calibrate)

        # Optional dedicated on/off codes.
        self._on_command: RadioFrequencyCommand | None = None
        self._off_command: RadioFrequencyCommand | None = None
        if on_code := config.get(CONF_ON_CODE):
            self._on_command = parse_code(on_code)
            self._attr_supported_features |= FanEntityFeature.TURN_ON
        if off_code := config.get(CONF_OFF_CODE):
            self._off_command = parse_code(off_code)
            self._attr_supported_features |= FanEntityFeature.TURN_OFF

        self._has_speed_control = (
            self._speed_commands is not None or self._plus_command is not None
        )

        if not self._has_speed_control:
            if self._on_command is None or self._off_command is None:
                raise ValueError(
                    "rf_generic fan without speed control requires both 'on' "
                    "and 'off' codes"
                )
        else:
            self._attr_supported_features |= FanEntityFeature.SET_SPEED
            # Derive speed_count from the largest configured direct level when
            # calibration is not used.
            if self._speed_commands is not None and self._plus_command is None:
                self._speed_count = len(self._speed_commands)
            self._attr_speed_count = self._speed_count

        self._level = 0

    @property
    def _speed_range(self) -> tuple[int, int]:
        return (1, self._speed_count)

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the fan is currently on."""
        return self._level > 0

    @property
    @override
    def percentage(self) -> int | None:
        """Return the current speed as a percentage."""
        if not self._has_speed_control:
            return None
        if self._level == 0:
            return 0
        return ranged_value_to_percentage(self._speed_range, self._level)

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last known state."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return
        if not self._has_speed_control:
            self._level = 1 if last.state == STATE_ON else 0
            return
        last_pct = last.attributes.get(ATTR_PERCENTAGE)
        if isinstance(last_pct, (int, float)) and last_pct > 0:
            self._level = math.ceil(
                percentage_to_ranged_value(self._speed_range, last_pct)
            )

    @override
    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        if not self._has_speed_control:
            assert self._on_command is not None
            await self._async_send(self._on_command)
            self._level = 1
            self.async_write_ha_state()
            return
        if percentage is None or percentage <= 0:
            if self._on_command is not None:
                await self._async_send(self._on_command)
                self._level = 1  # or restore last level
                self.async_write_ha_state()
                return
            level = 1
        else:
            level = math.ceil(percentage_to_ranged_value(self._speed_range, percentage))
        await self._async_set_level(level)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        if not self._has_speed_control:
            assert self._off_command is not None
            await self._async_send(self._off_command)
            self._level = 0
            self.async_write_ha_state()
            return
        if self._off_command is not None:
            await self._async_send(self._off_command)
            self._level = 0
            self.async_write_ha_state()
            return
        await self._async_set_level(0)

    @override
    async def async_set_percentage(self, percentage: int) -> None:
        """Set the fan speed."""
        if percentage <= 0:
            await self.async_turn_off()
            return
        level = math.ceil(percentage_to_ranged_value(self._speed_range, percentage))
        await self._async_set_level(level)

    @override
    async def async_increase_speed(self, percentage_step: int | None = None) -> None:
        """Bump speed up by N hardware levels (no recalibration)."""
        steps = self._steps_from_percentage(percentage_step)
        if self._plus_command is None:
            # Direct control: fall back to absolute set.
            await self._async_set_level(min(self._speed_count, self._level + steps))
            return
        for _ in range(steps):
            await self._async_send(self._plus_command)
        if self._minus_command is None:
            # Wrap-around: pressing plus past the top returns to 0.
            self._level = (self._level + steps) % (self._speed_count + 1)
        else:
            self._level = min(self._speed_count, self._level + steps)
        self.async_write_ha_state()

    @override
    async def async_decrease_speed(self, percentage_step: int | None = None) -> None:
        """Bump speed down by N hardware levels (no recalibration)."""
        steps = self._steps_from_percentage(percentage_step)
        if self._minus_command is not None:
            for _ in range(steps):
                await self._async_send(self._minus_command)
            self._level = max(0, self._level - steps)
            self.async_write_ha_state()
            return
        if self._plus_command is not None:
            # Plus-only wrap-around: step down by wrapping around the top.
            wrap_steps = (self._speed_count + 1) - steps
            for _ in range(wrap_steps):
                await self._async_send(self._plus_command)
            self._level = (self._level - steps) % (self._speed_count + 1)
            self.async_write_ha_state()
            return
        # Direct control: fall back to absolute set.
        await self._async_set_level(max(0, self._level - steps))

    def _steps_from_percentage(self, percentage_step: int | None) -> int:
        """Convert a percentage step into a number of hardware level presses."""
        if percentage_step is None:
            return 1
        return math.ceil(percentage_step * self._speed_count / 100)

    async def _async_set_level(self, level: int) -> None:
        """Drive the fan to ``level``.

        Direct control sends the matching code. Calibration control first
        snaps the device to a known end of the range, then climbs to the
        target with plus/minus presses. When both ``reset_high`` and
        ``reset_low`` are configured, the one requiring fewer climb steps is
        used; when neither is configured, the device is recalibrated with
        ``speed_count`` minus presses before climbing. Plus-only (no
        ``minus``) wraps around from the top with plus presses.
        """
        if level == 0 and self._off_command is not None:
            await self._async_send(self._off_command)
            self._level = 0
            self.async_write_ha_state()
            return

        if self._speed_commands is not None:
            if level > len(self._speed_commands):
                raise ValueError(f"No RF code configured for speed level {level}")
            await self._async_send(self._speed_commands[level - 1])
        else:
            assert self._speed_calibration is not None
            await run_calibration(self._speed_calibration, level, self._async_send)

        self._level = level
        self.async_write_ha_state()

    async def _async_send(self, command: RadioFrequencyCommand) -> None:
        """Send a single RF command via the configured transmitter."""
        await async_send_command(
            self.hass, self._transmitter, command, context=self._context
        )
