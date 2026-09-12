from __future__ import annotations

import struct
import threading
import unittest

from stm32_controller import Stm32ControllerAdapter
from stm32_protocol import (
    Aa55Codec,
    CURRENT_FILTER_WHEEL_MAPPING,
    CURRENT_STM32_FIRMWARE_PROFILE,
    FilterWheelMapping,
    Stm32ProtocolProfile,
)


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


def current_status_payload(state=0, error=0, position_deg=0.0, velocity_rpm=0.0, target_deg=0.0, fan=0, led1=0, led2=0, led3=0):
    return (
        bytes((state, error))
        + struct.pack("<fff", float(position_deg), float(velocity_rpm), float(target_deg))
        + bytes((fan, led1, led2, led3))
    )


def current_info_payload(firmware_version=1, ppr=1600.0, max_rpm=500.0, acc=300.0):
    return bytes((firmware_version,)) + struct.pack("<fff", float(ppr), float(max_rpm), float(acc))


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
                codec.encode(PROFILE.status_cmd, status_payload(state=2, position_cdeg=2250, target_cdeg=2250)),
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
                codec.encode(PROFILE.status_cmd, status_payload(state=2, position_cdeg=2250, target_cdeg=2250)),
            ]
        )
        adapter = self.make_adapter(fake)
        adapter.listen_once(timeout_s=0.01)

        result = adapter.move_filter_wheel_relative_slots(1, max_retries=1)

        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(fake.writes), 2)

    def test_ack_ok_but_position_unchanged_fails_and_sends_stop(self):
        codec = Aa55Codec(PROFILE)
        fake = FakeRawSerial(
            [
                codec.encode(PROFILE.status_cmd, status_payload(state=0, position_cdeg=0, target_cdeg=0)),
                codec.encode(PROFILE.ack_cmd, bytes((PROFILE.move_rel_cmd, 0x00))),
                codec.encode(PROFILE.status_cmd, status_payload(state=2, position_cdeg=0, target_cdeg=0)),
                codec.encode(PROFILE.ack_cmd, bytes((PROFILE.stop_cmd, 0x00))),
            ]
        )
        adapter = self.make_adapter(fake)
        adapter.listen_once(timeout_s=0.01)

        result = adapter.move_filter_wheel_relative_slots(1, max_retries=0)

        self.assertFalse(result.ok())
        self.assertEqual(result.failure_reason, "motion_not_started")
        self.assertEqual(codec.decode(fake.writes[-1]).cmd, PROFILE.stop_cmd)

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

    def test_legacy_tungsten_compatibility_uses_led_set_mask(self):
        codec = Aa55Codec(PROFILE)
        fake = FakeRawSerial([
            codec.encode(PROFILE.ack_cmd, bytes((PROFILE.led_set_cmd, 0x00))),
        ])
        adapter = self.make_adapter(fake)

        self.assertEqual(
            adapter.send_command(adapter.LEGACY_TUNGSTEN_SET, 0x01),
            0x00,
        )
        frame = codec.decode(fake.writes[-1])
        self.assertEqual(frame.cmd, PROFILE.led_set_cmd)
        self.assertEqual(frame.payload, b"\x01")

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

    def test_current_query_status_waits_for_status_not_ack(self):
        profile = CURRENT_STM32_FIRMWARE_PROFILE
        codec = Aa55Codec(profile)
        fake = FakeRawSerial(
            [
                codec.encode(profile.ack_cmd, bytes((profile.query_status_cmd, 0x00))),
                codec.encode(profile.status_cmd, current_status_payload(state=0, position_deg=22.5)),
            ]
        )
        adapter = Stm32ControllerAdapter(fake, profile=profile, filter_wheel=CURRENT_FILTER_WHEEL_MAPPING)

        status = adapter.query_status(timeout_s=0.05)

        self.assertEqual(codec.decode(fake.writes[0]).cmd, profile.query_status_cmd)
        self.assertEqual(codec.decode(fake.writes[0]).payload, b"")
        self.assertAlmostEqual(status.position_deg, 22.5)

    def test_current_fan_led_door_payloads_are_aa55(self):
        profile = CURRENT_STM32_FIRMWARE_PROFILE
        codec = Aa55Codec(profile)
        fake = FakeRawSerial(
            [
                codec.encode(profile.ack_cmd, bytes((profile.fan_set_cmd, 0))),
                codec.encode(profile.ack_cmd, bytes((profile.led_set_cmd, 0))),
                codec.encode(profile.ack_cmd, bytes((profile.door_cmd, 0))),
            ]
        )
        adapter = Stm32ControllerAdapter(fake, profile=profile, filter_wheel=CURRENT_FILTER_WHEEL_MAPPING)

        adapter.set_fan(True, timeout_s=0.05)
        adapter.set_led_mask(0x07, timeout_s=0.05)
        adapter.door_command(0x02, timeout_s=0.05)

        fan_frame = codec.decode(fake.writes[0])
        led_frame = codec.decode(fake.writes[1])
        door_frame = codec.decode(fake.writes[2])
        self.assertEqual((fan_frame.cmd, fan_frame.payload), (profile.fan_set_cmd, b"\x64"))
        self.assertEqual((led_frame.cmd, led_frame.payload), (profile.led_set_cmd, b"\x07"))
        self.assertEqual((door_frame.cmd, door_frame.payload), (profile.door_cmd, b"\x02"))

    def test_current_move_payloads_are_degrees_and_rpm_float32(self):
        profile = CURRENT_STM32_FIRMWARE_PROFILE
        codec = Aa55Codec(profile)
        fake = FakeRawSerial(
            [
                codec.encode(profile.ack_cmd, bytes((profile.move_rel_cmd, 0))),
                codec.encode(profile.status_cmd, current_status_payload(state=3, position_deg=22.5, target_deg=22.5)),
                codec.encode(profile.ack_cmd, bytes((profile.move_abs_cmd, 0))),
            ]
        )
        adapter = Stm32ControllerAdapter(
            fake,
            profile=profile,
            filter_wheel=CURRENT_FILTER_WHEEL_MAPPING,
            motion_timeout_s=0.05,
        )

        relative = adapter.move_filter_wheel_relative_slots(1, timeout_s=0.05)
        absolute = adapter.move_filter_wheel_absolute_slot(2, timeout_s=0.05)

        rel_frame = codec.decode(fake.writes[0])
        abs_frame = codec.decode(fake.writes[1])
        self.assertTrue(relative.completed_from_status)
        self.assertTrue(absolute.ok())
        self.assertEqual(rel_frame.payload, struct.pack("<ff", 22.5, 10.0))
        self.assertEqual(abs_frame.payload, struct.pack("<ff", 45.0, 10.0))

    def test_handshake_accepts_unsolicited_info_status_and_ready_noise(self):
        profile = CURRENT_STM32_FIRMWARE_PROFILE
        codec = Aa55Codec(profile)
        fake = FakeRawSerial(
            [
                b"ready\r\n",
                codec.encode(profile.info_cmd, current_info_payload(firmware_version=5, ppr=1600.0)),
                codec.encode(profile.status_cmd, current_status_payload(state=0, position_deg=0.0)),
                codec.encode(profile.status_cmd, current_status_payload(state=0, position_deg=0.0)),
            ]
        )
        adapter = Stm32ControllerAdapter(fake, profile=profile, filter_wheel=CURRENT_FILTER_WHEEL_MAPPING)

        result = adapter.handshake(timeout_s=0.05)

        self.assertTrue(result["currentFirmwareProfileValidated"])
        self.assertEqual(result["info"]["firmware_version"], 5)
        self.assertFalse(result["diagnostics"]["filterWheelPprMismatch"])
        self.assertGreater(adapter.parser.noise_bytes_dropped, 0)

    def test_ppr_mismatch_reports_diagnostic_without_rewriting_config(self):
        profile = CURRENT_STM32_FIRMWARE_PROFILE
        codec = Aa55Codec(profile)
        fake = FakeRawSerial(
            [
                codec.encode(profile.info_cmd, current_info_payload(ppr=800.0)),
            ]
        )
        adapter = Stm32ControllerAdapter(fake, profile=profile, filter_wheel=CURRENT_FILTER_WHEEL_MAPPING)

        adapter.listen_once(timeout_s=0.01)
        diagnostic = adapter.validate_profile_consistency()

        self.assertTrue(diagnostic["filterWheelPprMismatch"])
        self.assertEqual(diagnostic["expectedPpr"], 1600)
        self.assertEqual(diagnostic["reportedPpr"], 800.0)
        self.assertEqual(diagnostic["action"], "manual_confirmation_required")
        self.assertEqual(fake.writes, [])

    def test_set_origin_semantics_are_logical_not_automatic_home(self):
        profile = CURRENT_STM32_FIRMWARE_PROFILE
        codec = Aa55Codec(profile)
        fake = FakeRawSerial(
            [
                codec.encode(profile.ack_cmd, bytes((profile.set_origin_cmd, 0))),
            ]
        )
        adapter = Stm32ControllerAdapter(fake, profile=profile, filter_wheel=CURRENT_FILTER_WHEEL_MAPPING)

        adapter.set_origin(timeout_s=0.05)

        self.assertEqual(adapter.set_origin_semantics(), {"originEstablished": True, "automaticHoming": False})
        self.assertFalse(adapter.automatic_homing_supported)
        self.assertFalse(adapter.physical_encoder_verified)

    def test_position_tolerance_is_centralized_on_filter_mapping(self):
        adapter = Stm32ControllerAdapter(
            FakeRawSerial(),
            profile=CURRENT_STM32_FIRMWARE_PROFILE,
            filter_wheel=CURRENT_FILTER_WHEEL_MAPPING,
        )

        self.assertTrue(adapter.is_position_within_tolerance(359.8, 0.0))
        self.assertFalse(adapter.is_position_within_tolerance(358.0, 0.0))


if __name__ == "__main__":
    unittest.main()
