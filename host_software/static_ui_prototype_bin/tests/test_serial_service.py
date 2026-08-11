vfrom __future__ import annotations

import unittest

from serial_service import (
    ProtocolResponseError,
    SerialNotConnectedError,
    SerialResponseTimeout,
    SerialService,
)


class FakePort:
    def __init__(self, device, description="", hwid=""):
        self.device = device
        self.description = description
        self.hwid = hwid


class FakeSerial:
    def __init__(self, responses=None, **kwargs):
        self.responses = responses or {}
        self.kwargs = kwargs
        self.timeout = kwargs.get("timeout", 0.05)
        self.write_timeout = kwargs.get("write_timeout", 0.05)
        self.is_open = True

        self.writes = []
        self.pending = bytearray()
        self.input_reset_count = 0
        self.output_reset_count = 0

    def reset_input_buffer(self):
        self.input_reset_count += 1
        self.pending.clear()

    def reset_output_buffer(self):
        self.output_reset_count += 1

    def write(self, data):
        packet = bytes(data)
        self.writes.append(packet)
        self.pending.extend(self.responses.get(packet, b""))
        return len(packet)

    def flush(self):
        pass

    def read(self, size):
        chunk = bytes(self.pending[:size])
        del self.pending[:size]
        return chunk

    def close(self):
        self.is_open = False


class FakeSerialFactory:
    def __init__(self, fake_serial):
        self.fake_serial = fake_serial

    def __call__(self, **kwargs):
        self.fake_serial.kwargs.update(kwargs)
        return self.fake_serial


class SerialServiceTests(unittest.TestCase):
    def make_service(self, fake_serial, ports=None):
        return SerialService(
            serial_factory=FakeSerialFactory(fake_serial),
            ports_provider=lambda: ports or [],
        )

    def test_list_ports(self):
        fake = FakeSerial()
        service = self.make_service(
            fake,
            ports=[
                FakePort(
                    "COM3",
                    "STM32 Virtual COM Port",
                    "USB VID:PID=0483:5740",
                )
            ],
        )

        ports = service.list_ports()

        self.assertEqual(len(ports), 1)
        self.assertEqual(ports[0].device, "COM3")
        self.assertEqual(
            ports[0].description,
            "STM32 Virtual COM Port",
        )

    def test_connect_uses_115200_8n1(self):
        fake = FakeSerial()
        service = self.make_service(fake)

        service.connect("COM3")

        self.assertEqual(fake.kwargs["port"], "COM3")
        self.assertEqual(fake.kwargs["baudrate"], 115200)
        self.assertEqual(fake.kwargs["bytesize"], 8)
        self.assertEqual(fake.kwargs["parity"], "N")
        self.assertEqual(fake.kwargs["stopbits"], 1)
        self.assertFalse(fake.kwargs["xonxoff"])
        self.assertFalse(fake.kwargs["rtscts"])
        self.assertFalse(fake.kwargs["dsrdtr"])
        self.assertEqual(fake.input_reset_count, 1)
        self.assertEqual(fake.output_reset_count, 1)

    def test_ping_uses_01_5a_and_accepts_81_5a(self):
        fake = FakeSerial({
            b"\x01\x5A": b"\x81\x5A",
        })
        service = self.make_service(fake)
        service.connect("COM3")

        result = service.ping()

        self.assertTrue(result)
        self.assertEqual(fake.writes, [b"\x01\x5A"])

    def test_normal_command_returns_result(self):
        fake = FakeSerial({
            b"\x10\x01": b"\x90\x00",
        })
        service = self.make_service(fake)
        service.connect("COM3")

        result = service.send_command(
            0x10,
            0x01,
            timeout_s=0.1,
        )

        self.assertEqual(result, 0x00)
        self.assertEqual(fake.writes, [b"\x10\x01"])

    def test_wrong_reply_command_is_rejected(self):
        fake = FakeSerial({
            b"\x10\x01": b"\x91\x00",
        })
        service = self.make_service(fake)
        service.connect("COM3")

        with self.assertRaises(ProtocolResponseError):
            service.send_command(
                0x10,
                0x01,
                timeout_s=0.1,
            )

    def test_timeout_does_not_retry_command(self):
        fake = FakeSerial()
        service = self.make_service(fake)
        service.connect("COM3")

        with self.assertRaises(SerialResponseTimeout):
            service.send_command(
                0x20,
                0x01,
                timeout_s=0.01,
            )

        # 机械命令超时后不能自动重发。
        self.assertEqual(fake.writes, [b"\x20\x01"])

    def test_command_requires_connection(self):
        fake = FakeSerial()
        service = self.make_service(fake)

        with self.assertRaises(SerialNotConnectedError):
            service.send_command(0x10, 0x01)

    def test_disconnect_closes_serial_port(self):
        fake = FakeSerial()
        service = self.make_service(fake)
        service.connect("COM3")

        service.disconnect()

        self.assertFalse(fake.is_open)
        self.assertFalse(service.is_connected)


if __name__ == "__main__":
    unittest.main()
