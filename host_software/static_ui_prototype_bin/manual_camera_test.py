from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from camera_service import CameraError, CameraManager, Dvp2MonoCamera, RgbCameraConfig, RgbUvcCamera
from capture_coordinator import CaptureCoordinator
from hardware_controller import HardwareController
from serial_service import SerialService, SerialServiceError


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


class CameraOnlyValidationHardware:
    """Manual-test controller that marks hardware safety as intentionally bypassed."""

    hardware_safety_bypassed_for_manual_camera_validation = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []
        self.safe_stop_count = 0
        self.rgb_mask = 0x00
        self.tungsten_mask = 0x00
        self.fan_on_state = False
        self.door_closed = False

    def _record(self, name: str, value: object | None = None) -> None:
        self.calls.append((name, value))

    def ping(self) -> bool:
        self._record("manual_bypass_ping")
        return True

    def get_error_status(self) -> int:
        self._record("manual_bypass_get_error_status")
        return 0x00

    def door_close(self) -> None:
        self._record("manual_bypass_door_close")
        self.door_closed = True

    def fan_on(self) -> None:
        self._record("manual_bypass_fan_on")
        self.fan_on_state = True

    def rgb_led_set(self, mask: int) -> None:
        self._record("manual_bypass_rgb_led_set", mask)
        self.rgb_mask = int(mask)

    def tungsten_set(self, mask: int) -> None:
        self._record("manual_bypass_tungsten_set", mask)
        self.tungsten_mask = int(mask)

    def ensure_rgb_capture_ready(self) -> None:
        self._record("manual_bypass_ensure_rgb_capture_ready")
        if not self.door_closed or not self.fan_on_state or self.rgb_mask == 0x00 or self.tungsten_mask != 0x00:
            raise RuntimeError("manual RGB camera-only interlock state was not prepared")

    def ensure_multispectral_capture_ready(self) -> None:
        self._record("manual_bypass_ensure_multispectral_capture_ready")
        if not self.door_closed or not self.fan_on_state or self.tungsten_mask == 0x00 or self.rgb_mask != 0x00:
            raise RuntimeError("manual multispectral camera-only interlock state was not prepared")

    def safe_stop(self) -> None:
        self._record("manual_bypass_safe_stop")
        self.safe_stop_count += 1
        self.rgb_mask = 0x00
        self.tungsten_mask = 0x00


def run_rgb_capture_once_validation(
    *,
    config: RgbCameraConfig,
    output_root: Path,
    sample_name: str,
    preview_running: bool,
    collision_check: bool,
    failure_device_index: int | None,
    camera_only_validation: bool,
) -> int:
    if not camera_only_validation:
        print("Hardware-backed manual validation is not wired in this CLI yet.")
        print("Use --camera-only-validation to verify RGB production capture/save without claiming STM32 safety success.")
        return 2

    output_dir = _next_manual_sample_dir(output_root, sample_name)
    rgb = RgbUvcCamera(config=config)
    manager = CameraManager(rgb_camera=rgb)
    hardware = CameraOnlyValidationHardware()
    coordinator = CaptureCoordinator(camera_manager=manager, hardware_controller=hardware)
    preview_after = None
    try:
        probe = manager.probe_rgb()
        status = probe.get("status") or manager.status().get("rgb") or {}
        preview_meta = {"mode": "not_running", "previewFrameReadableAfterCapture": None}
        if preview_running:
            manager.start_rgb_preview()
            preview_meta["mode"] = "running_reuse_existing_handle"
        result = coordinator.run_rgb_capture(
            sample_id=sample_name,
            output_dir=output_dir,
            rgb_dir_name="rgb",
            view_index=0,
        )
        result["metadata"]["hardwareSafetyBypassedForManualCameraValidation"] = True
        result["metadata"]["manualValidationHardwareCalls"] = hardware.calls
        _write_manual_metadata(output_dir, result["metadata"])
        if preview_running:
            preview_data, preview_after = manager.rgb_preview_jpeg()
            preview_meta["previewFrameReadableAfterCapture"] = bool(preview_data.startswith(b"\xff\xd8"))
        frame_meta = _first_frame_metadata(result)
        if result.get("state") != "completed" or not frame_meta:
            _print_capture_result(status, result, output_dir, preview_meta, None)
            return 2
        saved_path = Path(frame_meta["path"])
        png_check = _inspect_saved_png(saved_path)
        _print_capture_result(status, result, output_dir, preview_meta, png_check)
        if collision_check:
            collision = coordinator.run_rgb_capture(
                sample_id=sample_name,
                output_dir=output_dir,
                rgb_dir_name="rgb",
                view_index=0,
            )
            print()
            print("Path collision check:")
            print(f"  state: {collision.get('state')}")
            print(f"  code: {(collision.get('error') or {}).get('code')}")
            print(f"  originalFileSizeBytes: {saved_path.stat().st_size if saved_path.exists() else '--'}")
        if failure_device_index is not None:
            _run_rgb_failure_validation(config, failure_device_index, output_root)
        return 0 if png_check["readable"] else 2
    except CameraError as exc:
        print(f"Camera error: {exc.user_message}")
        print(f"Technical detail: {exc.technical_message}")
        return 2
    finally:
        if preview_after is not None:
            try:
                manager.stop_rgb_preview()
            except Exception as exc:
                print(f"preview stop warning: {exc}")
        elif preview_running:
            try:
                manager.stop_rgb_preview()
            except Exception as exc:
                print(f"preview stop warning: {exc}")


