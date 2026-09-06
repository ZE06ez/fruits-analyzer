from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from serial_service import SerialService
from stm32_controller import Stm32ControllerAdapter
from stm32_protocol import FilterWheelMapping, Stm32ProtocolProfile


def load_profile(path: str | None, *, status_layout: str, crc_includes_header: bool) -> Stm32ProtocolProfile:
    if not path:
        raise SystemExit(
            "AA55 当前固件命令号必须来自 STM32 源码/交接 profile。"
            "请传入 --profile-json；本脚本不会猜测 CMD 数值。"
        )
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
        fan_set_cmd=_optional_int(data, "fanSetCmd"),
        led_set_cmd=_optional_int(data, "ledSetCmd"),
        door_cmd=_optional_int(data, "doorCmd"),
        status_layout=str(data.get("statusLayout") or status_layout),
        crc_includes_header=bool(data.get("crcIncludesHeader", crc_includes_header)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Safe STM32 current-firmware host validation helper."
    )
    parser.add_argument("--port", required=True, help="STM32 serial port, e.g. COM5.")
    parser.add_argument("--profile-json", default=None, help="JSON profile derived from current STM32 firmware headers.")
    parser.add_argument("--status-layout", default="opaque", choices=("opaque", "basic_v1"))
    parser.add_argument("--crc-excludes-header", action="store_true")
    parser.add_argument("--listen-seconds", type=float, default=1.0)
    parser.add_argument("--query-status", action="store_true")
    parser.add_argument("--fan", choices=("on", "off"))
    parser.add_argument("--led-mask", type=_int_auto, default=None)
    parser.add_argument("--door", choices=("open", "close", "stop"))
    parser.add_argument("--set-origin", action="store_true")
    parser.add_argument("--move-rel-slots", type=int, default=0)
    parser.add_argument("--allow-motion", action="store_true")
    parser.add_argument("--move-payload-units", default="pulses_i32_le")
    args = parser.parse_args(argv)

    motion_requested = bool(args.set_origin or args.move_rel_slots)
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
            filter_wheel=FilterWheelMapping(move_payload_units=args.move_payload_units),
        )
        print(f"Connected: {args.port}")
        print("Listening for STATUS/INFO frames...")
        deadline = time.monotonic() + max(0.0, args.listen_seconds)
        while time.monotonic() < deadline:
            for frame in adapter.listen_once(timeout_s=0.1):
                print(frame.to_dict())

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
            print({"originEstablished": True, "automaticHoming": False})
        if args.move_rel_slots:
            adapter.move_filter_wheel_relative_slots(args.move_rel_slots)
            print({"moveRelSlots": args.move_rel_slots})
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


if __name__ == "__main__":
    sys.exit(main())
