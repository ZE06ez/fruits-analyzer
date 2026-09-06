from __future__ import annotations

import struct
import unittest

from stm32_protocol import (
    Aa55Codec,
    Aa55StreamParser,
    CURRENT_FILTER_WHEEL_MAPPING,
    CURRENT_STM32_FIRMWARE_PROFILE,
    FilterWheelMapping,
    Stm32FrameKind,
    Stm32ProtocolProfile,
    ack_echo_cmd,
    crc16_ccitt_false,
    decode_info_payload,
    decode_status_payload,
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


def current_info_payload(firmware_version=7, ppr=1600.0, max_rpm=500.0, acc=320.0):
    return bytes((firmware_version,)) + struct.pack("<fff", float(ppr), float(max_rpm), float(acc))


class Stm32ProtocolTests(unittest.TestCase):
    def test_crc16_ccitt_false_known_vector(self):
        self.assertEqual(crc16_ccitt_false(b"123456789"), 0x29B1)

    def test_encode_decode_ack_frame(self):
        codec = Aa55Codec(PROFILE)
        raw = codec.encode(PROFILE.ack_cmd, bytes((PROFILE.move_rel_cmd, 0x00)))

        frame = codec.decode(raw)

        self.assertEqual(frame.kind, Stm32FrameKind.ACK)
        self.assertEqual(ack_echo_cmd(frame), PROFILE.move_rel_cmd)

    def test_stream_parser_handles_partial_multiple_and_noise(self):
        codec = Aa55Codec(PROFILE)
        parser = Aa55StreamParser(codec)
        status = codec.encode(PROFILE.status_cmd, status_payload(position_cdeg=2250))
        info = codec.encode(PROFILE.info_cmd, b"ok")

        self.assertEqual(parser.feed(b"\x00noise" + status[:4]), [])
        frames = parser.feed(status[4:] + info)

        self.assertEqual(parser.noise_bytes_dropped, 6)
        self.assertEqual([frame.kind for frame in frames], [Stm32FrameKind.STATUS, Stm32FrameKind.INFO])

    def test_stream_parser_drops_bad_crc_frame_and_resyncs(self):
        codec = Aa55Codec(PROFILE)
        parser = Aa55StreamParser(codec)
        bad = bytearray(codec.encode(PROFILE.info_cmd, b"bad"))
        bad[-1] ^= 0x01
        good = codec.encode(PROFILE.info_cmd, b"good")

        frames = parser.feed(bytes(bad) + good)

        self.assertEqual(parser.crc_errors, 1)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].payload, b"good")

    def test_basic_status_payload_decode_and_logical_slot(self):
        payload = status_payload(
            state=1,
            position_cdeg=4500,
            velocity_crpm=1000,
            target_cdeg=6750,
            fan=1,
            led=0x04,
            error=0,
        )

        status = decode_status_payload(payload, layout="basic_v1")

        self.assertEqual(status.motor_state, "moving")
        self.assertEqual(status.position_deg, 45.0)
        self.assertEqual(status.velocity_rpm, 10.0)
        self.assertEqual(status.target_deg, 67.5)
        self.assertTrue(status.fan_on)
        self.assertEqual(status.led_mask, 0x04)
        self.assertEqual(status.logical_slot(slots_per_rev=16), 2)

    def test_current_firmware_profile_ids_and_crc_scope_are_bound(self):
        profile = CURRENT_STM32_FIRMWARE_PROFILE
        self.assertEqual(profile.move_abs_cmd, 0x01)
        self.assertEqual(profile.move_rel_cmd, 0x02)
        self.assertEqual(profile.stop_cmd, 0x03)
        self.assertEqual(profile.set_pos_pid_cmd, 0x04)
        self.assertEqual(profile.set_vel_pid_cmd, 0x05)
        self.assertEqual(profile.set_profile_cmd, 0x06)
        self.assertEqual(profile.set_config_cmd, 0x07)
        self.assertEqual(profile.query_status_cmd, 0x08)
        self.assertEqual(profile.set_origin_cmd, 0x09)
        self.assertEqual(profile.reset_cmd, 0x0F)
        self.assertEqual(profile.fan_set_cmd, 0x10)
        self.assertEqual(profile.door_cmd, 0x11)
        self.assertEqual(profile.led_set_cmd, 0x12)
        self.assertEqual(profile.ack_cmd, 0x80)
        self.assertEqual(profile.status_cmd, 0x81)
        self.assertEqual(profile.info_cmd, 0x82)
        self.assertEqual(profile.status_layout, "current_v1")
        self.assertEqual(profile.info_layout, "current_v1")
        self.assertFalse(profile.crc_includes_header)

        codec = Aa55Codec(profile)
        raw = codec.encode(profile.fan_set_cmd, b"\x64")
        body = raw[2:-2]
        self.assertEqual(raw[-2:], crc16_ccitt_false(body).to_bytes(2, "big"))
        self.assertNotEqual(raw[-2:], crc16_ccitt_false(raw[:-2]).to_bytes(2, "big"))

    def test_current_status_payload_decode_exact_offsets(self):
        payload = current_status_payload(
            state=3,
            error=4,
            position_deg=22.5,
            velocity_rpm=10.0,
            target_deg=45.0,
            fan=100,
            led1=0,
            led2=80,
            led3=100,
        )

        status = decode_status_payload(payload, layout="current_v1")

        self.assertEqual(len(payload), 18)
        self.assertEqual(status.motor_state, "done")
        self.assertAlmostEqual(status.position_deg, 22.5)
        self.assertAlmostEqual(status.velocity_rpm, 10.0)
        self.assertAlmostEqual(status.target_deg, 45.0)
        self.assertEqual(status.error_code, 4)
        self.assertTrue(status.fan_on)
        self.assertEqual(status.fan_duty, 100)
        self.assertEqual(status.led_mask, 0x06)
        self.assertEqual(status.led1_duty, 0)
        self.assertEqual(status.led2_duty, 80)
        self.assertEqual(status.led3_duty, 100)
        self.assertEqual(status.logical_slot(slots_per_rev=16), 1)

    def test_current_info_payload_decode_exact_offsets(self):
        payload = current_info_payload(firmware_version=3, ppr=1600.0, max_rpm=500.0, acc=250.0)

        info = decode_info_payload(payload, layout="current_v1")

        self.assertEqual(len(payload), 13)
        self.assertEqual(info.firmware_version, 3)
        self.assertAlmostEqual(info.ppr, 1600.0)
        self.assertAlmostEqual(info.max_rpm, 500.0)
        self.assertAlmostEqual(info.acc, 250.0)

    def test_filter_wheel_mapping_keeps_slot_to_mechanical_conversion_out_of_coordinator(self):
        mapping = CURRENT_FILTER_WHEEL_MAPPING

        self.assertEqual(mapping.degrees_per_slot, 22.5)
        self.assertEqual(mapping.pulses_per_slot, 100)
        self.assertEqual(mapping.relative_payload(1), struct.pack("<ff", 22.5, 10.0))
        self.assertEqual(mapping.absolute_payload(2), struct.pack("<ff", 45.0, 10.0))

    def test_legacy_pulse_filter_wheel_mapping_remains_available(self):
        mapping = FilterWheelMapping(slots_per_rev=16, pulses_per_rev=1600, move_payload_units="pulses_i32_le")

        self.assertEqual(mapping.relative_payload(1), (100).to_bytes(4, "little", signed=True))


if __name__ == "__main__":
    unittest.main()
