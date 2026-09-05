"""Door-toggle tests use the controller simulator and offline HA API doubles."""
import asyncio
import importlib
import types
import unittest
from unittest.mock import AsyncMock, patch
from test_platform import flow, setup, EntityRegistry, DeviceRegistry, ha_stub
from test_service_protocol import module, PACKAGE
from test_client import Panel

button = importlib.import_module(PACKAGE + '.button')
lock = importlib.import_module(PACKAGE + '.lock')
binary = importlib.import_module(PACKAGE + '.binary_sensor')


class DoorToggleTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        client = module.ICTClient('test',21000,'1234',entry_id='one')
        client._connected = client._ready = True
        client.cache['door',1] = {'type':'door','id':1,'locked':True,'open':False}
        client._last_seen['door',1] = module.time.monotonic()
        client.send_command = AsyncMock()
        return client

    async def test_timed_unlock_for_open_and_closed_contacts(self):
        client = self.client()
        for opened in (False,True):
            client.cache['door',1]['open'] = opened
            entity = button.ICTDoorToggle(client,1,'Garage')
            await entity.async_press()
            client.send_command.assert_awaited_with(1,1,1)
        self.assertEqual(client.send_command.await_count,2)

    async def test_concurrent_and_repeat_presses_are_rejected(self):
        client = self.client()
        started, release = asyncio.Event(), asyncio.Event()
        async def send(*args):
            started.set()
            await release.wait()
        client.send_command.side_effect = send
        entity = button.ICTDoorToggle(client,1,'Garage')
        press = asyncio.create_task(entity.async_press())
        await started.wait()
        with self.assertRaises(ha_stub.HAError):
            await entity.async_press()
        release.set()
        await press
        with self.assertRaises(ha_stub.HAError):
            await entity.async_press()
        self.assertEqual(client.send_command.await_count,1)
        entity._next_press = 0
        await entity.async_press()
        self.assertEqual(client.send_command.await_count,2)

    async def test_unavailable_and_active_output_do_not_pulse(self):
        client = self.client()
        entity = button.ICTDoorToggle(client,1,'Garage')
        client.cache['door',1]['locked'] = False
        with self.assertRaisesRegex(ha_stub.HAError,'already active'):
            await entity.async_press()
        client.cache['door',1]['locked'] = True
        client._last_seen['door',1] -= 181
        with self.assertRaisesRegex(ha_stub.HAError,'unavailable'):
            await entity.async_press()
        client.send_command.assert_not_awaited()

    async def test_failure_is_not_retried_and_retains_press_guard(self):
        client = self.client()
        client.send_command.side_effect = module.ICTError('Reply timed out')
        entity = button.ICTDoorToggle(client,1,'Garage')
        with self.assertRaisesRegex(ha_stub.HAError,'timed out'):
            await entity.async_press()
        with self.assertRaisesRegex(ha_stub.HAError,'wait'):
            await entity.async_press()
        self.assertEqual(client.send_command.await_count,1)

    async def test_platforms_choose_button_or_lock_and_keep_contacts(self):
        client = self.client()
        entry = types.SimpleNamespace(entry_id='one',options={'doors':{'1':'Garage','2':'Front'},'door_types':{'1':'toggle'}})
        hass = types.SimpleNamespace(data={'ict_automation':{'one':client}})
        buttons, locks, sensors = [], [], []
        await button.async_setup_entry(hass,entry,buttons.extend)
        await lock.async_setup_entry(hass,entry,locks.extend)
        await binary.async_setup_entry(hass,entry,sensors.extend)
        self.assertEqual([e._record_id for e in buttons],[1])
        self.assertEqual([e._record_id for e in locks],[2])
        self.assertEqual([e._record_id for e in sensors],[1,2])
        self.assertEqual(buttons[0].device_info['identifiers'],sensors[0].device_info['identifiers'])
        self.assertEqual(sensors[0]._attr_unique_id,'one_ict_door_contact_1')

    async def test_existing_door_configuration_and_new_door_schema(self):
        entry = types.SimpleNamespace(data={},options={'doors':{'1':'Garage'}})
        handler = flow.ICTOptionsFlowHandler(entry)
        result = await handler.async_step_configure_door_types({'door':'1'})
        self.assertEqual(result['data_schema']({})['door_type'],'lock')
        result = await handler.async_step_door_type({'door_type':'toggle'})
        self.assertEqual(result['data']['door_types'],{1:'toggle'})
        handler = flow.ICTOptionsFlowHandler(types.SimpleNamespace(data={},options={}))
        form = await handler.async_step_add_door()
        values = form['data_schema']({'dev_id':1,'name':'Garage','door_type':'toggle','next_action':'finish'})
        result = await handler.async_step_add_door(values)
        self.assertEqual(result['data']['door_types'],{1:'toggle'})

    async def test_raw_editor_preserves_modes_and_rejects_invalid_modes(self):
        handler = flow.ICTOptionsFlowHandler(types.SimpleNamespace(data={},options={'doors':{'1':'Garage'},'door_types':{'1':'toggle'}}))
        result = await handler.async_step_raw_editor({'config_yaml':'doors:\n  1: Renamed\n'})
        self.assertEqual(result['data']['door_types'],{1:'toggle'})
        before = dict(handler.options)
        result = await handler.async_step_raw_editor({'config_yaml':'doors:\n  1: Garage\ndoor_types:\n  1: invalid\n'})
        self.assertEqual(result['errors']['base'],'yaml_error')
        self.assertEqual(handler.options,before)

    async def test_registry_cleanup_replaces_only_selected_lock(self):
        ids = ['one_ict_door_1','one_ict_door_toggle_1','one_ict_door_contact_1','one_ict_door_2']
        entities = [types.SimpleNamespace(entity_id=uid,unique_id=uid,config_entry_id='one') for uid in ids]
        hass = types.SimpleNamespace(entities=EntityRegistry(entities),devices=DeviceRegistry([]))
        entry = types.SimpleNamespace(entry_id='one',options={'door_types':{'1':'toggle'}})
        setup._remove_unconfigured_entities(hass,entry,{'doors':[1,2],'areas':[],'inputs':[],'outputs':[],'troubles':[]})
        self.assertEqual(set(hass.entities.entities),set(ids[1:]))

    async def test_controller_transaction_sends_one_timed_door_command(self):
        for checksum in (True,False):
            panel = Panel(checksum)
            client = module.ICTClient('test',21000,'1234',checksum='8_bit_sum' if checksum else 'none',entry_id='one')
            client.set_configuration([1],[],[],[])
            with patch.object(module.asyncio,'open_connection',panel.connect):
                try:
                    await client.start()
                    entity = button.ICTDoorToggle(client,1,'Garage')
                    await entity.async_press()
                    controls = [(g,s) for g,s,_ in panel.commands if g==1 and s!=0x80]
                    self.assertEqual(controls,[(1,1)])
                    self.assertTrue(client.available)
                finally:
                    await client.stop()
