"""ICT client: one request in flight, unsolicited data ACKed independently.

No control retries: a timeout closes the socket to discard late, uncorrelated ACKs.
"""
import asyncio
import logging
import struct
import time
from .const import CHECKSUM_NONE, CHECKSUM_8_BIT_SUM, DEFAULT_CHECKSUM

_LOGGER = logging.getLogger(__name__)
PKT_TYPE_COMMAND, PKT_TYPE_DATA, PKT_TYPE_SYSTEM = 0x00, 0x01, 0xC0
RESPONSE_TIMEOUT = 5.0
RECONNECT_DELAY = 10.0
HEARTBEAT_INTERVAL = 15.0
POLL_INTERVAL = 60.0
MIN_BODY_LENGTH = {1: 8, 2: 8, 3: 16, 4: 16, 6: 16}
KIND_BY_GROUP = {1: "door", 2: "area", 3: "output", 4: "input", 6: "trouble"}


class ICTError(Exception):
    """An operation could not be confirmed."""


class ICTNack(ICTError):
    def __init__(self, code):
        self.code = code
        descriptions = {
            0x0120: "Invalid command", 0x0121: "Invalid record index",
            0x0300: "User already logged in", 0x0301: "User logged out",
            0x0302: "Invalid PIN", 0x0303: "Area control denied",
            0x030A: "Door group control denied", 0x030F: "Door control denied",
            0x040E: "Bypass not allowed", 0x0869: "Area already in requested state",
        }
        super().__init__(descriptions.get(code, f"Controller rejected command ({code:#06x})"))


def encode_pin(pin):
    value = str(pin) if pin is not None else ""
    if not 1 <= len(value) <= 6 or any(c not in "0123456789" for c in value):
        raise ICTError("PIN must contain 1 to 6 ASCII digits")
    return bytes(int(c) for c in value) + (b"\xff" if len(value) < 6 else b"")


