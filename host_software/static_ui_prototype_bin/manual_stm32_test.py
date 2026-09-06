from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from serial_service import SerialService
from stm32_controller import Stm32ControllerAdapter
from stm32_protocol import (
    CURRENT_FILTER_WHEEL_MAPPING,
    CURRENT_STM32_FIRMWARE_PROFILE,
    FilterWheelMapping,
    Stm32ProtocolProfile,
)


def load_profile(path: str | None, *, status_layout: str, crc_includes_header: bool) -> Stm32ProtocolProfile:
    if not path:
        return CURRENT_STM32_FIRMWARE_PROFILE
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Stm32ProtocolProfile(
        ack_cmd=_required_int(data, "ackCmd"),
        status_cmd=_required_int(data, "statusCmd"),
        info_cmd=_required_int(data, "infoCmd"),
        query_status_cmd=_required_int(data, "queryStatusCmd"),
        move_abs_cmd=_required_int(data, "moveAbsCmd"),
        move_rel_cmd=_required_int(data, "moveRelCmd"),
        stop_cmd=_required_int(data, "stopCmd"),
        set_profile_cmd=_required_int(data, "setProfileCmd"),
        set_origin_cmd=_required_int(data, "setOriginCmd"),
        set_pos_pid_cmd=_optional_int(data, "setPosPidCmd"),
        set_vel_pid_cmd=_optional_int(data, "setVelPidCmd"),
        set_config_cmd=_optional_int(data, "setConfigCmd"),
        reset_cmd=_optional_int(data, "resetCmd"),
        fan_set_cmd=_optional_int(data, "fanSetCmd"),
        led_set_cmd=_optional_int(data, "ledSetCmd"),
        door_cmd=_optional_int(data, "doorCmd"),
        status_layout=str(data.get("statusLayout") or status_layout),
        info_layout=str(data.get("infoLayout") or "current_v1"),
        crc_includes_header=bool(data.get("crcIncludesHeader", crc_includes_header)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Safe STM32 current-firmware host validation helper."
    )
    parser.add_argument("--port", required=True, help="STM32 serial port, e.g. COM5.")
    parser.add_argument("--profile-json", default=None, help="Optional JSON profile override for a future firmware revision.")
    parser.add_argument("--status-layout", default="current_v1", choices=("opaque", "basic_v1", "current_v1"))
    parser.add_argument("--crc-excludes-header", action="store_true")
    parser.add_argument("--listen-seconds", type=float, default=1.0)
    parser.add_argument("--query-status", dest="query_status", action="store_true", default=True)
    parser.add_argument("--no-query-status", dest="query_status", action="store_false")
    parser.add_argument("--fan", choices=("on", "off"))
    parser.add_argument("--led-mask", type=_int_auto, default=None)
    parser.add_argument("--door", choices=("open", "close", "stop"))
    parser.add_argument("--set-origin", action="store_true")
    parser.add_argument("--move-rel-slots", type=int, default=0)
    parser.add_argument("--validate-wheel-22p5", action="store_true")
    parser.add_argument("--allow-motion", action="store_true")
    parser.add_argument("--move-payload-units", default=CURRENT_FILTER_WHEEL_MAPPING.move_payload_units)
    args = parser.parse_args(argv)

    motion_requested = bool(args.set_origin or args.move_rel_slots or args.validate_wheel_22p5)
    if motion_requested and not args.allow_motion:
        raise SystemExit("Motion commands require --allow-motion after confirming wheel clearance.")

    profile = load_profile(
        args.profile_json,
        status_layout=args.status_layout,
        crc_includes_header=not args.crc_excludes_header,
    )
    serial_service = SerialService()
    serial_service.connect(args.port)
    try:
        adapter = Stm32ControllerAdapter(
            serial_service,
            profile=profile,
            filter_wheel=FilterWheelMapping(
                move_payload_units=args.move_payload_units,
                default_motion_rpm=10.0,
            ),
        )
        print(f"Connected: {args.port}")
        print("Listening for STATUS/INFO frames...")
        deadline = time.monotonic() + max(0.0, args.listen_seconds)
        while time.monotonic() < deadline:
            for frame in adapter.listen_once(timeout_s=0.1):
                print(frame.to_dict())

        handshake = adapter.handshake(timeout_s=0.8)
        print({"handshake": handshake})
        if args.query_status:
            print({"status": adapter.query_status(timeout_s=0.5).to_dict()})
        if args.fan:
            adapter.set_fan(args.fan == "on")
            print({"fan": args.fan})
        if args.led_mask is not None:
            adapter.set_led_mask(args.led_mask)
            print({"ledMask": args.led_mask})
        if args.door:
            door_value = {"open": 0x00, "close": 0x01, "stop": 0x02}[args.door]
            adapter.door_command(door_value)
            print({"doorCommandAccepted": args.door, "doorEndpointVerified": False})
        if args.set_origin:
            adapter.set_origin()
            print(adapter.set_origin_semantics())
        if args.move_rel_slots:
            adapter.move_filter_wheel_relative_slots(args.move_rel_slots)
            print({"moveRelSlots": args.move_rel_slots})
        if args.validate_wheel_22p5:
            run_safe_wheel_22p5_validation(adapter)
        if adapter.status_cache is not None:
            print({"latestStatus": adapter.status_cache.to_dict()})
        return 0
    finally:
        serial_service.disconnect()


def _required_int(data: dict[str, Any], key: str) -> int:
    if key not in data:
        raise SystemExit(f"profile missing required key: {key}")
    return _int_auto(data[key])


def _optional_int(data: dict[str, Any], key: str) -> int | None:
    if key not in data or data[key] is None:
        return None
    return _int_auto(data[key])


def _int_auto(value: Any) -> int:
    if isinstance(value, bool):
        raise argparse.ArgumentTypeError("boolean is not an integer command value")
    if isinstance(value, int):
        return value
    return int(str(value), 0)


def run_safe_wheel_22p5_validation(adapter: Stm32ControllerAdapter) -> None:
    print("Confirm manually before this run: filter 1 is aligned and the wheel has no mechanical interference.")
    before = adapter.query_status(timeout_s=0.5)
    print({"before": before.to_dict()})
    adapter.set_origin()
    origin_status = adapter.query_status(timeout_s=0.5)
    print({"originEstablished": True, "automaticHoming": False, "status": origin_status.to_dict()})
    forward = adapter.move_filter_wheel_relative_slots(1, timeout_s=8.0, max_retries=1)
    forward_status = adapter.query_status(timeout_s=0.5)
    print({"moveRelDeg": 22.5, "rpm": 10.0, "result": forward.ok(), "status": forward_status.to_dict()})
    back = adapter.move_filter_wheel_relative_slots(-1, timeout_s=8.0, max_retries=1)
    back_status = adapter.query_status(timeout_s=0.5)
    print({"moveRelDeg": -22.5, "rpm": 10.0, "result": back.ok(), "status": back_status.to_dict()})
    adapter.stop_filter_wheel(timeout_s=0.5)
    print({"finalStopSent": True})


if __name__ == "__main__":
    sys.exit(main())