def _run_rgb_failure_validation(config: RgbCameraConfig, failure_device_index: int, output_root: Path) -> None:
    bad_config = RgbCameraConfig.from_dict({**config.to_dict(), "deviceIndex": failure_device_index})
    output_dir = _next_manual_sample_dir(output_root, f"failure_device_{failure_device_index}")
    manager = CameraManager(rgb_camera=RgbUvcCamera(config=bad_config))
    coordinator = CaptureCoordinator(
        camera_manager=manager,
        hardware_controller=CameraOnlyValidationHardware(),
    )
    result = coordinator.run_rgb_capture(
        sample_id=f"failure_device_{failure_device_index}",
        output_dir=output_dir,
        rgb_dir_name="rgb",
        view_index=0,
    )
    png_path = output_dir / "rgb" / "rgb_view_000.png"
    print()
    print("Failure scenario:")
    print(f"  deviceIndex: {failure_device_index}")
    print(f"  state: {result.get('state')}")
    print(f"  code: {(result.get('error') or {}).get('code')}")
    print(f"  message: {(result.get('error') or {}).get('message')}")
    print(f"  pngExists: {png_path.exists()}")
    print(f"  successMetadataFrames: {len((result.get('metadata') or {}).get('frames') or [])}")


def run_multispectral_capture_once_validation(
    *,
    sdk_dir: str | None,
    serial: str | None,
    output_root: Path,
    sample_name: str,
    preview_running: bool,
    collision_check: bool,
    exposure: float | None,
    gain: float | None,
    camera_only_validation: bool,
) -> int:
    if not camera_only_validation:
        print("Hardware-backed manual validation is not wired in this CLI yet.")
        print("Use --camera-only-validation to verify DVP2 production single-frame capture/save without claiming STM32 safety success.")
        return 2

    output_dir = _next_manual_sample_dir(output_root, sample_name, stage="p1b4_dvp2_capture")
    multispectral = Dvp2MonoCamera(sdk_dir=sdk_dir, serial_number=serial)
    manager = CameraManager(multispectral_camera=multispectral)
    hardware = CameraOnlyValidationHardware()
    preview_started = False
    try:
        probe = manager.probe_multispectral()
        status = probe.get("status") or manager.status().get("multispectral") or {}
        if not probe.get("passed"):
            print("DVP2 probe failed before production single-frame capture.")
            print(f"error: {status.get('error')}")
            print(f"technicalError: {status.get('technicalError')}")
            return 2
        if exposure is not None or gain is not None:
            settings = {}
            if exposure is not None:
                settings["exposure"] = exposure
            if gain is not None:
                settings["gain"] = gain
            manager.apply_multispectral_settings(settings)
        preview_meta = {"mode": "not_running", "previewFrameReadableAfterCapture": None}
        if preview_running:
            manager.start_multispectral_preview()
            preview_started = True
            preview_meta["mode"] = "running_reuse_existing_stream"
        coordinator = CaptureCoordinator(camera_manager=manager, hardware_controller=hardware)
        result = coordinator.run_multispectral_capture(
            sample_id=sample_name,
            output_dir=output_dir,
            multispectral_dir_name="multispectral",
            frame_index=0,
        )
        result["metadata"]["hardwareSafetyBypassedForManualCameraValidation"] = True
        result["metadata"]["manualValidationHardwareCalls"] = hardware.calls
        _write_manual_metadata(output_dir, result["metadata"])
        if preview_started:
            preview_data, _ = manager.multispectral_preview_jpeg()
            preview_meta["previewFrameReadableAfterCapture"] = bool(preview_data.startswith(b"\xff\xd8"))
        frame_meta = _first_frame_metadata(result)
        if result.get("state") != "completed" or not frame_meta:
            _print_multispectral_capture_result(status, result, output_dir, preview_meta, None)
            return 2
        saved_path = Path(frame_meta["path"])
        png_check = _inspect_saved_png(saved_path)
        _print_multispectral_capture_result(status, result, output_dir, preview_meta, png_check)
        if collision_check:
            collision = coordinator.run_multispectral_capture(
                sample_id=sample_name,
                output_dir=output_dir,
                multispectral_dir_name="multispectral",
                frame_index=0,
            )
            print()
            print("Path collision check:")
            print(f"  state: {collision.get('state')}")
            print(f"  code: {(collision.get('error') or {}).get('code')}")
            print(f"  originalFileSizeBytes: {saved_path.stat().st_size if saved_path.exists() else '--'}")
        return 0 if png_check["readable"] and png_check["dtype"] == frame_meta.get("dtype") else 2
    except CameraError as exc:
        print(f"Camera error: {exc.user_message}")
        print(f"Technical detail: {exc.technical_message}")
        return 2
    finally:
        if preview_started:
            try:
                manager.stop_multispectral_preview()
            except Exception as exc:
                print(f"preview stop warning: {exc}")
        else:
            try:
                multispectral.stop_stream()
                multispectral.close()
            except Exception as exc:
                print(f"multispectral close warning: {exc}")


