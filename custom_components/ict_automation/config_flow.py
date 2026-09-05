import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector, entity_registry as er
from .const import (
    DOMAIN, CONF_HOST, CONF_PORT, CONF_PASSWORD,
    CONF_DOOR_TYPES, DOOR_TYPE_LOCK, DOOR_TYPE_TOGGLE, get_door_type,
    CONF_CHECKSUM, CHECKSUM_NONE, CHECKSUM_8_BIT_SUM, DEFAULT_CHECKSUM,
    CONF_DOORS, CONF_AREAS, CONF_INPUTS, CONF_OUTPUTS, CONF_TROUBLES,
    CONF_ENABLE_AWAY, CONF_ENABLE_STAY, CONF_ENABLE_BYPASS
)
from .ict_library import ICTClient, ICTError, ICTNack, encode_pin
import logging
import yaml
import asyncio

_LOGGER = logging.getLogger(__name__)
RECORD_ID = vol.All(vol.Coerce(int), vol.Range(min=1, max=0xFFFFFFFF))
SCAN_LIMIT = vol.All(vol.Coerce(int), vol.Range(min=1, max=10000))
PORT = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))


async def validate_connection(data):
    client = ICTClient(data[CONF_HOST], data[CONF_PORT], data[CONF_PASSWORD],
                       checksum=data.get(CONF_CHECKSUM, DEFAULT_CHECKSUM))
    try:
        try:
            encode_pin(data[CONF_PASSWORD])
        except ICTError:
            return "invalid_auth"
        if not await client.start_temp_connection():
            return "cannot_connect"
        await client.authenticate()
        return None
    except ICTNack as err:
        return "invalid_auth" if err.code == 0x0302 else "cannot_connect"
    except ICTError:
        return "cannot_connect"
    finally:
        await client.stop()


def door_type_selector():
    return selector.SelectSelector(selector.SelectSelectorConfig(
        options=[{"value": DOOR_TYPE_LOCK, "label": "Normal lock"},
                 {"value": DOOR_TYPE_TOGGLE, "label": "Toggle (timed pulse)"}],
        mode=selector.SelectSelectorMode.DROPDOWN))


