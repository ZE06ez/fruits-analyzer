from __future__ import annotations

import unittest

from stm32_protocol import (
    Aa55Codec,
    Aa55StreamParser,
    FilterWheelMapping,
    Stm32FrameKind,
    Stm32ProtocolProfile,
    ack_echo_cmd,
    crc16_ccitt_false,
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

    def test_filter_wheel_mapping_keeps_slot_to_mechanical_conversion_out_of_coordinator(self):
        mapping = FilterWheelMapping(slots_per_rev=16, pulses_per_rev=1600)

        self.assertEqual(mapping.degrees_per_slot, 22.5)
        self.assertEqual(mapping.pulses_per_slot, 100)
        self.assertEqual(mapping.relative_payload(1), (100).to_bytes(4, "little", signed=True))


if __name__ == "__main__":
    unittest.main()
