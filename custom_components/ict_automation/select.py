from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import EntityCategory
from .const import DOMAIN, CONF_INPUTS
from .entity import ICTEntity

OPTIONS = ["Unbypassed", "Temporary Bypass", "Permanent Bypass"]


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ICTBypassSelect(hass.data[DOMAIN][entry.entry_id], int(k), name)
                        for k, name in entry.options.get(CONF_INPUTS, {}).items()])


class ICTBypassSelect(ICTEntity, SelectEntity):
    _attr_options = OPTIONS
    _attr_current_option = None
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, client, record_id, name):
        super().__init__(client, record_id, f"{name} Bypass", "input", "_bypass")
        self._device_name = name

    def _apply_update(self, update):
        self._attr_current_option = update["bypass_mode"]

    async def async_select_option(self, option):
        await self._command(4, OPTIONS.index(option))
