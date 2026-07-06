"""Shared entity for the RF Generic integration.

Tracks the configured ``radio_frequency`` transmitter entity and reflects
its availability. Reused by the fan platform and any future platform
(e.g. light).
"""

import logging
from typing import override

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class RFGenericEntity(Entity):
    """Base entity that forwards availability from the RF transmitter."""

    _attr_assumed_state = True
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, transmitter: str, unique_id: str, name: str) -> None:
        """Initialize the entity.

        Args:
            transmitter: Entity ID (or registry UUID) of the radio_frequency
                transmitter that will send the commands.
            unique_id: Unique id for this entity.
            name: Name of the device.
        """
        self._transmitter = transmitter
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unique_id)},
            name=name,
            manufacturer="Generic",
            model="RF Device",
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to transmitter entity state changes."""
        await super().async_added_to_hass()

        transmitter_entity_id = er.async_validate_entity_id(
            er.async_get(self.hass), self._transmitter
        )

        @callback
        def _async_transmitter_state_changed(
            event: Event[EventStateChangedData],
        ) -> None:
            """Handle transmitter entity state changes."""
            new_state = event.data["new_state"]
            transmitter_available = (
                new_state is not None and new_state.state != STATE_UNAVAILABLE
            )
            if transmitter_available != self.available:
                _LOGGER.info(
                    "Transmitter %s used by %s is %s",
                    transmitter_entity_id,
                    self.entity_id,
                    "available" if transmitter_available else "unavailable",
                )
                self._attr_available = transmitter_available
                self.async_write_ha_state()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [transmitter_entity_id],
                _async_transmitter_state_changed,
            )
        )

        transmitter_state = self.hass.states.get(transmitter_entity_id)
        self._attr_available = (
            transmitter_state is not None
            and transmitter_state.state != STATE_UNAVAILABLE
        )