def checksum_selector():
    return selector.SelectSelector(selector.SelectSelectorConfig(
        options=[
            {"value": CHECKSUM_NONE, "label": "None"},
            {"value": CHECKSUM_8_BIT_SUM, "label": "8 Bit Sum"},
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
    ))


class ICTConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2
    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST].strip().lower()}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()
            for entry in self._async_current_entries():
                if (entry.data[CONF_HOST].strip().lower(), entry.data[CONF_PORT]) == (
                        user_input[CONF_HOST].strip().lower(), user_input[CONF_PORT]):
                    return self.async_abort(reason="already_configured")
            error = await validate_connection(user_input)
            if error is None:
                return self.async_create_entry(title=f"ICT ({user_input[CONF_HOST]})", data=user_input)
            errors["base"] = error
        return self.async_show_form(step_id="user", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=21000): PORT,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(CONF_CHECKSUM, default=DEFAULT_CHECKSUM): checksum_selector(),
        }))

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            error = await validate_connection(data)
            if error is None:
                self.hass.config_entries.async_update_entry(entry, data=data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            errors["base"] = error
        return self.async_show_form(step_id="reauth_confirm", errors=errors,
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ICTOptionsFlowHandler(config_entry)

class ICTOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._config_entry = config_entry
        self.options = dict(config_entry.options)
        self.data = dict(config_entry.data)

        self.options.setdefault(CONF_DOORS, {})
        self.options.setdefault(CONF_DOOR_TYPES, {})
        self.options.setdefault(CONF_AREAS, {})
        self.options.setdefault(CONF_INPUTS, {})
        self.options.setdefault(CONF_OUTPUTS, {})
        self.options.setdefault(CONF_TROUBLES, {})

        # Default Arming Modes
        self.options.setdefault(CONF_ENABLE_AWAY, True)
        self.options.setdefault(CONF_ENABLE_STAY, True)
        self.options.setdefault(CONF_ENABLE_BYPASS, False)

        self._edit_type = None
        self._edit_id = None

    def _get_dict(self, key):
        data = self.options.get(key, {})
        if isinstance(data, dict): return {int(k): v for k, v in data.items()}
        return {}

    def _save_options(self):
        self.hass.config_entries.async_update_entry(self._config_entry, options=self.options)

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=[
            "scan_devices", "configure_arming", "configure_connection", "configure_door_types",
            "add_door", "add_area", "add_input", "add_output", "add_trouble",
            "edit_device", "remove_device", "raw_editor"
        ])

    async def async_step_configure_door_types(self, user_input=None):
        doors = self._get_dict(CONF_DOORS)
        if not doors:
            return self.async_abort(reason="no_devices")
        if user_input is not None:
            self._door_type_id = int(user_input["door"])
            if self._door_type_id not in doors:
                return self.async_abort(reason="no_devices")
            return await self.async_step_door_type()
        choices = [{"value": str(k), "label": f"{k}: {name}"} for k, name in doors.items()]
        return self.async_show_form(step_id="configure_door_types", data_schema=vol.Schema({
            vol.Required("door"): selector.SelectSelector(selector.SelectSelectorConfig(
                options=choices, mode=selector.SelectSelectorMode.DROPDOWN))}))

    async def async_step_door_type(self, user_input=None):
        if user_input is not None:
            mode = user_input["door_type"]
            if mode not in (DOOR_TYPE_LOCK, DOOR_TYPE_TOGGLE):
                return self.async_abort(reason="invalid_door_type")
            modes = self._get_dict(CONF_DOOR_TYPES)
            modes[self._door_type_id] = mode
            self.options[CONF_DOOR_TYPES] = modes
            return self.async_create_entry(title="", data=self.options)
        return self.async_show_form(step_id="door_type", data_schema=vol.Schema({
            vol.Required("door_type", default=get_door_type(self.options, self._door_type_id)): door_type_selector()}),
            description_placeholders={"name": self._get_dict(CONF_DOORS)[self._door_type_id]})

    # --- ARMING MODES CONFIGURATION ---
    async def async_step_configure_arming(self, user_input=None):
        if user_input is not None:
            self.options.update(user_input)
            return self.async_create_entry(title="", data=self.options)

        return self.async_show_form(
            step_id="configure_arming",
            data_schema=vol.Schema({
                vol.Required(CONF_ENABLE_AWAY, default=self.options.get(CONF_ENABLE_AWAY, True)): bool,
                vol.Required(CONF_ENABLE_STAY, default=self.options.get(CONF_ENABLE_STAY, True)): bool,
                vol.Optional(CONF_ENABLE_BYPASS, default=self.options.get(CONF_ENABLE_BYPASS, False)): bool,
            })
        )

    # --- RAW YAML EDITOR ---
    async def async_step_raw_editor(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                raw_data = yaml.safe_load(user_input["config_yaml"])
                if not isinstance(raw_data, dict): raise ValueError("Root must be a dictionary")
                parsed = {key: self._parse_raw_section(raw_data.get(key, {}))
                          for key in (CONF_DOORS, CONF_AREAS, CONF_INPUTS, CONF_OUTPUTS, CONF_TROUBLES)}
                modes = raw_data.get(CONF_DOOR_TYPES, self._get_dict(CONF_DOOR_TYPES))
                if not isinstance(modes, dict):
                    raise ValueError("Door types must be a mapping")
                modes = {RECORD_ID(k): value for k, value in modes.items()}
                if any(value not in (DOOR_TYPE_LOCK, DOOR_TYPE_TOGGLE) for value in modes.values()):
                    raise ValueError("Invalid door type")
                if CONF_DOOR_TYPES in raw_data and set(modes) - set(parsed[CONF_DOORS]):
                    raise ValueError("Door type references an unconfigured door")
                parsed[CONF_DOOR_TYPES] = {k: value for k, value in modes.items() if k in parsed[CONF_DOORS]}
                self.options.update(parsed)
                return self.async_create_entry(title="", data=self.options)
            except Exception: errors["base"] = "yaml_error"

        current_config = {
            "doors": self._get_dict(CONF_DOORS), "door_types": self._get_dict(CONF_DOOR_TYPES),
            "areas": self._get_dict(CONF_AREAS),
            "inputs": self._get_dict(CONF_INPUTS), "outputs": self._get_dict(CONF_OUTPUTS),
            "troubles": self._get_dict(CONF_TROUBLES)
        }
        yaml_str = yaml.dump(current_config, sort_keys=True, allow_unicode=True)
        return self.async_show_form(step_id="raw_editor", data_schema=vol.Schema({vol.Required("config_yaml", default=yaml_str): selector.TextSelector(selector.TextSelectorConfig(multiline=True))}), errors=errors)

    def _parse_raw_section(self, section):
        if not section: return {}
        return {RECORD_ID(k): str(v) for k, v in section.items()}

    # --- ADD ITEMS (WIZARD) ---
    async def _add_item_step(self, user_input, type_name, storage_key, step_id):
        storage_dict = self._get_dict(storage_key)
        errors = {}
        if user_input is not None:
            dev_id = int(user_input["dev_id"])
            if dev_id in storage_dict: errors["base"] = "id_exists"
            else:
                storage_dict[dev_id] = user_input["name"]
                self.options[storage_key] = storage_dict
                if storage_key == CONF_DOORS:
                    modes = self._get_dict(CONF_DOOR_TYPES)
                    modes[dev_id] = user_input.get("door_type", DOOR_TYPE_LOCK)
                    self.options[CONF_DOOR_TYPES] = modes
                if user_input.get("next_action") == "add_more":
                    return self.async_show_form(step_id=step_id, data_schema=self._get_schema_wizard(storage_key == CONF_DOORS), description_placeholders={"type": type_name})
                else:
                    return self.async_create_entry(title="", data=self.options)
        return self.async_show_form(step_id=step_id, data_schema=self._get_schema_wizard(storage_key == CONF_DOORS), errors=errors, description_placeholders={"type": type_name})

    def _get_schema_wizard(self, is_door=False):
        schema = {
            vol.Required("dev_id"): RECORD_ID,
            vol.Required("name"): str,
            vol.Required("next_action", default="add_more"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": "add_more", "label": "Save & Add Another"},
                        {"value": "finish", "label": "Save & Finish"}
                    ],
                    mode=selector.SelectSelectorMode.LIST
                )
            )
        }
        if is_door:
            schema[vol.Required("door_type", default=DOOR_TYPE_LOCK)] = door_type_selector()
        return vol.Schema(schema)

    async def async_step_add_door(self, user_input=None): return await self._add_item_step(user_input, "door", CONF_DOORS, "add_door")
    async def async_step_add_area(self, user_input=None): return await self._add_item_step(user_input, "area", CONF_AREAS, "add_area")
    async def async_step_add_input(self, user_input=None): return await self._add_item_step(user_input, "input", CONF_INPUTS, "add_input")
    async def async_step_add_output(self, user_input=None): return await self._add_item_step(user_input, "output", CONF_OUTPUTS, "add_output")

    # --- REMOVE ITEMS ---
    async def async_step_remove_device(self, user_input=None):
        return self.async_show_menu(step_id="remove_device", menu_options=["remove_door", "remove_area", "remove_input", "remove_output", "remove_trouble", "back"])

    async def _remove_step(self, user_input, storage_key, step_id):
        storage_dict = self._get_dict(storage_key)
        if user_input:
            ent_reg = er.async_get(self.hass)
            def get_uids_to_remove(dev_id, key):
                uids = []
                if key == CONF_DOORS:
                    uids.append(f"ict_door_{dev_id}"); uids.append(f"ict_door_contact_{dev_id}"); uids.append(f"ict_door_toggle_{dev_id}")
                elif key == CONF_AREAS: uids.append(f"ict_area_{dev_id}")
                elif key == CONF_INPUTS:
                    uids.append(f"ict_input_{dev_id}"); uids.append(f"ict_input_bypass_{dev_id}")
                elif key == CONF_OUTPUTS: uids.append(f"ict_output_{dev_id}")
                elif key == CONF_TROUBLES: uids.append(f"ict_trouble_{dev_id}")
                return uids

            for i in user_input["items"]:
                try:
                    dev_id = int(i)
                    if dev_id in storage_dict: del storage_dict[dev_id]
                    if storage_key == CONF_DOORS:
                        modes = self._get_dict(CONF_DOOR_TYPES)
                        modes.pop(dev_id, None)
                        self.options[CONF_DOOR_TYPES] = modes
                    target_uids = [f"{self._config_entry.entry_id}_{uid}" for uid in get_uids_to_remove(dev_id, storage_key)]
                    for entry in list(ent_reg.entities.values()):
                        if entry.config_entry_id == self._config_entry.entry_id and entry.unique_id in target_uids:
                            ent_reg.async_remove(entry.entity_id)
                except: continue
            self.options[storage_key] = storage_dict
            return self.async_create_entry(title="", data=self.options)

        if not storage_dict: return self.async_abort(reason="no_devices")
        options_list = [selector.SelectOptionDict(value=str(k), label=f"{k}: {v}") for k, v in storage_dict.items()]
        schema = vol.Schema({vol.Required("items"): selector.SelectSelector(selector.SelectSelectorConfig(options=options_list, mode=selector.SelectSelectorMode.DROPDOWN, multiple=True))})
        return self.async_show_form(step_id=step_id, data_schema=schema)

    async def async_step_remove_door(self, user_input=None): return await self._remove_step(user_input, CONF_DOORS, "remove_door")
    async def async_step_remove_area(self, user_input=None): return await self._remove_step(user_input, CONF_AREAS, "remove_area")
    async def async_step_remove_input(self, user_input=None): return await self._remove_step(user_input, CONF_INPUTS, "remove_input")
    async def async_step_remove_output(self, user_input=None): return await self._remove_step(user_input, CONF_OUTPUTS, "remove_output")

    # --- EDIT ---
    async def async_step_edit_device(self, user_input=None):
        return self.async_show_menu(step_id="edit_device", menu_options=["edit_door", "edit_area", "edit_input", "edit_output", "edit_trouble", "back"])

    async def _edit_select_step(self, user_input, storage_key, step_id):
        storage_dict = self._get_dict(storage_key)
        if user_input:
            self._edit_id = int(user_input["item"])
            self._edit_type = storage_key
            # IMPORTANT: We save the step_id so we can loop back to it later
            self._last_edit_step_id = step_id
            return await self.async_step_edit_confirm()
        if not storage_dict: return self.async_abort(reason="no_devices")
        options_list = [selector.SelectOptionDict(value=str(k), label=f"{k}: {v}") for k, v in storage_dict.items()]
        return self.async_show_form(step_id=step_id, data_schema=vol.Schema({vol.Required("item"): selector.SelectSelector(selector.SelectSelectorConfig(options=options_list, mode=selector.SelectSelectorMode.DROPDOWN))}))

    async def async_step_edit_confirm(self, user_input=None):
        storage = self._get_dict(self._edit_type)
        if user_input:
            storage[self._edit_id] = user_input["name"]
            self.options[self._edit_type] = storage
            self._save_options() # Save immediately

            # --- THE LOOP FIX ---
            # Instead of closing, we loop back to the selection list!
            # We map the storage key back to the function name
            if self._edit_type == CONF_DOORS: return await self.async_step_edit_door(None)
            if self._edit_type == CONF_AREAS: return await self.async_step_edit_area(None)
            if self._edit_type == CONF_INPUTS: return await self.async_step_edit_input(None)
            if self._edit_type == CONF_OUTPUTS: return await self.async_step_edit_output(None)
            if self._edit_type == CONF_TROUBLES: return await self.async_step_edit_trouble(None)

            return self.async_create_entry(title="", data=self.options)

        return self.async_show_form(step_id="edit_confirm", data_schema=vol.Schema({vol.Required("name", default=storage.get(self._edit_id, "")): str}), description_placeholders={"id": str(self._edit_id)})

    async def async_step_edit_door(self, user_input=None): return await self._edit_select_step(user_input, CONF_DOORS, "edit_door")
    async def async_step_edit_area(self, user_input=None): return await self._edit_select_step(user_input, CONF_AREAS, "edit_area")
    async def async_step_edit_input(self, user_input=None): return await self._edit_select_step(user_input, CONF_INPUTS, "edit_input")
    async def async_step_edit_output(self, user_input=None): return await self._edit_select_step(user_input, CONF_OUTPUTS, "edit_output")

    # --- SCANNER ---
    async def async_step_scan_devices(self, user_input=None):
        return self.async_show_menu(step_id="scan_devices", menu_options=["scan_all", "scan_doors", "scan_areas", "scan_inputs", "scan_outputs", "scan_troubles", "back"])

    async def async_step_scan_all(self, user_input=None):
        if user_input: return await self._execute_scan_logic(**user_input)
        return self.async_show_form(step_id="scan_all", data_schema=vol.Schema({
            vol.Required("limit_areas", default=10): SCAN_LIMIT, vol.Required("limit_doors", default=20): SCAN_LIMIT,
            vol.Required("limit_outputs", default=20): SCAN_LIMIT, vol.Required("limit_inputs", default=100): SCAN_LIMIT,
        }))

    async def async_step_scan_doors(self, user_input=None):
        if user_input: return await self._execute_scan_logic(limit_doors=user_input["limit"])
        return self.async_show_form(step_id="scan_doors", data_schema=vol.Schema({vol.Required("limit", default=20): SCAN_LIMIT}))
    async def async_step_scan_areas(self, user_input=None):
        if user_input: return await self._execute_scan_logic(limit_areas=user_input["limit"])
        return self.async_show_form(step_id="scan_areas", data_schema=vol.Schema({vol.Required("limit", default=10): SCAN_LIMIT}))
    async def async_step_scan_inputs(self, user_input=None):
        if user_input: return await self._execute_scan_logic(limit_inputs=user_input["limit"])
        return self.async_show_form(step_id="scan_inputs", data_schema=vol.Schema({vol.Required("limit", default=100): SCAN_LIMIT}))
    async def async_step_scan_outputs(self, user_input=None):
        if user_input: return await self._execute_scan_logic(limit_outputs=user_input["limit"])
        return self.async_show_form(step_id="scan_outputs", data_schema=vol.Schema({vol.Required("limit", default=20): SCAN_LIMIT}))

    async def async_step_add_trouble(self, user_input=None):
        return await self._add_item_step(user_input, "trouble input", CONF_TROUBLES, "add_trouble")

    async def async_step_remove_trouble(self, user_input=None):
        return await self._remove_step(user_input, CONF_TROUBLES, "remove_trouble")

    async def async_step_edit_trouble(self, user_input=None):
        return await self._edit_select_step(user_input, CONF_TROUBLES, "edit_trouble")

    async def async_step_scan_troubles(self, user_input=None):
        if user_input:
            return await self._execute_scan_logic(limit_troubles=user_input["limit"])
        return self.async_show_form(step_id="scan_troubles", data_schema=vol.Schema({
            vol.Required("limit", default=100): SCAN_LIMIT}))

    async def _execute_scan_logic(self, limit_doors=0, limit_areas=0, limit_inputs=0,
                                 limit_outputs=0, limit_troubles=0):
        client = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id)
        temporary = client is None
        if temporary:
            client = ICTClient(self.data[CONF_HOST], self.data[CONF_PORT], self.data[CONF_PASSWORD],
                               checksum=self.data.get(CONF_CHECKSUM, DEFAULT_CHECKSUM))
        try:
            if temporary:
                if not await client.start_temp_connection():
                    return self.async_abort(reason="cannot_connect")
                await client.authenticate()
            for group, limit, key, name in (
                (1, limit_doors, CONF_DOORS, "Door"), (2, limit_areas, CONF_AREAS, "Area"),
                (3, limit_outputs, CONF_OUTPUTS, "Output"), (4, limit_inputs, CONF_INPUTS, "Input"),
                (6, limit_troubles, CONF_TROUBLES, "Trouble Input")):
                if limit:
                    await self._run_scan(client, group, limit, self._get_dict(key), name, key)
        except ICTNack as err:
            return self.async_abort(reason="invalid_auth" if err.code == 0x0302 else "cannot_connect")
        except ICTError:
            return self.async_abort(reason="cannot_connect")
        finally:
            if temporary:
                await client.stop()
        return self.async_create_entry(title="", data=self.options)

    async def _run_scan(self, client, group, limit, storage, name_prefix, conf_key):
        for i in range(1, limit + 1):
            if i not in storage and await client.check_exists(group, i):
                storage[i] = f"{name_prefix} {i}"
            await asyncio.sleep(0.02)
        self.options[conf_key] = storage

    async def async_step_configure_connection(self, user_input=None):
        errors = {}
        if user_input is not None:
            # Editing an active service may not permit a second simultaneous
            # connection. Reload performs the authoritative login validation.
            try:
                encode_pin(user_input[CONF_PASSWORD])
            except ICTError:
                errors["base"] = "invalid_auth"
            else:
                unique_id = f"{user_input[CONF_HOST].strip().lower()}:{user_input[CONF_PORT]}"
                if any(entry.entry_id != self._config_entry.entry_id and
                       (entry.data[CONF_HOST].strip().lower(), entry.data[CONF_PORT]) ==
                       (user_input[CONF_HOST].strip().lower(), user_input[CONF_PORT])
                       for entry in self.hass.config_entries.async_entries(DOMAIN)):
                    errors["base"] = "already_configured"
                else:
                    self.hass.config_entries.async_update_entry(self._config_entry,
                        data={**self.data, **user_input}, options=self.options, unique_id=unique_id)
                    return self.async_create_entry(title="", data=self.options)
        schema = vol.Schema({
            vol.Required(CONF_HOST, default=self.data.get(CONF_HOST)): str,
            vol.Required(CONF_PORT, default=self.data.get(CONF_PORT, 21000)): PORT,
            vol.Required(CONF_PASSWORD, default=self.data.get(CONF_PASSWORD)): str,
            vol.Required(CONF_CHECKSUM, default=self.data.get(CONF_CHECKSUM, DEFAULT_CHECKSUM)): checksum_selector(),
        })
        return self.async_show_form(step_id="configure_connection", data_schema=schema, errors=errors)

    async def async_step_back(self, user_input=None): return await self.async_step_init()
