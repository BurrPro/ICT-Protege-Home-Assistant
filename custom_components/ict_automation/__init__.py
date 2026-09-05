"""Set up ICT and migrate identifiers without replacing existing entity IDs."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er, device_registry as dr
from .const import (
    DOMAIN, CONF_HOST, CONF_PORT, CONF_PASSWORD, CONF_CHECKSUM, DEFAULT_CHECKSUM,
    CONF_DOORS, CONF_AREAS, CONF_INPUTS, CONF_OUTPUTS, CONF_TROUBLES,
)
from .ict_library import ICTClient, ICTError, ICTNack, encode_pin

PLATFORMS = ["lock", "binary_sensor", "switch", "alarm_control_panel", "select"]
RECORD_KINDS = {CONF_DOORS: "door", CONF_AREAS: "area", CONF_INPUTS: "input",
                CONF_OUTPUTS: "output", CONF_TROUBLES: "trouble"}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


def _migrate_registries(hass, entry):
    """Scope old IDs while retaining registry IDs, names and automations."""
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    entries = er.async_entries_for_config_entry(entities, entry.entry_id)
    for entity in entries:
        if entity.unique_id.startswith("ict_"):
            entities.async_update_entity(entity.entity_id,
                new_unique_id=f"{entry.entry_id}_{entity.unique_id}")
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        legacy = {(domain, value) for domain, value in device.identifiers
                  if domain == DOMAIN and ":" not in value}
        if not legacy:
            continue
        scoped = {(domain, f"{entry.entry_id}:{value}") for domain, value in legacy}
        if device.config_entries == {entry.entry_id}:
            devices.async_update_device(device.id,
                new_identifiers=(device.identifiers - legacy) | scoped)
        else:
            # Older versions could merge devices belonging to two controllers.
            # Split this entry's entities; leave other entries' devices intact.
            replacement = devices.async_get_or_create(config_entry_id=entry.entry_id,
                identifiers=scoped, name=device.name, manufacturer=device.manufacturer,
                model=device.model)
            for entity in entries:
                if entity.device_id == device.id:
                    entities.async_update_entity(entity.entity_id, device_id=replacement.id)
            devices.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_migrate_entry(hass, entry):
    if entry.version > 2:
        return False
    if entry.version < 2:
        _migrate_registries(hass, entry)
        hass.config_entries.async_update_entry(entry, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    # Also retry any interrupted registry migration on setup.
    _migrate_registries(hass, entry)
    client = ICTClient(entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data.get(CONF_PASSWORD),
        checksum=entry.data.get(CONF_CHECKSUM, DEFAULT_CHECKSUM), entry_id=entry.entry_id)
    try:
        encode_pin(entry.data.get(CONF_PASSWORD))
    except ICTError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    try:
        ids = {key: [int(k) for k in entry.options.get(key, {})] for key in RECORD_KINDS}
        client.set_configuration(doors=ids[CONF_DOORS], areas=ids[CONF_AREAS],
            inputs=ids[CONF_INPUTS], outputs=ids[CONF_OUTPUTS], troubles=ids[CONF_TROUBLES])
        await client.start()
    except ICTNack as err:
        await client.stop()
        if err.code == 0x0302:
            raise ConfigEntryAuthFailed(str(err)) from err
        raise ConfigEntryNotReady(str(err)) from err
    except (ICTError, ValueError, TypeError) as err:
        await client.stop()
        raise ConfigEntryNotReady(str(err)) from err

    devices = dr.async_get(hass)
    devices.async_get_or_create(config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}:ict_controller")},
        name=f"ICT Controller ({entry.data[CONF_HOST]})",
        manufacturer="Integrated Control Technology", model="Protege")
    hass.data[DOMAIN][entry.entry_id] = client
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await client.stop()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    _remove_unconfigured_entities(hass, entry, ids)
    return True


def _remove_unconfigured_entities(hass, entry, ids):
    valid_entities = set()
    valid_devices = {(DOMAIN, f"{entry.entry_id}:ict_controller")}
    for key, kind in RECORD_KINDS.items():
        for idx in ids[key]:
            valid_entities.add(f"{entry.entry_id}_ict_{kind}_{idx}")
            valid_devices.add((DOMAIN, f"{entry.entry_id}:{kind}_{idx}"))
            if kind == "door":
                valid_entities.add(f"{entry.entry_id}_ict_door_contact_{idx}")
            if kind == "input":
                valid_entities.add(f"{entry.entry_id}_ict_input_bypass_{idx}")
    entities = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entities, entry.entry_id):
        if entity.unique_id not in valid_entities:
            entities.async_remove(entity.entity_id)
    devices = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if not device.identifiers & valid_devices:
            devices.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    client = hass.data[DOMAIN].pop(entry.entry_id)
    await client.stop()
    return True