def run_multispectral_sequence_validation(
    *,
    sdk_dir: str | None,
    serial: str | None,
    stm32_port: str | None,
    output_root: Path,
    sample_name: str,
    exposure: float | None,
    gain: float | None,
    filter_config: str | None,
    settling_ms: int,
    confirm_wheel_motion: bool,
) -> int:
    if not confirm_wheel_motion:
        print("Refusing to run multispectral sequence: this test will move the filter wheel.")
        print("Re-run with --confirm-wheel-motion and a real --stm32-port after checking the hardware is safe.")
        return 2
    if not stm32_port:
        print("Refusing to run multispectral sequence: --stm32-port is required for real filter-wheel motion.")
        return 2

    output_dir = _next_manual_sample_dir(output_root, sample_name, stage="p1b5_dvp2_sequence")
    multispectral = Dvp2MonoCamera(sdk_dir=sdk_dir, serial_number=serial)
    manager = CameraManager(multispectral_camera=multispectral)
    serial_service = SerialService()
    try:
        print("P1B-5 multispectral sequence validation")
        print("WARNING: this will HOME and move the real filter wheel through HardwareController.")
        serial_service.connect(stm32_port)
        if not serial_service.ping():
            print("STM32 ping failed; refusing to move filter wheel.")
            return 2
        hardware = HardwareController(serial_service)
        probe = manager.probe_multispectral()
        status = probe.get("status") or manager.status().get("multispectral") or {}
        if not probe.get("passed"):
            print("DVP2 probe failed before multispectral sequence.")
            print(f"error: {status.get('error')}")
            print(f"technicalError: {status.get('technicalError')}")
            return 2
        if exposure is not None or gain is not None:
            settings = {}
            if exposure is not None:
                settings["exposure"] = exposure
            if gain is not None:
                settings["gain"] = gain
            manager.apply_multispectral_settings(settings)
        coordinator = CaptureCoordinator(camera_manager=manager, hardware_controller=hardware)
        result = coordinator.run_multispectral_sequence(
            sample_id=sample_name,
            output_dir=output_dir,
            multispectral_dir_name="multispectral",
            filter_config_path=filter_config,
            settling_ms=settling_ms,
        )
        _write_manual_metadata(output_dir, result["metadata"])
        _print_multispectral_sequence_result(result, output_dir)
        return 0 if result.get("state") == "completed" and result.get("metadata", {}).get("multispectralSequenceComplete") else 2
    except (CameraError, SerialServiceError) as exc:
        print(f"Hardware validation error: {exc}")
        return 2
    finally:
        try:
            multispectral.stop_stream()
            multispectral.close()
        except Exception as exc:
            print(f"multispectral close warning: {exc}")
        try:
            serial_service.disconnect()
        except Exception as exc:
            print(f"serial disconnect warning: {exc}")


