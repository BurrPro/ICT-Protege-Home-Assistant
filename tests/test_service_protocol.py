"""Service wire tests without importing Home Assistant."""
import asyncio
import importlib.util
from pathlib import Path
import struct
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "ict_automation"
PACKAGE = "ict_protocol_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(PACKAGE + ".ict_library", ROOT / "ict_library.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def frame(payload, checksum=True, kind=1):
    packet = b"IC" + struct.pack("<H", 6 + len(payload) + int(checksum)) + bytes([kind, 0]) + payload
    return packet + bytes([sum(packet) & 255]) if checksum else packet


class Writer:
    def __init__(self):
        self.packets = []
    def write(self, data):
        self.packets.append(bytes(data))
    async def drain(self):
        pass
    def close(self):
        pass
    async def wait_closed(self):
        pass


class ServiceProtocolTests(unittest.IsolatedAsyncioTestCase):
    def client(self, checksum="8_bit_sum"):
        client = module.ICTClient("localhost", 21000, "1234", checksum)
        client._writer = Writer()
        return client

    async def test_command_wire_bytes(self):
        for mode, expected in [("8_bit_sum", "494309000000000095"), ("none", "4943080000000000")]:
            client = self.client(mode)
            await client._send_raw(0, 0, b"")
            self.assertEqual(client._writer.packets, [bytes.fromhex(expected)])

    async def test_data_delivered_and_acknowledged_in_both_modes(self):
        for mode in ("none", "8_bit_sum"):
            client = self.client(mode)
            updates = []
            client.register_callback(updates.append)
            # Door status: record 1, unlocked, open. Final byte must survive None mode.
            payload = bytes.fromhex("000106010000000101")
            await client._handle_packet(frame(payload, mode != "none"))
            self.assertEqual(updates, [{"type": "door", "id": 1, "locked": False, "open": True}])
            self.assertEqual(client._writer.packets, [frame(b"\xff\x00", mode != "none", 0xc0)])

    async def test_invalid_checksum_and_truncated_block_not_acknowledged(self):
        client = self.client()
        updates = []
        client.register_callback(updates.append)
        packet = bytearray(frame(bytes.fromhex("000106010000000101")))
        packet[-1] ^= 1
        await client._handle_packet(packet)
        await client._handle_packet(frame(bytes.fromhex("00010601")))
        self.assertEqual(updates, [])
        self.assertEqual(client._writer.packets, [])

    async def test_system_ack_does_not_cause_ack_loop(self):
        client = self.client()
        await client._handle_packet(frame(b"\xff\x00", kind=0xc0))
        self.assertEqual(client._writer.packets, [])

    async def test_event_block_is_acknowledged(self):
        client = self.client()
        await client._handle_packet(frame(bytes.fromhex("001003010203ffff")))
        self.assertEqual(client._writer.packets, [frame(b"\xff\x00", kind=0xc0)])

    async def test_fragmented_coalesced_and_invalid_length_frames(self):
        for mode in ("none", "8_bit_sum"):
            client = self.client(mode)
            writer = client._writer
            client._connected = True
            client._reader = asyncio.StreamReader()
            listener = asyncio.create_task(client._listen())
            packet = frame(bytes.fromhex("000106010000000101"), mode != "none")
            client._reader.feed_data(b"noiseIC\x00\x00" + packet[:3])
            await asyncio.sleep(0)
            self.assertEqual(writer.packets, [])
            client._reader.feed_data(packet[3:] + packet)
            client._reader.feed_eof()
            await listener
            self.assertEqual(len(writer.packets), 2)


if __name__ == "__main__":
    unittest.main()
