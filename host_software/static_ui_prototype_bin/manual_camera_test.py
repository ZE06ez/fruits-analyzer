from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

from camera_service import CameraError, Dvp2MonoCamera, RgbCameraConfig, RgbUvcCamera


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


def run_multispectral_test(
    *,
    sdk_dir: str | None,
    serial: str | None,
    exposure: float | None,
    gain: float | None,
    save: bool,
    frames: int,
) -> int:
    camera = Dvp2MonoCamera(sdk_dir=sdk_dir, serial_number=serial)
    try:
        print("DVP2 Multispectral Camera")
        listing_camera = Dvp2MonoCamera(sdk_dir=sdk_dir, serial_number=serial)
        try:
            devices = listing_camera.list_devices()
            print(f"devices: {len(devices)}")
            for device in devices:
                print(f"  index={device.device_index} name={device.device_name} stable_id={device.stable_id} transport={device.transport}")
        finally:
            listing_camera.close()
        print("probe_open_with_timeout: start", flush=True)
        if not camera.probe_available():
            status = camera.get_status().to_dict()
            print("probe_open_with_timeout: failed", flush=True)
            print(f"error: {status.get('error')}", flush=True)
            print(f"technicalError: {status.get('technicalError')}", flush=True)
            return 2
        print("probe_open_with_timeout: passed", flush=True)
        camera.open()
        if exposure is not None:
            camera.set_exposure(exposure)
        if gain is not None:
            camera.set_gain(gain)
        status = camera.get_status().to_dict()
        print()
        _print_section("requested", status.get("requested") or {})
        _print_section("actual", status.get("actual") or {})
        _print_section("capabilities", status.get("capabilities") or {})
        print()

        camera.start_stream()
        frame = camera.capture_frame()
        _print_frame_stats("frame", frame)
        if frames > 1:
            print()
            _run_multispectral_frame_loop(camera, frames)

        if save:
            from PIL import Image
            import numpy as np

            out_dir = Path(tempfile.mkdtemp(prefix="fruit_dvp2_camera_test_"))
            out_path = out_dir / "dvp2_mono_test.png"
            array = np.asarray(frame.data)
            Image.fromarray(array).save(out_path)
            print(f"Saved test image: {out_path}")
        return 0
    except CameraError as exc:
        print(f"Camera error: {exc.user_message}")
        print(f"Technical detail: {exc.technical_message}")
        return 2
    finally:
        camera.stop_stream()
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


def _run_multispectral_frame_loop(camera: Dvp2MonoCamera, frames: int) -> None:
    success = 0
    failure = 0
    intervals: list[float] = []
    first_frame_id = None
    last_frame_id = None
    last = time.perf_counter()
    for index in range(frames):
        now = time.perf_counter()
        intervals.append(now - last)
        last = now
        try:
            frame = camera.capture_frame()
            frame_id = frame.metadata.get("frameId")
            if first_frame_id is None:
                first_frame_id = frame_id
            last_frame_id = frame_id
            if index == 0:
                _print_frame_stats("first_loop_frame", frame)
            success += 1
        except CameraError as exc:
            failure += 1
            print(f"frame_{index}_error: {exc.user_message} / {exc.technical_message}")
    average = sum(intervals[1:], 0.0) / max(len(intervals) - 1, 1)
    status = camera.get_status().to_dict()
    print("stability:")
    print(f"frames: {frames}")
    print(f"success: {success}")
    print(f"failure: {failure}")
    print(f"average_frame_interval_ms: {average * 1000:.2f}")
    print(f"first_frame_id: {first_frame_id if first_frame_id is not None else '--'}")
    print(f"last_frame_id: {last_frame_id if last_frame_id is not None else '--'}")
    frame_count = (status.get("capabilities") or {}).get("frameCount") or {}
    if frame_count:
        print(f"sdk_stream_fps: {frame_count.get('frameRate', '--')}")
        print(f"frame_drop: {frame_count.get('frameDrop', '--')}")
        print(f"frame_error: {frame_count.get('frameError', '--')}")
        print(f"frame_resend: {frame_count.get('frameResend', '--')}")
    _print_section("post_loop_actual", status.get("actual") or {})


def _print_frame_stats(name: str, frame) -> None:
    import numpy as np

    array = np.asarray(frame.data)
    print(f"{name}:")
    print(f"  shape: {frame.shape}")
    print(f"  dtype: {frame.dtype}")
    print(f"  color_space: {frame.color_space}")
    print(f"  min: {array.min() if array.size else '--'}")
    print(f"  max: {array.max() if array.size else '--'}")
    print(f"  mean: {float(array.mean()):.2f}" if array.size else "  mean: --")
    _print_section("  metadata", frame.metadata)


def _print_section(name: str, values: dict) -> None:
    print(f"{name}:")
    for key, value in values.items():
        print(f"  {key}: {value}")
    if not values:
        print("  --")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual camera verification; not used by unittest.")
    parser.add_argument("--rgb", action="store_true", help="Test RGB UVC camera through OpenCV DirectShow.")
    parser.add_argument("--multispectral", action="store_true", help="Test DO3THINK DVP2 GigE monochrome camera.")
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
    parser.add_argument("--sdk-dir", default=None, help="DVP2 SDK root directory, for example: D:\\Netease\\DVP2 SDK CN.")
    parser.add_argument("--serial", default=None, help="DVP2 camera serial number, default: GP23400004963.")
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
    if args.multispectral:
        return run_multispectral_test(
            sdk_dir=args.sdk_dir,
            serial=args.serial,
            exposure=args.exposure,
            gain=args.gain,
            save=args.save,
            frames=max(1, args.frames),
        )
    parser.error("Choose a manual test, for example: --rgb or --multispectral")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
