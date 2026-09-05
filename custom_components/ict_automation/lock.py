from homeassistant.components.lock import LockEntity
from .const import DOMAIN, CONF_DOORS
from .entity import ICTEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ICTDoor(hass.data[DOMAIN][entry.entry_id], int(k), name)
                        for k, name in entry.options.get(CONF_DOORS, {}).items()])


class ICTDoor(ICTEntity, LockEntity):
    _attr_is_locked = None
    _attr_is_open = None

    def __init__(self, client, record_id, name):
        super().__init__(client, record_id, name, "door")

    def _apply_update(self, update):
        self._attr_is_locked = update["locked"]
        self._attr_is_open = update["open"]

    async def async_lock(self, **kwargs):
        await self._command(1, 0)

    async def async_unlock(self, **kwargs):
        # Preserve the existing latched-unlock behaviour.
        await self._command(1, 2)
