from homeassistant.components.switch import SwitchEntity
from .const import DOMAIN, CONF_OUTPUTS
from .entity import ICTEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ICTOutput(hass.data[DOMAIN][entry.entry_id], int(k), name)
                        for k, name in entry.options.get(CONF_OUTPUTS, {}).items()])


class ICTOutput(ICTEntity, SwitchEntity):
    _attr_is_on = None

    def __init__(self, client, record_id, name):
        super().__init__(client, record_id, name, "output")

    def _apply_update(self, update):
        self._attr_is_on = update["on"]

    async def async_turn_on(self, **kwargs):
        await self._command(3, 1)

    async def async_turn_off(self, **kwargs):
        await self._command(3, 0)
