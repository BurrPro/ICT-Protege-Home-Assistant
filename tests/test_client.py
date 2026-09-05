"""Deterministic controller simulator, exercising the complete client lifecycle."""
import asyncio
import struct
import unittest
from unittest.mock import patch
from test_service_protocol import module, frame


def status(group, idx, state=0, flags=0):
    if group in (1, 2):
        body = struct.pack('<I', idx) + bytes([state, 0x80 if group == 2 else 0, flags, 0])
    else:
        body = struct.pack('<I', idx) + b'CP001:01' + bytes([state, flags, 0, 0])
    return bytes([0, group, len(body)]) + body


class PanelWriter:
    def __init__(self, panel, reader):
        self.panel, self.reader = panel, reader
        self.closed = False
        self.logged_in = False

    def write(self, packet):
        if self.closed:
            raise ConnectionResetError("Test socket closed")
        self.panel.wire.append(bytes(packet))
        if packet[4] == 0xC0:
            return
        checksum = self.panel.checksum
        if checksum:
            assert packet[-1] == sum(packet[:-1]) & 255
        payload = packet[6:-1] if checksum else packet[6:]
        group, sub = payload[:2]
        self.panel.commands.append((group, sub, payload[2:]))
        reply = frame(b'\xff\x00', checksum, 0xc0)
        if self.panel.silent:
            return
        if (group, sub) in self.panel.nack_once:
            code = self.panel.nack_once.pop((group, sub))
            reply = frame(b'\xff\xff' + struct.pack('<H', code), checksum, 0xc0)
        elif (group, sub) == (0, 2):
            if self.panel.retained_login:
                reply = frame(b'\xff\xff\x00\x03', checksum, 0xc0)
            elif payload[2:] != b'\x01\x02\x03\x04\xff':
                self.logged_in = False
                reply = frame(b'\xff\xff\x02\x03', checksum, 0xc0)
            else:
                self.logged_in = True
                self.panel.retained_login = True
        elif (group, sub) == (0, 3):
            self.logged_in = False
            if not self.panel.sticky_login:
                self.panel.retained_login = False
        elif sub == 0x80:
            idx = struct.unpack('<I', payload[2:6])[0]
            if idx == 999:
                reply = frame(b'\xff\xff\x21\x01', checksum, 0xc0)
            else:
                reply = frame(status(group, idx, self.panel.area_state if group == 2 else 0,
                                     self.panel.area_flags if group == 2 else 0), checksum)
        elif group == 2:
            if not self.logged_in:
                reply = frame(b'\xff\xff\x01\x03', checksum, 0xc0)
            elif self.panel.reject_control:
                reply = frame(b'\xff\xff\x03\x03', checksum, 0xc0)
            elif sub == 0:
                self.panel.area_state, self.panel.area_flags = 0, 0
            elif sub in (3, 4, 5):
                self.panel.area_state = 0x80
                self.panel.area_flags = {3: 0, 4: 0x10, 5: 0x40}[sub]
            else:
                raise AssertionError('Unsafe or unexpected area command')
        asyncio.get_running_loop().call_soon(lambda: None if self.closed else self.reader.feed_data(reply))

    async def drain(self):
        await asyncio.sleep(0)

    def close(self):
        if not self.closed:
            self.closed = True
            self.reader.feed_eof()

    async def wait_closed(self):
        pass


