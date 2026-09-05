import asyncio
import importlib
import importlib.util
import types
import unittest
from unittest.mock import AsyncMock, patch
from test_service_protocol import module, ROOT, PACKAGE
from test_client import Panel, status
import ha_stub

ha_stub.install()
flow = importlib.import_module(PACKAGE+'.config_flow')
area = importlib.import_module(PACKAGE+'.alarm_control_panel')
select = importlib.import_module(PACKAGE+'.select')
lock = importlib.import_module(PACKAGE+'.lock')
spec = importlib.util.spec_from_file_location(PACKAGE+'.setup', ROOT/'__init__.py')
setup = importlib.util.module_from_spec(spec)
setup.__package__ = PACKAGE
spec.loader.exec_module(setup)


class PlatformTests(unittest.IsolatedAsyncioTestCase):
    def client(self, identity='first'):
        client = module.ICTClient('host',21000,'1234',entry_id=identity)
        client.send_command_with_pin=AsyncMock()
        client.send_command=AsyncMock()
        return client

    async def test_area_commands_do_not_touch_24_hour_monitoring(self):
        client = self.client()
        entity = area.ICTArea(client, 9, 'Area', True, True, True)
        for method, sub in [('async_alarm_disarm',0),('async_alarm_arm_away',3),
                            ('async_alarm_arm_home',5),('async_alarm_arm_custom_bypass',4)]:
            await getattr(entity,method)('1234')
            client.send_command_with_pin.assert_awaited_with(2,sub,9,'1234')
        features = entity._attr_supported_features
        self.assertFalse(features & (ha_stub.Feature.ARM_NIGHT|ha_stub.Feature.ARM_VACATION|ha_stub.Feature.TRIGGER))
        with self.assertRaises(ha_stub.HAError):
            await entity.async_alarm_arm_away()
        self.assertEqual(client.send_command_with_pin.await_count,4)

    async def test_platform_errors_surface(self):
        client=self.client()
        client.send_command_with_pin.side_effect=module.ICTNack(0x0302)
        entity=area.ICTArea(client,1,'Area',True,True,False)
        with self.assertRaisesRegex(ha_stub.HAError,'Invalid PIN'):
            await entity.async_alarm_arm_away('9999')

    async def test_unknown_start_cache_replay_and_unsubscribe(self):
        client=self.client()
        entity=lock.ICTDoor(client,1,'Door')
        self.assertFalse(entity.available)
        self.assertIsNone(entity._attr_is_locked)
        client._connected=client._ready=True
        client.cache['door',1]={'type':'door','id':1,'locked':False,'open':True}
        client._last_seen['door',1]=module.time.monotonic()
        await entity.async_added_to_hass()
        self.assertTrue(entity.available)
        self.assertFalse(entity._attr_is_locked)
        self.assertEqual(len(client._callbacks),1)
        for remove in entity.removers:
            remove()
        self.assertEqual(client._callbacks,[])

    async def test_bypass_is_confirmed_not_optimistic(self):
        client=self.client()
        entity=select.ICTBypassSelect(client,1,'Input')
        await entity.async_select_option('Permanent Bypass')
        self.assertIsNone(entity._attr_current_option)
        entity._apply_update({'bypass_mode':'Temporary Bypass'})
        self.assertEqual(entity._attr_current_option,'Temporary Bypass')
        client.send_command.side_effect=module.ICTError('Disconnected')
        with self.assertRaises(ha_stub.HAError):
            await entity.async_select_option('Permanent Bypass')
        self.assertEqual(entity._attr_current_option,'Temporary Bypass')

    async def test_distinct_controller_identifiers(self):
        one=lock.ICTDoor(self.client('one'),1,'Door')
        two=lock.ICTDoor(self.client('two'),1,'Door')
        self.assertNotEqual(one._attr_unique_id,two._attr_unique_id)
        self.assertNotEqual(one.device_info['identifiers'],two.device_info['identifiers'])

    async def test_form_checksum_defaults_and_validation(self):
        result=await flow.ICTConfigFlow().async_step_user()
        schema=result['data_schema']
        self.assertEqual(schema({'host':'test','password':'1234'})['checksum'],'8_bit_sum')
        self.assertEqual(schema({'host':'test','password':'1234','checksum':'none'})['checksum'],'none')
        import voluptuous as vol
        for patch_data in ({'port':0},{'port':65536},{'checksum':'crc'}):
            with self.assertRaises(vol.Invalid):
                schema({'host':'test','password':'1234',**patch_data})

    async def test_setup_validation_requires_ack(self):
        panel=Panel()
        with patch.object(module.asyncio,'open_connection',panel.connect):
            self.assertIsNone(await flow.validate_connection({'host':'x','port':1,'password':'1234'}))
            self.assertEqual(await flow.validate_connection({'host':'x','port':1,'password':'9999'}),'invalid_auth')
            panel.silent=True
            with patch.object(module,'RESPONSE_TIMEOUT',0.01):
                self.assertEqual(await flow.validate_connection({'host':'x','port':1,'password':'1234'}),'cannot_connect')
        self.assertTrue(all(writer.closed for writer in panel.writers))

    async def test_scan_all_limits_not_swapped_and_gaps_not_skipped(self):
        entry=types.SimpleNamespace(data={},options={})
        handler=flow.ICTOptionsFlowHandler(entry)
        handler._execute_scan_logic=AsyncMock(return_value={})
        limits={'limit_areas':2,'limit_doors':3,'limit_outputs':4,'limit_inputs':5}
        await handler.async_step_scan_all(limits)
        handler._execute_scan_logic.assert_awaited_once_with(**limits)
        client=types.SimpleNamespace(check_exists=AsyncMock(side_effect=lambda group,idx:idx==9))
        with patch.object(flow.asyncio,'sleep',AsyncMock()):
            await handler._run_scan(client,4,10,{},'Input','inputs')
        self.assertEqual(handler.options['inputs'],{9:'Input 9'})
        self.assertEqual(client.check_exists.await_count,10)


