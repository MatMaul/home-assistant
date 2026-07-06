"""Utility functions for the RF Generic integration."""

from rf_protocols import RadioFrequencyCommand
from rf_protocols.commands.ook import OOKCommand
from rf_protocols.parser import parse_sub_content

from homeassistant.components.rf_generic.const import DEFAULT_FREQUENCY_HZ


def parse_code(code: str) -> RadioFrequencyCommand:
    """Parse a space separated timings list or a Flipper ``.sub`` file content string into a command."""
    try:
        timings = [int(c) for c in code.split()]
        return OOKCommand(frequency=DEFAULT_FREQUENCY_HZ, timings=timings)
    except ValueError:
        return parse_sub_content(code)
