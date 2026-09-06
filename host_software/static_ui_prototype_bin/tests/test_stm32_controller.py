from __future__ import annotations

import threading
import unittest

from stm32_controller import Stm32ControllerAdapter
from stm32_protocol import Aa55Codec, FilterWheelMapping, Stm32ProtocolProfile


PROFILE = Stm32ProtocolProfile(
    ack_cmd=0x80,
    status_cmd=0x81,
    info_cmd=0x82,
    query_status_cmd=0x20,
    move_abs_cmd=0x21,
    move_rel_cmd=0x22,
    stop_cmd=0x23,
    set_profile_cmd=0x24,
    set_origin_cmd=0x25,
    fan_set_cmd=0x30,
    led_set_cmd=0x31,
    door_cmd=0x32,
    status_layout="basic_v1",
)


def status_payload(state=0, position_cdeg=0, velocity_crpm=0, target_cdeg=0, fan=0, led=0, error=0):
    return (
        bytes((state,))
        + int(position_cdeg).to_bytes(4, "little", signed=True)
        + int(velocity_crpm).to_bytes(2, "little", signed=True)
        + int(target_cdeg).to_bytes(4, "little", signed=True)
        + bytes((fan, led, error))
    )


class FakeRawSerial:
    def __init__(self, reads=None):
        self.is_connected = True
        self.writes = []
        self.write_count = 0
        self.reads = list(reads or [])
        self.lock = threading.Lock()

    def write_bytes(self, data):
        with self.lock:
            self.writes.append(bytes(data))
            self.write_count += 1
        return len(data)

    def read_bytes(self, size, timeout_s=None):
        del timeout_s
        with self.lock:
            if not self.reads:
                return b""
            chunk = self.reads.pop(0)
            if callable(chunk):
                data = chunk(self)
                if data is None:
                    self.reads.insert(0, chunk)
                    return b""
                chunk = data
        return bytes(chunk[:size])

    def clear_buffers(self):
        pass


class Stm32ControllerAdapterTests(unittest.TestCase):
    def make_adapter(self, fake):
        return Stm32ControllerAdapter(
            fake,
            profile=PROFILE,
            filter_wheel=FilterWheelMapping(slots_per_rev=16, pulses_per_rev=1600),
            ack_timeout_s=0.02,
            motion_timeout_s=0.03,
        )

    def test_ack_matching_ignores_interleaved_status_and_wrong_ack(self):
        codec = Aa55Codec(PROFILE)
        fake = FakeRawSerial(
            [
                codec.encode(PROFILE.status_cmd, status_payload(state=0, position_cdeg=0)),
                codec.encode(PROFILE.ack_cmd, bytes((PROFILE.stop_cmd, 0x00))),
                codec.encode(PROFILE.ack_cmd, bytes((PROFILE.move_rel_cmd, 0x00))),
                codec.encode(PROFILE.status_cmd, status_payload(state=2, position_cdeg=2250)),
            ]
        )
        adapter = self.make_adapter(fake)

        result = adapter.move_filter_wheel_relative_slots(1)

        self.assertTrue(result.ack_received)
        self.assertEqual(result.cmd, PROFILE.move_rel_cmd)
        self.assertEqual(len(fake.writes), 1)

    def test_status_aware_retry_only_when_idle_and_position_unchanged(self):
        codec = Aa55Codec(PROFILE)
        fake = FakeRawSerial(
            [
                codec.encode(PROFILE.status_cmd, status_payload(state=0, position_cdeg=0)),
                lambda serial: (
                    codec.encode(PROFILE.ack_cmd, bytes((PROFILE.move_rel_cmd, 0x00)))
                    if serial.write_count >= 2
                    else None
                ),
                codec.encode(PROFILE.status_cmd, status_payload(state=2, position_cdeg=2250)),
            ]
        )
        adapter = self.make_adapter(fake)
        adapter.listen_once(timeout_s=0.01)

        result = adapter.move_filter_wheel_relative_slots(1, max_retries=1)

        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(fake.writes), 2)

    def test_no_duplicate_relative_move_when_status_says_motion_started(self):
        codec = Aa55Codec(PROFILE)
        fake = FakeRawSerial(
            [
                codec.encode(PROFILE.status_cmd, status_payload(state=1, position_cdeg=1000)),
                codec.encode(PROFILE.status_cmd, status_payload(state=2, position_cdeg=2250)),
            ]
        )
        adapter = self.make_adapter(fake)

        result = adapter.move_filter_wheel_relative_slots(1, max_retries=2)

        self.assertFalse(result.ack_received)
        self.assertTrue(result.completed_from_status)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(fake.writes), 1)

    def test_fan_led_can_use_explicit_short_frame_compatibility(self):
        fake = FakeRawSerial()
        profile = Stm32ProtocolProfile(
            ack_cmd=0x80,
            status_cmd=0x81,
            info_cmd=0x82,
            query_status_cmd=0x20,
            move_abs_cmd=0x21,
            move_rel_cmd=0x22,
            stop_cmd=0x23,
            set_profile_cmd=0x24,
            set_origin_cmd=0x25,
            fan_set_cmd=None,
            led_set_cmd=None,
            door_cmd=0x32,
        )
        adapter = Stm32ControllerAdapter(fake, profile=profile)

        adapter.send_command(adapter.LEGACY_FAN_SET, 0x01)
        adapter.send_command(adapter.LEGACY_RGB_LED_SET, 0x04)

        self.assertEqual(fake.writes, [b"\x10\x01", b"\x12\x04"])

    def test_tungsten_nonzero_is_capability_unavailable(self):
        fake = FakeRawSerial()
        adapter = self.make_adapter(fake)

        self.assertEqual(
            adapter.send_command(adapter.LEGACY_TUNGSTEN_SET, 0x01),
            adapter.ERROR_CAPABILITY_UNAVAILABLE,
        )

    def test_safe_stop_reports_each_best_effort_action(self):
        codec = Aa55Codec(PROFILE)
        fake = FakeRawSerial(
            [
                codec.encode(PROFILE.ack_cmd, bytes((PROFILE.stop_cmd, 0x00))),
                codec.encode(PROFILE.ack_cmd, bytes((PROFILE.door_cmd, 0x00))),
                codec.encode(PROFILE.ack_cmd, bytes((PROFILE.led_set_cmd, 0x00))),
                codec.encode(PROFILE.ack_cmd, bytes((PROFILE.fan_set_cmd, 0x00))),
            ]
        )
        adapter = self.make_adapter(fake)

        report = adapter.safe_stop(timeout_s=0.02)

        self.assertTrue(report["ok"])
        self.assertEqual(
            [item["action"] for item in report["actions"]],
            ["filter_wheel_stop", "door_stop", "led_off", "fan_on"],
        )


if __name__ == "__main__":
    unittest.main()