class ICTClient:
    def __init__(self, host, port, password, checksum=DEFAULT_CHECKSUM, entry_id=""):
        if checksum not in (CHECKSUM_NONE, CHECKSUM_8_BIT_SUM):
            raise ValueError("Unsupported ICT checksum type")
        self.host, self.port, self.service_pin = host, port, password
        self.checksum, self.entry_id = checksum, entry_id
        self._reader = self._writer = None
        self._connected = self._ready = self._shutdown = False
        self._lock = asyncio.Lock()
        self._pending = self._expected = None
        self._listener = self._supervisor = self._poller = None
        self._callbacks = []
        self.monitored_items = []
        self.cache = {}
        self._last_seen = {}
        self._closing = False
        self._disconnected = asyncio.Event()
        self._disconnected.set()

    @property
    def available(self):
        return self._connected and self._ready and not self._shutdown

    def record_available(self, kind, idx):
        return (self.available and (kind, idx) in self.cache
                and time.monotonic() - self._last_seen.get((kind, idx), 0) < 180)

    def register_callback(self, callback):
        self._callbacks.append(callback)
        def unsubscribe():
            if callback in self._callbacks:
                self._callbacks.remove(callback)
        return unsubscribe

    def _publish(self, update):
        for callback in tuple(self._callbacks):
            try:
                callback(update)
            except Exception:
                _LOGGER.exception("ICT state subscriber failed")

    def set_configuration(self, doors, areas, inputs, outputs, troubles=()):
        self.monitored_items = [(0, group, idx) for group, ids in
            ((1, doors), (2, areas), (3, outputs), (4, inputs), (6, troubles)) for idx in ids]
        for _, _, idx in self.monitored_items:
            if not 1 <= idx <= 0xFFFFFFFF:
                raise ValueError("ICT record IDs must be between 1 and 4294967295")

    async def start_temp_connection(self):
        await self._disconnected.wait()
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), 10.0)
        except (OSError, asyncio.TimeoutError):
            return False
        self._connected = True
        self._listener = asyncio.create_task(self._listen())
        return True

    async def authenticate(self):
        pin = encode_pin(self.service_pin)
        async with self._lock:
            try:
                await self._request(0, 2, pin)
            except ICTNack as err:
                if err.code != 0x0300:
                    raise
                # A reload may inherit the service's old login. Do not treat
                # that as PIN validation: clear it and verify this PIN once.
                await self._logout()
                await self._request(0, 2, pin)
            await self._request(0, 4, struct.pack("<H", 6000))
        return True

    async def _open_session(self):
        if not await self.start_temp_connection():
            raise ICTError("Cannot connect to ICT controller")
        try:
            await self.authenticate()
            async with self._lock:
                await self._update_monitoring()
            self._ready = True
            self._publish({"type": "connection"})
        except BaseException:
            await self.disconnect()
            raise

    async def start(self):
        self._shutdown = False
        await self._open_session()
        self._supervisor = asyncio.create_task(self._supervisor_loop())
        self._poller = asyncio.create_task(self._safety_poll_loop())

    async def _logout(self):
        try:
            await self._request(0, 3, b"")
        except ICTNack as err:
            if err.code != 0x0301:  # Already logged out is an acceptable end state.
                raise

    async def _logout_before_close(self):
        async with self._lock:
            if self._connected:
                await self._logout()

    async def stop(self):
        self._shutdown = True
        # Keep the reader alive to receive the logout ACK before closing TCP.
        # Bound the wait if another operation owns the request lock.
        try:
            if self._connected:
                await asyncio.wait_for(self._logout_before_close(), RESPONSE_TIMEOUT * 2)
        except (ICTError, asyncio.TimeoutError):
            _LOGGER.debug("ICT logout could not be confirmed during shutdown")
        finally:
            await self._stop_tasks_and_disconnect()

    async def _stop_tasks_and_disconnect(self):
        tasks = [t for t in (self._supervisor, self._poller) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.disconnect()
        self._callbacks.clear()

    async def _supervisor_loop(self):
        while not self._shutdown:
            try:
                if not self._connected:
                    await self._open_session()
                else:
                    async with self._lock:
                        await self._request(0, 0, b"")
                self._publish({"type": "connection"})
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            except ICTError as err:
                _LOGGER.warning("ICT connection unavailable: %s", err)
                await self.disconnect()
                await asyncio.sleep(RECONNECT_DELAY)

    async def _safety_poll_loop(self):
        while not self._shutdown:
            await asyncio.sleep(POLL_INTERVAL)
            for _, group, idx in self.monitored_items:
                if not self.available:
                    break
                try:
                    async with self._lock:
                        await self._status(group, idx)
                except ICTNack as err:
                    if err.code == 0x0121:
                        self._invalidate(group, idx)
                    else:
                        await self.disconnect()
                        break
                except ICTError:
                    break
                await asyncio.sleep(0.02)

    async def _update_monitoring(self):
        for number, (_, group, idx) in enumerate(self.monitored_items):
            try:
                if number < 250:
                    # Subscribe without force-update: command ACK, then explicit
                    # status request. Additional items use the safety poll.
                    payload = struct.pack("<HI", group, idx) + b"\x01\x00"
                    await self._request(0, 5, payload)
                await self._status(group, idx)
            except ICTNack as err:
                if err.code != 0x0121:
                    raise
                self._invalidate(group, idx)

    def _invalidate(self, group, idx):
        _LOGGER.warning("ICT %s record %s was not found", KIND_BY_GROUP[group], idx)
        self.cache.pop((KIND_BY_GROUP[group], idx), None)
        self._last_seen.pop((KIND_BY_GROUP[group], idx), None)
        self._publish({"type": "connection"})

    async def _status(self, group, idx):
        return await self._request(group, 0x80, struct.pack("<I", idx), (group, idx))

    async def check_exists(self, group, idx):
        async with self._lock:
            try:
                await self._status(group, idx)
                return True
            except ICTNack as err:
                if err.code == 0x0121:
                    return False
                raise

    async def send_command(self, group, sub, index_id):
        return await self.send_command_with_pin(group, sub, index_id, self.service_pin)

    async def send_command_with_pin(self, group, sub, index_id, pin_code):
        pin = encode_pin(pin_code)
        async with self._lock:
            if not self.available:
                raise ICTError("ICT controller is unavailable")
            await self._request(0, 3, b"")
            error = None
            try:
                # A command is sent only after this exact user's login is ACKed.
                await self._request(0, 2, pin)
                await self._request(group, sub, struct.pack("<I", index_id))
                await self._status(group, index_id)
            except ICTError as err:
                error = err
            finally:
                # Logout clears subscriptions. Restore the service identity and
                # monitoring even after a rejected user command.
                if self._connected:
                    try:
                        try:
                            await self._request(0, 3, b"")
                        except ICTNack as err:
                            if err.code != 0x0301:  # Already logged out after a rejected PIN.
                                raise
                        await self._request(0, 2, encode_pin(self.service_pin))
                        await self._request(0, 4, struct.pack("<H", 6000))
                        await self._update_monitoring()
                    except ICTError as err:
                        await self.disconnect()
                        error = error or err
            if error is not None:
                raise error
        return True

    async def _request(self, group, sub, data, expected=None):
        if not self._connected:
            raise ICTError("ICT controller is disconnected")
        future = asyncio.get_running_loop().create_future()
        self._pending, self._expected = future, expected
        try:
            await self._send_raw(group, sub, data)
            return await asyncio.wait_for(future, RESPONSE_TIMEOUT)
        except asyncio.TimeoutError as err:
            await self.disconnect()
            raise ICTError("ICT reply timed out; command outcome is unknown") from err
        except asyncio.CancelledError:
            await self.disconnect()
            raise
        finally:
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()
            if self._pending is future:
                self._pending = self._expected = None

    async def _send_raw(self, group, sub, data):
        await self._send_packet(PKT_TYPE_COMMAND, bytes([group, sub]) + data)

    async def _send_packet(self, packet_type, payload):
        if self._writer is None:
            raise ICTError("ICT controller is disconnected")
        checksum_length = int(self.checksum == CHECKSUM_8_BIT_SUM)
        full = bytearray(b"IC") + struct.pack("<H", 6 + len(payload) + checksum_length)
        full.extend((packet_type, 0))
        full.extend(payload)
        if checksum_length:
            full.append(sum(full) & 255)
        try:
            self._writer.write(full)
            await asyncio.wait_for(self._writer.drain(), RESPONSE_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as err:
            await self.disconnect()
            raise ICTError("ICT send failed; command outcome is unknown") from err

    async def _listen(self):
        buffer = bytearray()
        try:
            while self._connected:
                chunk = await self._reader.read(4096)
                if not chunk:
                    break
                buffer.extend(chunk)
                while len(buffer) >= 4:
                    if buffer[:2] != b"IC":
                        del buffer[0]
                        continue
                    length = struct.unpack("<H", buffer[2:4])[0]
                    if length < 6 + int(self.checksum == CHECKSUM_8_BIT_SUM):
                        del buffer[0]
                        continue
                    if len(buffer) < length:
                        break
                    packet = bytes(buffer[:length])
                    del buffer[:length]
                    await self._handle_packet(packet)
        except (OSError, ICTError) as err:
            _LOGGER.warning("ICT receive failed: %s", err)
        finally:
            await self.disconnect()

    async def _handle_packet(self, packet):
        checksum_length = int(self.checksum == CHECKSUM_8_BIT_SUM)
        if (len(packet) < 6 + checksum_length or packet[:2] != b"IC"
                or struct.unpack("<H", packet[2:4])[0] != len(packet)):
            return
        if checksum_length and (sum(packet[:-1]) & 255) != packet[-1]:
            _LOGGER.warning("Discarding ICT packet with invalid checksum")
            return
        if packet[5] != 0:
            _LOGGER.warning("Unsupported ICT packet format: %s", packet[5])
            return
        payload = packet[6:-1] if checksum_length else packet[6:]
        pending = self._pending
        if packet[4] == PKT_TYPE_SYSTEM:
            if pending is None or pending.done():
                return
            if payload[:2] == b"\xff\xff":
                code = struct.unpack("<H", payload[2:4])[0] if len(payload) >= 4 else 0xFFFF
                pending.set_exception(ICTNack(code))
            elif payload[:2] == b"\xff\x00" and self._expected is None:
                pending.set_result(True)
        elif packet[4] == PKT_TYPE_DATA:
            blocks = self._decode_blocks(payload)
            if blocks is None:
                _LOGGER.warning("Discarding malformed ICT data packet")
                return
            matched = (pending is not None and not pending.done() and self._expected is not None
                and any((group, idx) == self._expected for group, idx, _ in blocks))
            if not matched:
                await self._send_packet(PKT_TYPE_SYSTEM, b"\xff\x00")
            for _, _, update in blocks:
                self.cache[(update["type"], update["id"])] = update
                self._last_seen[(update["type"], update["id"])] = time.monotonic()
                self._publish(update)
            if matched:
                pending.set_result(True)

    def _decode_blocks(self, data):
        blocks = []
        offset = 0
        while offset < len(data):
            if data[offset:offset+2] == b"\xff\xff":
                break
            if len(data) - offset < 3:
                return None
            low, high, length = data[offset:offset+3]
            end = offset + 3 + length
            if end > len(data):
                return None
            body = data[offset+3:end]
            offset = end
            if low != 0 or high not in MIN_BODY_LENGTH:
                continue
            if len(body) < MIN_BODY_LENGTH[high]:
                return None
            idx = struct.unpack("<I", body[:4])[0]
            update = self._decode_status(high, idx, body)
            if update is None:
                return None
            blocks.append((high, idx, update))
        return blocks

    @staticmethod
    def _decode_status(group, idx, body):
        update = {"type": KIND_BY_GROUP[group], "id": idx}
        if group == 1:
            if body[4] > 9 or body[5] > 4:
                return None
            update.update(locked=body[4] == 0, open=body[5] != 0)
        elif group == 2:
            state, tamper, flags = body[4:7]
            if state not in (*range(6), *range(0x80, 0x85)) or tamper not in (0, 1, 0x80):
                return None
            alarm_state = {0: "disarmed", 0x81: "arming", 0x82: "pending",
                           0x83: "disarming"}.get(state)
            if state == 0x80:
                alarm_state = "armed_home" if flags & 0x40 else "armed_away"
            if flags & 1:
                alarm_state = "triggered"
            reasons = {1: "Inputs open", 2: "Trouble condition", 3: "Bypass error",
                       4: "Bypass warning", 5: "User count not zero", 0x84: "Waiting for second code"}
            update.update(alarm_state=alarm_state, area_state=state,
                status_text=reasons.get(state, alarm_state or "Unknown"),
                tamper_24h_state={0: "disarmed", 1: "busy", 0x80: "armed"}[tamper],
                force_armed=bool(flags & 0x10), instant_armed=bool(flags & 0x20),
                stay_armed=bool(flags & 0x40), alarm=bool(flags & 1))
        elif group == 3:
            if body[12] > 4:
                return None
            update["on"] = body[12] != 0
        else:
            state, flags = body[12:14]
            if state > 3:
                return None
            bypassed = bool(flags & 1)
            mode = "Unbypassed" if not bypassed else (
                "Permanent Bypass" if flags & 2 else "Temporary Bypass")
            update.update(on=state != 0, status=("Closed", "Open", "Short Circuit", "Tamper")[state],
                bypassed=bypassed, bypass_mode=mode)
        return update

    async def disconnect(self):
        # Another task may already be closing this socket. Its listener's finally
        # block must not recursively wait on the task that is cancelling it.
        if self._closing:
            return
        self._closing = True
        self._disconnected.clear()
        try:
            self._connected = self._ready = False
            self.cache.clear()
            self._last_seen.clear()
            self._publish({"type": "connection"})
            if self._pending is not None and not self._pending.done():
                self._pending.set_exception(ICTError("ICT connection lost; command outcome is unknown"))
            writer, self._writer = self._writer, None
            listener, self._listener = self._listener, None
            if listener is not None and listener is not asyncio.current_task():
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), RESPONSE_TIMEOUT)
                except (OSError, asyncio.TimeoutError):
                    pass
        finally:
            self._closing = False
            self._disconnected.set()
