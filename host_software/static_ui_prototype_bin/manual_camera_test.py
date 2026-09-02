from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

from camera_service import CameraError, RgbCameraConfig, RgbUvcCamera


def run_rgb_test(config: RgbCameraConfig, save: bool, frames: int) -> int:
    camera = RgbUvcCamera(config=config)
    try:
        camera.open()
        status = camera.get_status().to_dict()
        print("RGB Camera")
        print(f"device_index: {config.device_index}")
        print(f"opened: {camera.is_open}")
        print()
        _print_section("requested", status.get("requested") or {})
        _print_section("actual", status.get("actual") or {})
        _print_section("capabilities", status.get("capabilities") or {})
        print()

        frame = camera.capture_frame()
        print("frame:")
        print(f"{frame.shape} {frame.dtype} {frame.color_space}")

        if frames > 1:
            print()
            _run_frame_loop(camera, frames)

        if save:
            from PIL import Image

            out_dir = Path(tempfile.mkdtemp(prefix="fruit_rgb_camera_test_"))
            out_path = out_dir / "rgb_test.png"
            Image.fromarray(frame.data).save(out_path)
            print(f"Saved test image: {out_path}")

        camera.close()
        print()
        print("reopen:")
        camera.open()
        frame = camera.capture_frame()
        print(f"capture_after_reopen: true, shape={frame.shape}, dtype={frame.dtype}, color_space={frame.color_space}")
        return 0
    except CameraError as exc:
        print(f"Camera error: {exc.user_message}")
        print(f"Technical detail: {exc.technical_message}")
        return 2
    finally:
        camera.close()


def _run_frame_loop(camera: RgbUvcCamera, frames: int) -> None:
    success = 0
    failure = 0
    intervals: list[float] = []
    last = time.perf_counter()
    for _ in range(frames):
        now = time.perf_counter()
        intervals.append(now - last)
        last = now
        try:
            camera.capture_frame()
            success += 1
        except CameraError:
            failure += 1
    average = sum(intervals[1:], 0.0) / max(len(intervals) - 1, 1)
    print("stability:")
    print(f"frames: {frames}")
    print(f"success: {success}")
    print(f"failure: {failure}")
    print(f"average_frame_interval_ms: {average * 1000:.2f}")


def _print_section(name: str, values: dict) -> None:
    print(f"{name}:")
    for key, value in values.items():
        print(f"  {key}: {value}")
    if not values:
        print("  --")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual camera verification; not used by unittest.")
    parser.add_argument("--rgb", action="store_true", help="Test RGB UVC camera through OpenCV DirectShow.")
    parser.add_argument("--device-index", type=int, default=1, help="OpenCV device index, default: 1 for the current development PC.")
    parser.add_argument("--width", type=int, default=3840, help="Requested width, default: 3840.")
    parser.add_argument("--height", type=int, default=2160, help="Requested height, default: 2160.")
    parser.add_argument("--fps", type=float, default=25.0, help="Requested FPS, default: 25.")
    parser.add_argument("--fourcc", default="MJPG", help="Requested FOURCC, default: MJPG.")
    parser.add_argument("--exposure", type=float, default=None, help="Requested exposure value.")
    parser.add_argument("--gain", type=float, default=None, help="Requested gain value.")
    parser.add_argument("--white-balance", type=float, default=None, help="Requested white balance temperature/value.")
    parser.add_argument("--auto-exposure", type=float, default=None, help="Requested OpenCV auto exposure value.")
    parser.add_argument("--auto-white-balance", type=float, default=None, help="Requested OpenCV auto white balance value.")
    parser.add_argument("--frames", type=int, default=1, help="Number of frames to capture for stability test.")
    parser.add_argument("--save", action="store_true", help="Save one frame to a temporary test directory.")
    args = parser.parse_args()

    if args.rgb:
        config = RgbCameraConfig(
            device_index=args.device_index,
            width=args.width,
            height=args.height,
            fps=args.fps,
            fourcc=args.fourcc,
            exposure=args.exposure,
            gain=args.gain,
            white_balance=args.white_balance,
            auto_exposure=args.auto_exposure,
            auto_white_balance=args.auto_white_balance,
        )
        return run_rgb_test(config, args.save, max(1, args.frames))
    parser.error("Choose a manual test, for example: --rgb")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
