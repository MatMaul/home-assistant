"""Constants for the RF Generic integration."""

from typing import Final

DOMAIN: Final = "rf_generic"

DEFAULT_FREQUENCY_HZ: Final = 433_920_000

CONF_TRANSMITTER: Final = "transmitter"

# Fan platform.
CONF_ON_CODE: Final = "on_code"
CONF_OFF_CODE: Final = "off_code"
CONF_SPEED_STEPS: Final = "speed_steps"
CONF_SPEED_CALIBRATE: Final = "speed_calibrate"
CONF_SPEED_PLUS: Final = "plus"
CONF_SPEED_MINUS: Final = "minus"
CONF_SPEED_RESET_HIGH: Final = "reset_high"
CONF_SPEED_RESET_LOW: Final = "reset_low"
CONF_SPEED_COUNT: Final = "count"

DEFAULT_SPEED_COUNT: Final = 4
