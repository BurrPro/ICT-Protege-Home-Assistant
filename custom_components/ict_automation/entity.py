"""Shared push state, availability, and command errors."""
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity, DeviceInfo
from .const import DOMAIN
from .ict_library import ICTError


class ICTEntity(Entity):
    _attr_should_poll = False

    def __init__(self, client, record_id, name, kind, suffix=""):
        self._client = client
        self._record_id = record_id
        self._kind = kind
        self._attr_name = name
        self._device_name = name
        self._attr_unique_id = f"{client.entry_id}_ict_{kind}{suffix}_{record_id}"

    @property
    def available(self):
        return self._client.record_available(self._kind, self._record_id)

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._client.entry_id}:{self._kind}_{self._record_id}")},
            name=self._device_name,
            manufacturer="Integrated Control Technology",
            model=f"Protege {self._kind.title()}",
            via_device=(DOMAIN, f"{self._client.entry_id}:ict_controller"),
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self._client.register_callback(self._handle_update))
        self._handle_update({"type": "connection"})

    @callback
    def _handle_update(self, update):
        if update["type"] == "connection" or (
                update["type"] == self._kind and update["id"] == self._record_id):
            cached = self._client.cache.get((self._kind, self._record_id))
            if cached is not None:
                self._apply_update(cached)
            self.async_write_ha_state()

    async def _command(self, group, sub, code=None):
        try:
            if code is None:
                await self._client.send_command(group, sub, self._record_id)
            else:
                await self._client.send_command_with_pin(group, sub, self._record_id, code)
        except ICTError as err:
            raise HomeAssistantError(str(err)) from err
