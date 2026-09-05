from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from .const import DOMAIN, CONF_INPUTS, CONF_DOORS, CONF_TROUBLES
from .entity import ICTEntity


async def async_setup_entry(hass, entry, async_add_entities):
    client = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for key, kind in ((CONF_INPUTS, "input"), (CONF_DOORS, "door"), (CONF_TROUBLES, "trouble")):
        for record_id, name in entry.options.get(key, {}).items():
            entities.append(ICTInput(client, int(record_id), name, kind))
    async_add_entities(entities)


class ICTInput(ICTEntity, BinarySensorEntity):
    _attr_is_on = None

    def __init__(self, client, record_id, name, kind):
        super().__init__(client, record_id, name + (" Contact" if kind == "door" else ""),
                         kind, "_contact" if kind == "door" else "")
        self._device_name = name
        self._attr_device_class = (BinarySensorDeviceClass.DOOR if kind == "door" else
                                  BinarySensorDeviceClass.PROBLEM if kind == "trouble" else None)

    def _apply_update(self, update):
        self._attr_is_on = update["open"] if self._kind == "door" else update["on"]
        if "status" in update:
            self._attr_extra_state_attributes = {"status_text": update["status"]}
