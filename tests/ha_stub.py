"""Small HA API contract doubles for offline platform and migration tests.

These do not replace validation in a running Home Assistant installation.
"""
import enum
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.test-deps'))


def module(name, **attrs):
    obj = types.ModuleType(name)
    obj.__dict__.update(attrs)
    sys.modules[name] = obj
    return obj


class Entity:
    async def async_added_to_hass(self):
        pass
    def async_on_remove(self, callback):
        self.removers = getattr(self, 'removers', []) + [callback]
    def async_write_ha_state(self):
        self.writes = getattr(self, 'writes', 0) + 1


class Flow:
    def __init_subclass__(cls, **kwargs):
        pass
    def async_show_form(self, **kwargs):
        return {'type': 'form', **kwargs}
    def async_show_menu(self, **kwargs):
        return {'type': 'menu', **kwargs}
    def async_create_entry(self, **kwargs):
        return {'type': 'create_entry', **kwargs}
    def async_abort(self, **kwargs):
        return {'type': 'abort', **kwargs}
    async def async_set_unique_id(self, value):
        self._unique_id = value
    def _abort_if_unique_id_configured(self):
        pass
    def _async_current_entries(self):
        return []


class Select:
    def __init__(self, config):
        self.config = config
    def __call__(self, value):
        import voluptuous as vol
        allowed = [v['value'] if isinstance(v,dict) else v for v in self.config['options']]
        if value not in allowed:
            raise vol.Invalid('Invalid selection')
        return value


class AlarmState(enum.StrEnum):
    DISARMED='disarmed'
    ARMED_AWAY='armed_away'
    ARMED_HOME='armed_home'
    ARMING='arming'
    PENDING='pending'
    DISARMING='disarming'
    TRIGGERED='triggered'


class Feature(enum.IntFlag):
    ARM_HOME=1
    ARM_AWAY=2
    ARM_NIGHT=4
    TRIGGER=8
    ARM_CUSTOM_BYPASS=16
    ARM_VACATION=32


class HAError(Exception):
    pass


def install():
    module('homeassistant')
    module('homeassistant.core', callback=lambda f:f, HomeAssistant=object)
    module('homeassistant.config_entries', ConfigEntry=object, ConfigFlow=Flow, OptionsFlow=Flow)
    module('homeassistant.exceptions', HomeAssistantError=HAError,
           ConfigEntryAuthFailed=type('ConfigEntryAuthFailed',(HAError,),{}),
           ConfigEntryNotReady=type('ConfigEntryNotReady',(HAError,),{}))
    module('homeassistant.helpers')
    module('homeassistant.helpers.entity', Entity=Entity, DeviceInfo=dict,
           EntityCategory=types.SimpleNamespace(CONFIG='config'))
    module('homeassistant.helpers.entity_registry', async_get=lambda hass:hass.entities,
           async_entries_for_config_entry=lambda reg,entry:[e for e in reg.entities.values() if e.config_entry_id==entry])
    module('homeassistant.helpers.device_registry', async_get=lambda hass:hass.devices,
           async_entries_for_config_entry=lambda reg,entry:[d for d in reg.devices.values() if entry in d.config_entries])
    module('homeassistant.helpers.selector', SelectSelector=Select, SelectSelectorConfig=lambda **kw:kw,
           SelectSelectorMode=types.SimpleNamespace(LIST='list',DROPDOWN='dropdown'),
           SelectOptionDict=lambda **kw:kw, TextSelector=lambda conf:str, TextSelectorConfig=lambda **kw:kw)
    module('homeassistant.components')
    module('homeassistant.components.alarm_control_panel', AlarmControlPanelEntity=type('AlarmEntity',(Entity,),{}),
           AlarmControlPanelEntityFeature=Feature, AlarmControlPanelState=AlarmState,
           CodeFormat=types.SimpleNamespace(NUMBER='number'))
    for platform, classname in [('button','ButtonEntity'),('lock','LockEntity'),('switch','SwitchEntity'),('select','SelectEntity'),('binary_sensor','BinarySensorEntity')]:
        attrs = {classname:type(classname,(Entity,),{})}
        if platform == 'binary_sensor':
            attrs['BinarySensorDeviceClass']=types.SimpleNamespace(DOOR='door',PROBLEM='problem')
        module('homeassistant.components.'+platform, **attrs)
