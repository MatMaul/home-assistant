"""The RF Generic component.

Generic RF-controlled devices (fans, lights, ...) driven by user-supplied
RF codes. Configuration is YAML-only and supports multiple platforms; each
platform entry is forwarded to the corresponding platform module via
``discovery.async_load_platform``.
"""

import asyncio
from collections.abc import Coroutine
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .fan import FAN_SCHEMA

_LOGGER = logging.getLogger(__name__)

PLATFORM_MAPPING = {
    FAN_DOMAIN: Platform.FAN,
}

COMBINED_SCHEMA = vol.Schema(
    {
        vol.Optional(FAN_DOMAIN): vol.All(cv.ensure_list, [FAN_SCHEMA]),
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN): vol.All(
            cv.ensure_list,
            [COMBINED_SCHEMA],
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the RF Generic integration from YAML config."""
    await async_load_platforms(hass, config.get(DOMAIN, []), config)
    return True


async def async_load_platforms(
    hass: HomeAssistant,
    rf_generic_config: list[dict[str, dict[str, Any]]],
    config: ConfigType,
) -> None:
    """Load platforms from yaml."""
    if not rf_generic_config:
        return

    _LOGGER.debug("Full config loaded: %s", rf_generic_config)

    load_coroutines: list[Coroutine[Any, Any, None]] = []
    for platform_config in rf_generic_config:
        for platform, _config in platform_config.items():
            if platform not in PLATFORM_MAPPING:
                _LOGGER.warning("Unsupported platform %s for %s", platform, DOMAIN)
                continue
            _LOGGER.debug(
                "Loading config %s for platform %s",
                platform_config,
                PLATFORM_MAPPING[platform],
            )
            load_coroutines.append(
                discovery.async_load_platform(
                    hass,
                    PLATFORM_MAPPING[platform],
                    DOMAIN,
                    _config,
                    config,
                )
            )

    if load_coroutines:
        await asyncio.gather(*load_coroutines)
