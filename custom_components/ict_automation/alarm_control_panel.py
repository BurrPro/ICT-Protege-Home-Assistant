"""Area controls never disarm the separate 24-hour portion."""
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity, AlarmControlPanelEntityFeature, AlarmControlPanelState, CodeFormat,
)
from homeassistant.exceptions import HomeAssistantError
from .const import DOMAIN, CONF_AREAS, CONF_ENABLE_AWAY, CONF_ENABLE_STAY, CONF_ENABLE_BYPASS
from .entity import ICTEntity

AREA_DISARM = 0x00
AREA_ARM_NORMAL = 0x03
AREA_ARM_FORCE = 0x04
AREA_ARM_STAY = 0x05


async def async_setup_entry(hass, entry, async_add_entities):
    client = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ICTArea(client, int(k), name, entry.options.get(CONF_ENABLE_AWAY, True),
                entry.options.get(CONF_ENABLE_STAY, True), entry.options.get(CONF_ENABLE_BYPASS, False))
        for k, name in entry.options.get(CONF_AREAS, {}).items()
    ])


class ICTArea(ICTEntity, AlarmControlPanelEntity):
    _attr_code_format = CodeFormat.NUMBER
    _attr_code_arm_required = True
    _attr_alarm_state = None

    def __init__(self, client, record_id, name, away, stay, bypass):
        super().__init__(client, record_id, name, "area")
        features = AlarmControlPanelEntityFeature(0)
        if away:
            features |= AlarmControlPanelEntityFeature.ARM_AWAY
        if stay:
            features |= AlarmControlPanelEntityFeature.ARM_HOME
        if bypass:
            features |= AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS
        self._attr_supported_features = features

    def _apply_update(self, update):
        state = update["alarm_state"]
        self._attr_alarm_state = AlarmControlPanelState(state) if state else None
        self._attr_extra_state_attributes = {key: update[key] for key in
            ("area_state", "status_text", "tamper_24h_state", "force_armed", "instant_armed", "stay_armed")}

    async def _area_command(self, command, code):
        if not code:
            raise HomeAssistantError("Enter a user PIN to control the area")
        await self._command(2, command, code)

    async def async_alarm_disarm(self, code=None):
        await self._area_command(AREA_DISARM, code)

    async def async_alarm_arm_away(self, code=None):
        await self._area_command(AREA_ARM_NORMAL, code)

    async def async_alarm_arm_home(self, code=None):
        await self._area_command(AREA_ARM_STAY, code)

    async def async_alarm_arm_custom_bypass(self, code=None):
        await self._area_command(AREA_ARM_FORCE, code)