def run_multispectral_focus_validation(
    *,
    sdk_dir: str | None,
    serial: str | None,
    exposure: float | None,
    gain: float | None,
    roi_mode: str,
    watch: bool,
    frames: int,
    interval_ms: int,
    preview_running: bool,
) -> int:
    multispectral = Dvp2MonoCamera(sdk_dir=sdk_dir, serial_number=serial)
    manager = CameraManager(multispectral_camera=multispectral)
    preview_started = False
    try:
        print("P1B-5.5 DVP2 focus quality validation")
        print("This only reads raw DVP2 mono frames; it does not move any focus mechanism.")
        probe = manager.probe_multispectral()
        status = probe.get("status") or manager.status().get("multispectral") or {}
        if not probe.get("passed"):
            print("DVP2 probe failed before focus evaluation.")
            print(f"error: {status.get('error')}")
            print(f"technicalError: {status.get('technicalError')}")
            return 2
        if exposure is not None or gain is not None:
            settings = {}
            if exposure is not None:
                settings["exposure"] = exposure
            if gain is not None:
                settings["gain"] = gain
            manager.apply_multispectral_settings(settings)
        if preview_running:
            manager.start_multispectral_preview()
            preview_started = True
            print("previewReuse: enabled")
        count = 0
        max_score = 0.0
        limit = frames if frames > 0 else None
        while limit is None or count < limit:
            count += 1
            result = manager.evaluate_multispectral_focus({"roiMode": roi_mode})
            score = result.get("focusScore")
            metrics = result.get("metrics") or {}
            if isinstance(score, (int, float)):
                max_score = max(max_score, float(score))
            print(
                f"focus[{count}] "
                f"score={_fmt_float(score)} "
                f"tenengrad={_fmt_float(metrics.get('tenengrad'))} "
                f"laplacian={_fmt_float(metrics.get('laplacianVariance'))} "
                f"edgeDensity={_fmt_float(metrics.get('edgeDensity'))} "
                f"classification={result.get('classification')} "
                f"roi={(result.get('roi') or {}).get('mode')} "
                f"dtype={(result.get('frame') or {}).get('dtype')} "
                f"pixelFormat={(result.get('frame') or {}).get('pixelFormat')} "
                f"previewWasRunning={(result.get('capture') or {}).get('previewWasRunning')}"
            )
            if not watch:
                break
            time.sleep(max(50, interval_ms) / 1000.0)
        print(f"maxObservedFocusScore: {_fmt_float(max_score)}")
        return 0
    except KeyboardInterrupt:
        print()
        print("focus watch stopped by operator")
        return 0
    except CameraError as exc:
        print(f"Camera error: {exc.user_message}")
        print(f"Technical detail: {exc.technical_message}")
        return 2
    finally:
        if preview_started:
            try:
                manager.stop_multispectral_preview()
            except Exception as exc:
                print(f"preview stop warning: {exc}")
        else:
            try:
                multispectral.stop_stream()
                multispectral.close()
            except Exception as exc:
                print(f"multispectral close warning: {exc}")


