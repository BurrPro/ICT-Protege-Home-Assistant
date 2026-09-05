"""Momentary door activation using the controller's Lock Activation Time."""
import time
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from .const import DOMAIN, CONF_DOORS, DOOR_TYPE_TOGGLE, get_door_type
from .entity import ICTEntity

DOOR_UNLOCK_TIMED = 0x01
PRESS_GUARD_SECONDS = 2.0


async def async_setup_entry(hass, entry, async_add_entities):
    client = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ICTDoorToggle(client, int(k), name)
        for k, name in entry.options.get(CONF_DOORS, {}).items()
        if get_door_type(entry.options, int(k)) == DOOR_TYPE_TOGGLE])


class ICTDoorToggle(ICTEntity, ButtonEntity):
    _attr_icon = "mdi:garage"

    def __init__(self, client, record_id, name):
        super().__init__(client, record_id, f"{name} Toggle", "door", "_toggle")
        self._device_name = name
        self._press_pending = False
        self._next_press = 0.0

    def _apply_update(self, update):
        # Door position belongs to the existing contact sensor, not this button.
        pass

    async def async_press(self):
        if not self.available:
            raise HomeAssistantError("Door status is unavailable; toggle was not sent")
        if self._press_pending or time.monotonic() < self._next_press:
            raise HomeAssistantError("Please wait before toggling the door again")
        if not self._client.cache[("door", self._record_id)]["locked"]:
            raise HomeAssistantError("Door lock output is already active; wait for it to release")
        # Set the guard before awaiting so concurrent presses cannot queue pulses.
        self._press_pending = True
        try:
            await self._command(1, DOOR_UNLOCK_TIMED)
        finally:
            # Retain the guard after failures as the physical outcome may be unknown.
            self._next_press = time.monotonic() + PRESS_GUARD_SECONDS
            self._press_pending = False
