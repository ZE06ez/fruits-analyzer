from __future__ import annotations

import argparse
import json
from pathlib import Path

from device_manager import CameraIntegrationRequired, DeviceManager


def build_payload(args: argparse.Namespace) -> dict:
    mode = "multi_view" if args.multi_view else "single_view"
    calibration_mode = "capture_new" if args.calibration_new else "none" if args.no_calibration else "existing"
    payload = {
        "sampleId": args.sample_id,
        "captureMode": mode,
        "outputDir": str(Path(args.output_dir).resolve()) if args.output_dir else "",
        "calibrationMode": calibration_mode,
        "calibrationId": args.calibration_id,
        "captureDark": bool(args.calibration_new),
        "captureWhite": bool(args.calibration_new),
        "operatorConfirmedDark": bool(args.operator_confirmed),
        "operatorConfirmedWhite": bool(args.operator_confirmed),
        "requireCalibration": not args.no_calibration,
        "rgbDirName": args.rgb_dir,
        "multispectralDirName": args.multispectral_dir,
        "sampleStageMode": "hardware",
        "returnHome": True,
    }
    if args.band:
        payload["bandPlan"] = [
            {
                "bandId": item.split(":", 2)[0],
                "wheelPosition": int(item.split(":", 2)[1]),
                "wavelengthNm": int(item.split(":", 2)[2]) if len(item.split(":", 2)) > 2 else None,
            }
            for item in args.band
        ]
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual P1B-8 true capture readiness and guarded start tool.")
    parser.add_argument("--sample-id", default="MANUAL_TRUE_CAPTURE")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--readiness", action="store_true")
    parser.add_argument("--single-view", action="store_true")
    parser.add_argument("--multi-view", action="store_true")
    parser.add_argument("--calibration-only", action="store_true")
    parser.add_argument("--calibration-new", action="store_true")
    parser.add_argument("--calibration-id", default="")
    parser.add_argument("--no-calibration", action="store_true")
    parser.add_argument("--operator-confirmed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-hardware", action="store_true")
    parser.add_argument("--rgb-dir", default="rgb")
    parser.add_argument("--multispectral-dir", default="multispectral")
    parser.add_argument("--band", action="append", help="bandId:wheelPosition:wavelengthNm, e.g. A520:1:520")
    args = parser.parse_args()

    manager = DeviceManager()
    payload = build_payload(args)
    readiness = manager.capture_readiness(payload)
    print(json.dumps({"readiness": readiness}, ensure_ascii=False, indent=2))

    if args.readiness or args.dry_run or not (args.single_view or args.multi_view or args.calibration_only):
        return 0
    if not args.allow_hardware:
        print("Refusing to start true hardware capture without --allow-hardware.")
        return 2
    if args.calibration_only:
        print("Use the UI calibration endpoints for operator-gated Dark/White capture; this tool does not bypass confirmation.")
        return 2
    try:
        capture = manager.start_capture(args.sample_id, payload=payload)
    except CameraIntegrationRequired as exc:
        print(json.dumps({"ok": False, "error": str(exc), "readiness": readiness}, ensure_ascii=False, indent=2))
        return 3
    print(json.dumps({"ok": capture.get("state") == "completed", "capture": capture}, ensure_ascii=False, indent=2))
    return 0 if capture.get("state") == "completed" else 4


if __name__ == "__main__":
    raise SystemExit(main())