class Panel:
    def __init__(self, checksum=True):
        self.checksum = checksum
        self.commands, self.wire, self.writers = [], [], []
        self.area_state, self.area_flags = 0, 0
        self.silent = self.reject_control = False
        self.retained_login = self.sticky_login = False
        self.nack_once = {}

    async def connect(self, *args):
        reader = asyncio.StreamReader()
        writer = PanelWriter(self, reader)
        self.writers.append(writer)
        return reader, writer


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.panel = Panel()
        self.connection_patch = patch.object(module.asyncio, 'open_connection', self.panel.connect)
        self.connection_patch.start()
        self.client = module.ICTClient('test', 21000, '1234', entry_id='one')
        self.client.set_configuration([], [1], [], [])

    async def asyncTearDown(self):
        await self.client.stop()
        self.connection_patch.stop()

    async def test_verified_login_control_restore_and_cached_feedback(self):
        await self.client.start()
        self.assertTrue(self.client.record_available('area', 1))
        self.assertEqual(self.client.cache['area', 1]['alarm_state'], 'disarmed')
        await self.client.send_command_with_pin(2, 5, 1, '1234')
        self.assertEqual(self.client.cache['area', 1]['alarm_state'], 'armed_home')
        self.assertEqual(self.client.cache['area', 1]['tamper_24h_state'], 'armed')
        await self.client.send_command_with_pin(2, 0, 1, '1234')
        self.assertEqual(self.client.cache['area', 1]['alarm_state'], 'disarmed')
        self.assertEqual(self.client.cache['area', 1]['tamper_24h_state'], 'armed')
        self.assertTrue(self.panel.writers[-1].logged_in)
        self.assertTrue(all(sub not in (1, 2) for group, sub, _ in self.panel.commands if group == 2))

    async def test_bad_user_pin_never_sends_control(self):
        await self.client.start()
        with self.assertRaisesRegex(module.ICTNack, 'Invalid PIN'):
            await self.client.send_command_with_pin(2, 3, 1, '9999')
        self.assertFalse(any(group == 2 and sub == 3 for group, sub, _ in self.panel.commands))
        self.assertTrue(self.client.available)
        self.assertTrue(self.panel.writers[-1].logged_in)

    async def test_rejected_command_is_error_not_optimistic_success(self):
        await self.client.start()
        self.panel.reject_control = True
        with self.assertRaisesRegex(module.ICTNack, 'Area control denied'):
            await self.client.send_command_with_pin(2, 3, 1, '1234')
        self.assertEqual(self.client.cache['area', 1]['alarm_state'], 'disarmed')

    async def test_control_nack_names_the_rejected_operation(self):
        await self.client.start()
        self.panel.nack_once[1, 1] = 0x0120
        with self.assertRaisesRegex(module.ICTNack, 'Timed unlock door 1: Invalid command'):
            await self.client.send_command(1, 1, 1)
        self.assertEqual(sum((g, s) == (1, 1) for g, s, _ in self.panel.commands), 1)
        self.assertTrue(self.client.available)

    async def test_feedback_nack_does_not_turn_confirmed_control_into_failure(self):
        self.client.set_configuration([1], [], [], [])
        await self.client.start()
        self.panel.nack_once[1, 0x80] = 0x0120
        with self.assertLogs(module._LOGGER, level='WARNING') as logs:
            self.assertTrue(await self.client.send_command(1, 1, 1))
        self.assertTrue(any('feedback refresh failed' in line for line in logs.output))
        self.assertEqual(sum((g, s) == (1, 1) for g, s, _ in self.panel.commands), 1)
        self.assertTrue(self.client.available)

    async def test_service_session_control_does_not_cycle_login(self):
        self.client.set_configuration([1], [], [], [])
        await self.client.start()
        before = len(self.panel.commands)
        self.assertTrue(await self.client.send_command(1, 1, 1))
        transaction = [(g, s) for g, s, _ in self.panel.commands[before:]]
        self.assertEqual(transaction, [(1, 1), (1, 0x80)])
        self.assertTrue(self.panel.writers[-1].logged_in)

    async def test_restore_nack_recovers_without_failing_confirmed_control(self):
        self.client.set_configuration([1], [], [], [])
        await self.client.start()
        self.panel.nack_once[0, 5] = 0x0120
        with self.assertLogs(module._LOGGER, level='WARNING') as logs:
            self.assertTrue(await self.client.send_command_with_pin(1, 1, 1, '1234'))
        self.assertTrue(any('Monitor door 1: Invalid command' in line for line in logs.output))
        self.assertEqual(sum((g, s) == (1, 1) for g, s, _ in self.panel.commands), 1)
        self.assertFalse(self.client.available)

    async def test_restore_resubscribes_without_refreshing_every_record(self):
        self.client.set_configuration([1, 2], [], [], [])
        await self.client.start()
        before = sum(sub == 0x80 for _, sub, _ in self.panel.commands)
        await self.client.send_command_with_pin(1, 1, 1, '1234')
        after = sum(sub == 0x80 for _, sub, _ in self.panel.commands)
        self.assertEqual(after - before, 1)

    async def test_timeout_closes_session_without_retry(self):
        await self.client.start()
        self.panel.silent = True
        with patch.object(module, 'RESPONSE_TIMEOUT', 0.02):
            with self.assertRaises(module.ICTError):
                await self.client.send_command_with_pin(2, 3, 1, '1234')
        self.assertFalse(self.client.available)
        self.assertEqual(self.client.cache, {})
        self.assertFalse(any(group == 2 and sub == 3 for group, sub, _ in self.panel.commands))
        self.assertFalse(self.client._supervisor.done())

    async def test_reconnect_reauthenticates_and_restores_monitoring(self):
        with patch.object(module, 'HEARTBEAT_INTERVAL', 0.01), patch.object(module, 'RECONNECT_DELAY', 0.01):
            await self.client.start()
            self.panel.writers[-1].close()
            for _ in range(100):
                await asyncio.sleep(0.01)
                if len(self.panel.writers) >= 2 and self.client.available:
                    break
            self.assertGreaterEqual(len(self.panel.writers), 2)
            self.assertTrue(self.client.record_available('area', 1))
            self.assertGreaterEqual(sum((g, s) == (0, 5) for g, s, _ in self.panel.commands), 2)

    async def test_none_checksum_complete_session(self):
        self.panel.checksum = False
        self.client.checksum = 'none'
        await self.client.start()
        await self.client.send_command_with_pin(2, 3, 1, '1234')
        self.assertEqual(self.client.cache['area', 1]['alarm_state'], 'armed_away')

    async def test_scan_matches_record_not_unsolicited_data(self):
        await self.client.start()
        await asyncio.sleep(0.02)  # Let the initial heartbeat complete.
        self.panel.silent = True
        scan = asyncio.create_task(self.client.check_exists(2, 999))
        for _ in range(100):
            await asyncio.sleep(0)
            if self.client._expected == (2, 999):
                break
        self.assertEqual(self.client._expected, (2, 999))
        await self.client._handle_packet(frame(status(1, 1)))
        self.assertFalse(scan.done())
        await self.client._handle_packet(frame(status(2, 1)))
        self.assertFalse(scan.done())
        await self.client._handle_packet(frame(b'\xff\xff\x21\x01', kind=0xc0))
        self.assertFalse(await scan)

    async def test_no_status_ack_for_requested_reply(self):
        await self.client.start()
        acks = lambda: sum(packet[4] == 0xc0 for packet in self.panel.wire)
        before = acks()
        self.assertTrue(await self.client.check_exists(2, 1))
        self.assertEqual(acks(), before)
        await self.client._handle_packet(frame(status(2, 1)))
        self.assertEqual(acks(), before + 1)

    async def test_unrelated_status_cannot_authenticate(self):
        self.assertTrue(await self.client.start_temp_connection())
        self.panel.silent = True
        auth = asyncio.create_task(self.client.authenticate())
        await asyncio.sleep(0)
        await self.client._handle_packet(frame(status(2, 1)))
        self.assertFalse(auth.done())
        await self.client._handle_packet(frame(b'\xff\xff\x02\x03', kind=0xc0))
        with self.assertRaises(module.ICTNack):
            await auth

    async def test_callback_unsubscribe_and_failure_isolation(self):
        updates = []
        def broken(update):
            raise RuntimeError('test subscriber')
        self.client.register_callback(broken)
        unsubscribe = self.client.register_callback(updates.append)
        with self.assertLogs(module._LOGGER, level='ERROR'):
            self.client._publish({'type':'connection'})
        self.assertEqual(len(updates), 1)
        unsubscribe()
        with self.assertLogs(module._LOGGER, level='ERROR'):
            self.client._publish({'type':'connection'})
        self.assertEqual(len(updates), 1)
        self.client._callbacks.clear()

    async def test_bad_pins_rejected_without_modification(self):
        for pin in ('', '1234567', '12x34', ' 1234', '\u0661\u0662\u0663\u0664', None):
            with self.assertRaises(module.ICTError):
                module.encode_pin(pin)
        self.assertEqual(module.encode_pin('0123'), b'\x00\x01\x02\x03\xff')

    async def test_area_transitions_and_trouble_bypass_decoding(self):
        expected = {0:'disarmed', 0x80:'armed_away', 0x81:'arming', 0x82:'pending', 0x83:'disarming', 0x84:None, 1:None}
        for state, name in expected.items():
            blocks = self.client._decode_blocks(status(2, 1, state))
            self.assertEqual(blocks[0][2]['alarm_state'], name)
        self.assertEqual(self.client._decode_blocks(status(2, 1, 0x80, 1))[0][2]['alarm_state'], 'triggered')
        update = self.client._decode_blocks(status(6, 7, 1, 3))[0][2]
        self.assertTrue(update['on'])
        self.assertEqual(update['bypass_mode'], 'Permanent Bypass')
        self.assertEqual(update['id'], 7)

    async def test_status_expires_and_disconnect_clears_cache(self):
        await self.client.start()
        self.client._last_seen['area',1] -= 181
        self.assertFalse(self.client.record_available('area',1))
        await self.client.disconnect()
        self.assertEqual(self.client.cache, {})
        self.assertFalse(self.client._supervisor.done())

    async def test_troubles_are_not_inferred_from_physical_inputs(self):
        self.client.set_configuration([], [], [3], [], [7])
        self.assertEqual(self.client.monitored_items, [(0,4,3),(0,6,7)])


class ReloadSessionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = ClientTests.asyncSetUp
    asyncTearDown = ClientTests.asyncTearDown
    async def test_stale_login_requires_logout_then_verified_login(self):
        self.panel.retained_login = True
        await self.client.start()
        self.assertEqual([(g,s) for g,s,_ in self.panel.commands[:4]], [(0,2),(0,3),(0,2),(0,4)])
        self.assertTrue(self.client.available)

    async def test_wrong_pin_is_not_accepted_after_stale_login(self):
        self.panel.retained_login = True
        self.client.service_pin = '9999'
        with self.assertRaisesRegex(module.ICTNack, 'Invalid PIN'):
            await self.client.start()
        self.assertFalse(self.client.available)
        self.assertFalse(any((g,s)==(0,5) for g,s,_ in self.panel.commands))

    async def test_stop_logs_out_before_close_and_reload_works(self):
        await self.client.start()
        await self.client.stop()
        self.assertEqual(self.panel.commands[-1][:2], (0,3))
        self.assertFalse(self.panel.retained_login)
        self.assertTrue(self.panel.writers[-1].closed)
        await self.client.start()
        self.assertTrue(self.client.available)

    async def test_persistent_already_logged_in_is_bounded(self):
        self.panel.retained_login = self.panel.sticky_login = True
        with self.assertRaises(module.ICTNack) as caught:
            await self.client.start()
        self.assertEqual(caught.exception.code, 0x0300)
        self.assertEqual(sum((g,s)==(0,2) for g,s,_ in self.panel.commands),2)
        self.assertFalse(self.client.available)