class EntityRegistry:
    def __init__(self, entities):
        self.entities={e.entity_id:e for e in entities}
    def async_update_entity(self, entity_id, **kwargs):
        entity=self.entities[entity_id]
        for key,value in kwargs.items():
            setattr(entity,'unique_id' if key=='new_unique_id' else key,value)
    def async_remove(self, entity_id):
        del self.entities[entity_id]


class DeviceRegistry:
    def __init__(self, devices):
        self.devices={d.id:d for d in devices}
    def async_update_device(self, device_id, **kwargs):
        device=self.devices[device_id]
        if 'new_identifiers' in kwargs:
            device.identifiers=kwargs['new_identifiers']
        if 'remove_config_entry_id' in kwargs:
            device.config_entries.discard(kwargs['remove_config_entry_id'])
    def async_get_or_create(self, config_entry_id, identifiers, **kwargs):
        for device in self.devices.values():
            if device.identifiers & identifiers:
                device.config_entries.add(config_entry_id)
                return device
        device=types.SimpleNamespace(id=f'new{len(self.devices)}',identifiers=identifiers,
            config_entries={config_entry_id},**kwargs)
        self.devices[device.id]=device
        return device


class MigrationTests(unittest.TestCase):
    def test_migration_preserves_entity_id_and_splits_shared_devices(self):
        entities=[types.SimpleNamespace(entity_id='alarm_control_panel.my_area',unique_id='ict_area_1',
            config_entry_id='one',device_id='old'), types.SimpleNamespace(entity_id='alarm_control_panel.other_area',
            unique_id='ict_area_2',config_entry_id='two',device_id='old')]
        device=types.SimpleNamespace(id='old',identifiers={('ict_automation','area_1')},
            config_entries={'one','two'},name='Area',manufacturer='ICT',model='Area')
        hass=types.SimpleNamespace(entities=EntityRegistry(entities),devices=DeviceRegistry([device]))
        setup._migrate_registries(hass,types.SimpleNamespace(entry_id='one'))
        self.assertEqual(entities[0].unique_id,'one_ict_area_1')
        self.assertEqual(entities[0].entity_id,'alarm_control_panel.my_area')
        self.assertNotEqual(entities[0].device_id,'old')
        self.assertEqual(entities[1].device_id,'old')
        self.assertEqual(device.config_entries,{'two'})
        self.assertEqual(entities[1].unique_id,'ict_area_2')
        setup._migrate_registries(hass,types.SimpleNamespace(entry_id='one'))
        self.assertEqual(len(hass.devices.devices),2)

    def test_exclusive_device_preserves_device_id(self):
        device=types.SimpleNamespace(id='old',identifiers={('ict_automation','door_1')},config_entries={'one'})
        hass=types.SimpleNamespace(entities=EntityRegistry([]),devices=DeviceRegistry([device]))
        setup._migrate_registries(hass,types.SimpleNamespace(entry_id='one'))
        self.assertEqual(device.id,'old')
        self.assertEqual(device.identifiers,{('ict_automation','one:door_1')})


class SetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.panel=Panel()
        self.patch=patch.object(module.asyncio,'open_connection',self.panel.connect)
        self.patch.start()
        self.entry=types.SimpleNamespace(entry_id='one',version=1,unique_id=None,
            data={'host':'test','port':21000,'password':'1234'},options={'areas':{'1':'Area'}},
            async_on_unload=lambda cb:None,add_update_listener=lambda cb:lambda:None)
        manager=types.SimpleNamespace(async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),async_reload=AsyncMock(),
            async_get_entry=lambda key:self.entry,
            async_update_entry=lambda entry,**kw:entry.__dict__.update(kw))
        self.hass=types.SimpleNamespace(data={},entities=EntityRegistry([]),
            devices=DeviceRegistry([]),config_entries=manager)

    async def asyncTearDown(self):
        for client in self.hass.data.get('ict_automation',{}).values():
            await client.stop()
        self.patch.stop()

    async def test_setup_migration_and_unload_lifecycle(self):
        self.assertTrue(await setup.async_migrate_entry(self.hass,self.entry))
        self.assertEqual(self.entry.version,2)
        self.assertTrue(await setup.async_setup_entry(self.hass,self.entry))
        client=self.hass.data['ict_automation']['one']
        self.assertTrue(client.record_available('area',1))
        self.hass.config_entries.async_unload_platforms.return_value=False
        self.assertFalse(await setup.async_unload_entry(self.hass,self.entry))
        self.assertTrue(client.available)
        self.hass.config_entries.async_unload_platforms.return_value=True
        self.assertTrue(await setup.async_unload_entry(self.hass,self.entry))
        self.assertFalse(client.available)
        self.assertTrue(client._supervisor.done())
        self.assertTrue(client._poller.done())

    async def test_invalid_credentials_surface_as_auth_failure(self):
        from homeassistant.exceptions import ConfigEntryAuthFailed
        for pin in ('invalid','9999'):
            self.entry.data['password']=pin
            with self.assertRaises(ConfigEntryAuthFailed):
                await setup.async_setup_entry(self.hass,self.entry)
        self.assertTrue(all(writer.closed for writer in self.panel.writers))

    async def test_reauthentication_saves_verified_pin(self):
        handler=flow.ICTConfigFlow()
        handler.hass=self.hass
        handler.context={'entry_id':'one'}
        result=await handler.async_step_reauth({})
        self.assertEqual(result['step_id'],'reauth_confirm')
        result=await handler.async_step_reauth_confirm({'password':'9999'})
        self.assertEqual(result['errors']['base'],'invalid_auth')
        result=await handler.async_step_reauth_confirm({'password':'1234'})
        self.assertEqual(result['reason'],'reauth_successful')
        self.hass.config_entries.async_reload.assert_awaited_once_with('one')

    async def test_cleanup_removes_inferred_troubles_only_from_this_entry(self):
        own=types.SimpleNamespace(entity_id='binary_sensor.old_trouble',unique_id='one_ict_trouble_1',config_entry_id='one')
        other=types.SimpleNamespace(entity_id='binary_sensor.other',unique_id='two_ict_trouble_1',config_entry_id='two')
        explicit=types.SimpleNamespace(entity_id='binary_sensor.valid_trouble',unique_id='one_ict_trouble_7',config_entry_id='one')
        self.hass.entities=EntityRegistry([own,other,explicit])
        setup._remove_unconfigured_entities(self.hass,self.entry,
            {'areas':[1],'doors':[],'inputs':[1],'outputs':[],'troubles':[7]})
        self.assertEqual(set(self.hass.entities.entities),{'binary_sensor.other','binary_sensor.valid_trouble'})


class ConnectionOptionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_none_and_legacy_duplicate_connection(self):
        entry=types.SimpleNamespace(entry_id='one',data={'host':'first','port':21000,
            'password':'1234','checksum':'none'},options={})
        other=types.SimpleNamespace(entry_id='two',unique_id=None,
            data={'host':'second','port':21000,'password':'1234'})
        handler=flow.ICTOptionsFlowHandler(entry)
        handler.hass=types.SimpleNamespace(config_entries=types.SimpleNamespace(
            async_entries=lambda domain:[entry,other]))
        result=await handler.async_step_configure_connection()
        self.assertEqual(result['data_schema']({})['checksum'],'none')
        result=await handler.async_step_configure_connection({'host':'second','port':21000,
            'password':'1234','checksum':'none'})
        self.assertEqual(result['errors']['base'],'already_configured')
