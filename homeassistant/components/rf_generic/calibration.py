"""Shared calibration helpers for the RF Generic integration.

Several RF-controlled devices (fans, dimmable lights, ...) expose only
relative ``plus``/``minus`` codes instead of a direct code per level. To
target an absolute level on such devices, the integration first snaps the
device to a known end of the range (via optional ``reset_high``/``reset_low``
codes, or ``count`` minus presses as a legacy fallback) and then climbs to
the target with plus/minus presses.

When ``minus`` is omitted the device is assumed to wrap around (pressing
``plus`` past the top returns to 0), so absolute targeting requires a reset
code.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from rf_protocols import RadioFrequencyCommand

from homeassistant.components.rf_generic.util import parse_code
from homeassistant.helpers.typing import ConfigType

CONF_PLUS: Final = "plus"
CONF_MINUS: Final = "minus"
CONF_RESET_HIGH: Final = "reset_high"
CONF_RESET_LOW: Final = "reset_low"
CONF_COUNT: Final = "count"


@dataclass(frozen=True)
class CalibrationConfig:
    """Relative-code calibration configuration for a single axis.

    ``plus`` is required; ``minus`` is optional. When ``minus`` is omitted
    the device is assumed to wrap around (pressing plus past the top returns
    to 0), so absolute targeting requires a reset code.
    """

    plus: RadioFrequencyCommand
    minus: RadioFrequencyCommand | None = None
    reset_high: RadioFrequencyCommand | None = None
    reset_low: RadioFrequencyCommand | None = None
    count: int = 0

    def __post_init__(self) -> None:
        """Validate the configuration."""
        if self.minus is None and self.reset_low is None and self.reset_high is None:
            raise ValueError(
                "plus-only calibrate requires 'reset_low' or 'reset_high' "
                "to target an absolute level"
            )

    @classmethod
    def from_config(cls, config: ConfigType) -> CalibrationConfig:
        """Parse a calibration configuration from the component's configuration."""
        plus_command = parse_code(config[CONF_PLUS])
        minus_command: RadioFrequencyCommand | None = None
        if minus_code := config.get(CONF_MINUS):
            minus_command = parse_code(minus_code)
        count = config.get(CONF_COUNT, 0)
        if reset_high := config.get(CONF_RESET_HIGH):
            reset_high_command = parse_code(reset_high)
        if reset_low := config.get(CONF_RESET_LOW):
            reset_low_command = parse_code(reset_low)
        return CalibrationConfig(
            plus=plus_command,
            minus=minus_command,
            reset_high=reset_high_command,
            reset_low=reset_low_command,
            count=count,
        )


@dataclass(frozen=True)
class CalibrationPlan:
    """A planned reset + climb sequence.

    ``reset`` is the command to snap to a known end of the range, or ``None``
    when no reset code is configured (the caller must then perform the legacy
    ``count`` minus-press recalibration before climbing). ``climb`` is the
    command to press ``climb_steps`` times to reach the target level.
    """

    reset: RadioFrequencyCommand | None
    climb: RadioFrequencyCommand
    climb_steps: int


def calibration_plan(
    config: CalibrationConfig,
    level: int,
) -> CalibrationPlan:
    """Plan the reset + climb sequence for a calibration-driven set.

    Returns the plan to drive the device to ``level``. When a reset code is
    configured, the one minimizing the number of climb steps is selected.
    ``reset_high`` climbs down with ``minus`` when available, or wraps around
    with ``plus`` in plus-only mode. ``reset_low`` climbs up with ``plus``.
    When no reset code is configured, returns ``(None, plus, level)`` and the
    caller performs the legacy ``count`` minus-press recalibration before
    climbing.
    """
    plus_only = config.minus is None
    # Distance from each end of the range; reset_high wraps in plus-only.
    dist_from_low = level
    dist_from_high = config.count - level
    if plus_only:
        # Wrapping from the top costs one extra plus press (max -> 0).
        dist_from_high = level + 1

    use_reset_high = config.reset_high is not None and (
        config.reset_low is None or dist_from_high < dist_from_low
    )
    if use_reset_high:
        assert config.reset_high is not None
        if plus_only:
            return CalibrationPlan(config.reset_high, config.plus, level + 1)
        assert config.minus is not None
        return CalibrationPlan(
            config.reset_high,
            config.minus,
            config.count - level,
        )
    if config.reset_low is not None:
        return CalibrationPlan(config.reset_low, config.plus, level)
    return CalibrationPlan(None, config.plus, level)


async def run_calibration(
    config: CalibrationConfig,
    level: int,
    send: Callable[[RadioFrequencyCommand], Awaitable[None]],
) -> None:
    """Drive a calibration-controlled axis to ``level``.

    ``send`` is an awaitable taking a single ``RadioFrequencyCommand``.
    """
    plan = calibration_plan(config, level)
    if plan.reset is not None:
        await send(plan.reset)
    else:
        # Legacy recalibration: snap to 0 with count minus presses.
        assert config.minus is not None
        for _ in range(config.count):
            await send(config.minus)
    for _ in range(plan.climb_steps):
        await send(plan.climb)