def _next_manual_sample_dir(output_root: Path, sample_name: str, *, stage: str = "p1b3_rgb_capture") -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    root = output_root / stage
    root.mkdir(parents=True, exist_ok=True)
    base = root / f"{timestamp}_{sample_name}"
    if not base.exists():
        return base
    suffix = 1
    while True:
        candidate = root / f"{timestamp}_{sample_name}_{suffix:02d}"
        if not candidate.exists():
            return candidate
        suffix += 1


def _first_frame_metadata(result: dict) -> dict | None:
    frames = (result.get("metadata") or {}).get("frames") or []
    return frames[0] if frames else None


def _inspect_saved_png(path: Path) -> dict:
    from PIL import Image
    import numpy as np

    with Image.open(path) as image:
        array = np.asarray(image)
        return {
            "readable": True,
            "width": int(image.width),
            "height": int(image.height),
            "mode": image.mode,
            "shape": tuple(int(value) for value in array.shape),
            "dtype": str(array.dtype),
            "channels": int(array.shape[2]) if array.ndim == 3 else 1,
            "fileSizeBytes": path.stat().st_size,
        }


def _write_manual_metadata(output_dir: Path, metadata: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "capture_metadata_skeleton.json"
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _print_capture_result(
    status: dict,
    result: dict,
    output_dir: Path,
    preview_meta: dict,
    png_check: dict | None,
) -> None:
    frame_meta = _first_frame_metadata(result) or {}
    requested = frame_meta.get("requestedSettings") or status.get("requested") or {}
    actual = frame_meta.get("actualSettings") or status.get("actual") or {}
    device = frame_meta.get("device") or {}
    print("RGB device:")
    print(f"  deviceIndex: {device.get('deviceIndex', status.get('deviceIndex', '--'))}")
    print(f"  transport: {device.get('transport', status.get('transport', '--'))}")
    print(f"Capture status: {result.get('state')}")
    print(f"Requested resolution: {requested.get('width', '--')}x{requested.get('height', '--')}")
    print(f"Actual resolution: {actual.get('width', '--')}x{actual.get('height', '--')}")
    print(f"Requested FPS: {requested.get('fps', '--')}")
    print(f"Actual FPS: {actual.get('fps', '--')}")
    print(f"Codec: requested={requested.get('fourcc', '--')} actual={actual.get('fourcc', '--')}")
    print(f"Frame shape: {frame_meta.get('height', '--')}x{frame_meta.get('width', '--')}x{frame_meta.get('channels', '--')}")
    print(f"dtype: {frame_meta.get('dtype', '--')}")
    print(f"channels: {frame_meta.get('channels', '--')}")
    print(f"pixel order: {frame_meta.get('pixelOrder', '--')}")
    print(f"source pixel order: {frame_meta.get('sourcePixelOrder', '--')}")
    print(f"Capture timestamp: {frame_meta.get('timestamp', '--')}")
    print(f"Saved path: {frame_meta.get('path', '--')}")
    print(f"File size: {(png_check or {}).get('fileSizeBytes', '--')}")
    print(f"Preview ownership mode: {preview_meta.get('mode')}")
    print(f"Preview frame readable after capture: {preview_meta.get('previewFrameReadableAfterCapture')}")
    print(f"Output directory: {output_dir}")
    print("PNG check:")
    _print_section("  png", png_check or {})
    print("Metadata:")
    _print_section("  frame", frame_meta)
    print(f"hardwareSafetyBypassedForManualCameraValidation: {bool((result.get('metadata') or {}).get('hardwareSafetyBypassedForManualCameraValidation'))}")


def _print_multispectral_capture_result(
    status: dict,
    result: dict,
    output_dir: Path,
    preview_meta: dict,
    png_check: dict | None,
) -> None:
    frame_meta = _first_frame_metadata(result) or {}
    requested = frame_meta.get("requestedSettings") or status.get("requested") or {}
    actual = frame_meta.get("actualSettings") or status.get("actual") or {}
    device = frame_meta.get("device") or {}
    print("DVP2 multispectral device:")
    print(f"  model: {device.get('model', actual.get('model', '--'))}")
    print(f"  serial: {device.get('serial', actual.get('cameraSerial', '--'))}")
    print(f"  userId: {device.get('userId', actual.get('userId', '--'))}")
    print(f"  ip: {device.get('ip', actual.get('cameraIp', '--'))}")
    print(f"  mac: {device.get('mac', actual.get('cameraMac', '--'))}")
    print(f"  transport: {device.get('transport', status.get('transport', '--'))}")
    print(f"Capture status: {result.get('state')}")
    print(f"Requested serial: {requested.get('serialNumber', '--')}")
    print(f"Frame shape: {frame_meta.get('height', '--')}x{frame_meta.get('width', '--')}x{frame_meta.get('channels', '--')}")
    print(f"dtype: {frame_meta.get('dtype', '--')}")
    print(f"pixel format: {frame_meta.get('pixelFormat', '--')}")
    print(f"exposure us: {frame_meta.get('exposureUs', '--')}")
    print(f"gain: {frame_meta.get('gain', '--')}")
    print(f"wavelengthNm: {frame_meta.get('wavelengthNm')}")
    print(f"bandAssignment: {frame_meta.get('bandAssignment', '--')}")
    print(f"filterWheelSynchronized: {frame_meta.get('filterWheelSynchronized')}")
    print(f"Saved path: {frame_meta.get('path', '--')}")
    print(f"File size: {(png_check or {}).get('fileSizeBytes', '--')}")
    print(f"Preview ownership mode: {preview_meta.get('mode')}")
    print(f"Preview frame readable after capture: {preview_meta.get('previewFrameReadableAfterCapture')}")
    print(f"Output directory: {output_dir}")
    print("PNG check:")
    _print_section("  png", png_check or {})
    print("Metadata:")
    _print_section("  frame", frame_meta)
    print(f"hardwareSafetyBypassedForManualCameraValidation: {bool((result.get('metadata') or {}).get('hardwareSafetyBypassedForManualCameraValidation'))}")


def _print_multispectral_sequence_result(result: dict, output_dir: Path) -> None:
    metadata = result.get("metadata") or {}
    sequence = metadata.get("multispectralSequence") or {}
    print("DVP2 multispectral sequence:")
    print(f"  state: {result.get('state')}")
    print(f"  sequenceComplete: {metadata.get('multispectralSequenceComplete')}")
    print(f"  filterConfigSource: {sequence.get('filterConfigSource', '--')}")
    print(f"  developmentConfig: {sequence.get('developmentConfig')}")
    print(f"  completedBands: {sequence.get('completedBands')}")
    print(f"  failedBand: {sequence.get('failedBand')}")
    print(f"  pendingBands: {sequence.get('pendingBands')}")
    print(f"  partialCapture: {sequence.get('partialCapture')}")
    print(f"  outputDirectory: {output_dir}")
    print("Frames:")
    for frame in metadata.get("frames") or []:
        print(
            "  "
            f"{frame.get('bandId', '--')} "
            f"wl={frame.get('wavelengthNm')} "
            f"pos={(frame.get('filterWheel') or {}).get('position')} "
            f"dtype={frame.get('dtype')} "
            f"path={frame.get('relativePath')}"
        )
    if result.get("error"):
        _print_section("  error", result.get("error") or {})


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


def _fmt_float(value) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.3f}"
    return "--"


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual camera verification; not used by unittest.")
    parser.add_argument("--rgb", action="store_true", help="Test RGB UVC camera through OpenCV DirectShow.")
    parser.add_argument("--rgb-capture-once", action="store_true", help="Run protected CaptureCoordinator -> CameraManager -> RgbUvcCamera single-frame PNG validation.")
    parser.add_argument("--multispectral", action="store_true", help="Test DO3THINK DVP2 GigE monochrome camera.")
    parser.add_argument("--multispectral-capture-once", action="store_true", help="Run protected CaptureCoordinator -> CameraManager -> DVP2 single-frame raw PNG validation.")
    parser.add_argument("--multispectral-sequence", action="store_true", help="Run protected filter-wheel + DVP2 multi-band raw PNG validation with real STM32 motion.")
    parser.add_argument("--focus-evaluate", action="store_true", help="Evaluate DVP2 raw mono focus quality once.")
    parser.add_argument("--focus-watch", action="store_true", help="Continuously print DVP2 focus metrics while the operator manually adjusts the lens; use --frames 0 until Ctrl+C.")
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
    parser.add_argument("--stm32-port", default=None, help="STM32 serial port for real filter-wheel motion, for example COM5.")
    parser.add_argument("--filter-config", default=None, help="Optional filter config JSON path for multispectral sequence validation.")
    parser.add_argument("--settling-ms", type=int, default=250, help="Filter-wheel settling time in milliseconds for sequence validation.")
    parser.add_argument("--focus-roi", choices=["center", "full"], default="center", help="ROI for DVP2 focus quality evaluation.")
    parser.add_argument("--focus-interval-ms", type=int, default=500, help="Focus watch polling interval in milliseconds.")
    parser.add_argument("--confirm-wheel-motion", action="store_true", help="Explicitly allow manual validation to HOME and move the real filter wheel.")
    parser.add_argument("--frames", type=int, default=1, help="Number of frames to capture for stability test.")
    parser.add_argument("--save", action="store_true", help="Save one frame to a temporary test directory.")
    parser.add_argument("--output-root", default="manual_test_output", help="Manual validation output root.")
    parser.add_argument("--sample-name", default="sample_test", help="Manual validation sample name.")
    parser.add_argument("--preview-running", action="store_true", help="Start camera preview before protected capture to validate handle/stream reuse.")
    parser.add_argument("--collision-check", action="store_true", help="Attempt the same capture path again to verify overwrite protection.")
    parser.add_argument("--failure-device-index", type=int, default=None, help="Optional wrong RGB device index for failure-path validation.")
    parser.add_argument("--camera-only-validation", action="store_true", help="Bypass real STM32/light hardware only for manual camera capture/save validation.")
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
    if args.rgb_capture_once:
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
        return run_rgb_capture_once_validation(
            config=config,
            output_root=Path(args.output_root),
            sample_name=args.sample_name,
            preview_running=args.preview_running,
            collision_check=args.collision_check,
            failure_device_index=args.failure_device_index,
            camera_only_validation=args.camera_only_validation,
        )
    if args.multispectral:
        return run_multispectral_test(
            sdk_dir=args.sdk_dir,
            serial=args.serial,
            exposure=args.exposure,
            gain=args.gain,
            save=args.save,
            frames=max(1, args.frames),
        )
    if args.multispectral_capture_once:
        return run_multispectral_capture_once_validation(
            sdk_dir=args.sdk_dir,
            serial=args.serial,
            output_root=Path(args.output_root),
            sample_name=args.sample_name,
            preview_running=args.preview_running,
            collision_check=args.collision_check,
            exposure=args.exposure,
            gain=args.gain,
            camera_only_validation=args.camera_only_validation,
        )
    if args.multispectral_sequence:
        return run_multispectral_sequence_validation(
            sdk_dir=args.sdk_dir,
            serial=args.serial,
            stm32_port=args.stm32_port,
            output_root=Path(args.output_root),
            sample_name=args.sample_name,
            exposure=args.exposure,
            gain=args.gain,
            filter_config=args.filter_config,
            settling_ms=args.settling_ms,
            confirm_wheel_motion=args.confirm_wheel_motion,
        )
    if args.focus_evaluate or args.focus_watch:
        return run_multispectral_focus_validation(
            sdk_dir=args.sdk_dir,
            serial=args.serial,
            exposure=args.exposure,
            gain=args.gain,
            roi_mode=args.focus_roi,
            watch=args.focus_watch,
            frames=args.frames,
            interval_ms=args.focus_interval_ms,
            preview_running=args.preview_running,
        )
    parser.error("Choose a manual test, for example: --rgb, --rgb-capture-once, --multispectral, --multispectral-capture-once, --multispectral-sequence, --focus-evaluate or --focus-watch")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
