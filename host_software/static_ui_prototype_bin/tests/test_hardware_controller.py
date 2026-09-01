import unittest

from hardware_controller import (
    CommandFailedError,
    DoorState,
    HardwareController,
    InterlockError,
)


class FakeSerialService:
    def __init__(self):
        self.responses = {}
        self.calls = []
        self.fail_on = None

    def set_response(self, cmd, param, result):
        self.responses[(cmd, param)] = result

    def send_command(self, cmd, param, timeout_s=None):
        self.calls.append((cmd, param, timeout_s))
        if self.fail_on == (cmd, param):
            raise RuntimeError("serial timeout")
        return self.responses.get((cmd, param), 0x00)

    def ping(self, timeout_s=0.5):
        self.calls.append((0x01, 0x5A, timeout_s))
        return True


class HardwareControllerTests(unittest.TestCase):
    def test_fan_on_uses_protocol_command(self):
        serial = FakeSerialService()
        controller = HardwareController(serial)

        controller.fan_on()

        self.assertEqual(serial.calls, [(0x10, 0x01, 0.5)])

    def test_tungsten_requires_fan_and_closed_door(self):
        serial = FakeSerialService()
        serial.set_response(0x30, 0x00, 0x00)  # fan off
        serial.set_response(0x31, 0x00, DoorState.CLOSED)
        controller = HardwareController(serial)

        with self.assertRaises(InterlockError):
            controller.tungsten_set(0x01)

        self.assertNotIn((0x13, 0x01, 0.5), serial.calls)

    def test_tungsten_rejects_rgb_led_conflict(self):
        serial = FakeSerialService()
        serial.set_response(0x30, 0x00, 0b00000111)  # fan + RGB LEDs
        serial.set_response(0x31, 0x00, DoorState.CLOSED)
        controller = HardwareController(serial)

        with self.assertRaises(InterlockError):
            controller.tungsten_set(0x01)

    def test_rgb_led_rejects_tungsten_conflict(self):
        serial = FakeSerialService()
        serial.set_response(0x30, 0x00, 0b00001001)  # fan + tungsten 1
        serial.set_response(0x31, 0x00, DoorState.CLOSED)
        controller = HardwareController(serial)

        with self.assertRaises(InterlockError):
            controller.rgb_led_set(0x03)

    def test_wheel_relative_encodes_negative_int8(self):
        serial = FakeSerialService()
        serial.set_response(0x33, 0x00, 0x00)
        controller = HardwareController(serial)

        controller.wheel_move_relative(-9)

        self.assertIn((0x20, 0xF7, 5.0), serial.calls)

    def test_query_failure_is_decoded(self):
        serial = FakeSerialService()
        serial.set_response(0x31, 0x00, 0x84)
        controller = HardwareController(serial)

        with self.assertRaises(CommandFailedError) as context:
            controller.get_door_status()

        self.assertEqual(context.exception.result_code, 0x04)

    def test_safe_stop_locks_future_actions_until_fault_clear(self):
        serial = FakeSerialService()
        controller = HardwareController(serial)
        controller.safe_stop()

        with self.assertRaises(InterlockError):
            controller.wheel_home()

        controller.fault_clear()
        serial.set_response(0x33, 0x00, 0x00)
        controller.wheel_home()
        self.assertIn((0x21, 0x00, 10.0), serial.calls)

    def test_control_exception_triggers_best_effort_safe_stop(self):
        serial = FakeSerialService()
        serial.fail_on = (0x20, 0x01)
        serial.set_response(0x33, 0x00, 0x00)
        controller = HardwareController(serial)

        with self.assertRaises(RuntimeError):
            controller.wheel_move_relative(1)

        self.assertIn((0x3E, 0x00, 0.5), serial.calls)


if __name__ == "__main__":
    unittest.main()
