from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from camera_service import CameraFrame, CameraManager, CameraSettingsStore, RgbCameraConfig, RgbUvcCamera
from tests.test_camera_service import FakeCapture, FakeCv2


class RestoreRgbAdapter:
    role = "rgb"
    transport = "UVC/DirectShow"

    def __init__(self, stable_id: str = "RGB-A") -> None:
        self.config = RgbCameraConfig()
        self.stable_id = stable_id
        self.is_open = False
        self.apply_calls: list[dict] = []

    def probe_available(self) -> bool:
        self.is_open = True
        return True

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def stop_stream(self) -> None:
        pass

    def get_status(self) -> dict:
        requested = self.config.to_dict()
        return {
            "role": "rgb",
            "detected": True,
            "available": True,
            "connected": True,
            "opened": self.is_open,
            "streaming": False,
            "stableId": self.stable_id,
            "deviceIndex": requested["deviceIndex"],
            "deviceName": "Fake RGB",
            "requested": requested,
            "actual": {
                "width": requested["width"],
                "height": requested["height"],
                "fps": requested["fps"],
                "fourcc": requested["fourcc"],
                "exposure": requested["exposure"],
                "gain": requested["gain"],
                "whiteBalance": requested["whiteBalance"],
                "matchesRequested": True,
            },
            "capabilities": {},
        }

    def apply_config(self, config, *, restart: bool = False) -> dict:
        self.config = config if isinstance(config, RgbCameraConfig) else RgbCameraConfig.from_dict(config)
        self.is_open = True
        self.apply_calls.append({"config": self.config.to_dict(), "restart": restart})
        return {
            "status": self.get_status(),
            "settingResults": {
                "width": {"requested": self.config.width, "actual": self.config.width, "accepted": True},
                "height": {"requested": self.config.height, "actual": self.config.height, "accepted": True},
                "fps": {"requested": self.config.fps, "actual": self.config.fps, "accepted": True},
                "fourcc": {"requested": self.config.fourcc, "actual": self.config.fourcc, "accepted": True},
                "exposure": {"requested": self.config.exposure, "actual": self.config.exposure, "accepted": True},
            },
        }


class RestoreDvp2Adapter:
    role = "multispectral"
    transport = "GigE/DVP2"

    def __init__(self, stable_id: str = "DSGP23400004963", serial: str = "GP23400004963") -> None:
        self.stable_id = stable_id
        self.serial = serial
        self.is_open = False
        self.exposure = 10000.0
        self.gain = 1.0
        self.set_calls: list[tuple[str, float]] = []

    def probe_available(self) -> bool:
        self.is_open = True
        return True

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def stop_stream(self) -> None:
        pass

    def get_status(self) -> dict:
        return {
            "role": "multispectral",
            "detected": True,
            "available": True,
            "connected": True,
            "opened": self.is_open,
            "streaming": False,
            "stableId": self.stable_id,
            "requested": {"serialNumber": self.serial},
            "actual": {
                "cameraSerial": self.stable_id,
                "userId": self.serial,
                "exposure": self.exposure,
                "gain": self.gain,
                "pixelFormat": "Mono8",
                "frameDtype": "uint8",
            },
            "capabilities": {},
            "pixelFormat": "Mono8",
            "frameDtype": "uint8",
            "sdkAvailable": True,
        }

    def set_exposure(self, value: float) -> float:
        self.exposure = float(value)
        self.set_calls.append(("exposure", self.exposure))
        return self.exposure

    def set_gain(self, value: float) -> float:
        self.gain = float(value)
        self.set_calls.append(("gain", self.gain))
        return self.gain

    def capture_frame(self) -> CameraFrame:
        self.open()
        import numpy as np

        data = np.zeros((2, 2), dtype=np.uint8)
        return CameraFrame(data=data, color_space="MONO", dtype="uint8", shape=data.shape)


class CameraSettingsPersistenceTests(unittest.TestCase):
    def store(self, tmp: str) -> CameraSettingsStore:
        return CameraSettingsStore(Path(tmp) / "camera_settings.json")

    def test_store_save_load_missing_corrupt_and_reset(self):
        with tempfile.TemporaryDirectory(prefix="camera_settings_store_") as tmp:
            store = self.store(tmp)
            self.assertFalse(store.snapshot()["exists"])
            saved = store.update_rgb({"deviceIndex": 2, "width": 1920, "height": 1080, "fps": 15, "fourcc": "MJPG"})
            store.update_multispectral({"deviceStableId": "DSGP23400004963", "exposure": 12000, "gain": 1.5})

            loaded = CameraSettingsStore(store.path).snapshot()
            self.assertTrue(loaded["exists"])
            self.assertEqual(saved["rgb"]["width"], 1920)
            self.assertEqual(loaded["rgb"]["deviceIndex"], 2)
            self.assertEqual(loaded["multispectral"]["exposure"], 12000.0)

            store.path.write_text("{not json", encoding="utf-8")
            corrupt = CameraSettingsStore(store.path).snapshot()
            self.assertEqual(corrupt["rgb"]["deviceIndex"], 1)
            self.assertEqual(corrupt["warnings"][0]["code"], "CAMERA_SETTINGS_CORRUPT")

            reset_store = self.store(tmp)
            reset_store.update_rgb({"deviceIndex": 3})
            reset = reset_store.reset("rgb")
            self.assertEqual(reset["rgb"]["deviceIndex"], 1)
            self.assertFalse(reset_store.snapshot()["hasCustom"]["rgb"])

    def test_rgb_apply_and_persist_records_requested_actual_and_unsupported_results(self):
        with tempfile.TemporaryDirectory(prefix="camera_settings_rgb_") as tmp:
            store = self.store(tmp)
            rgb = RgbUvcCamera(cv2_module=FakeCv2, capture_factory=lambda index: FakeCapture(set_ok=False))
            manager = CameraManager(rgb_camera=rgb, multispectral_camera=RestoreDvp2Adapter(), settings_store=store)

            result = manager.apply_rgb_settings({
                "deviceIndex": 1,
                "width": 1920,
                "height": 1080,
                "fps": 25,
                "fourcc": "MJPG",
                "autoExposureEnabled": False,
                "exposure": -6,
                "persist": True,
            })

            snapshot = CameraSettingsStore(store.path).snapshot()
            self.assertTrue(result["persisted"])
            self.assertEqual(snapshot["rgb"]["width"], 1920)
            self.assertEqual(snapshot["rgb"]["exposure"], -6.0)
            self.assertFalse(snapshot["rgb"]["settingResults"]["exposure"]["accepted"])
            self.assertIn("lastActual", snapshot["rgb"])

    def test_dvp2_apply_and_persist_reads_back_actual_values(self):
        with tempfile.TemporaryDirectory(prefix="camera_settings_dvp2_") as tmp:
            store = self.store(tmp)
            dvp2 = RestoreDvp2Adapter()
            manager = CameraManager(rgb_camera=RestoreRgbAdapter(), multispectral_camera=dvp2, settings_store=store)

            result = manager.apply_multispectral_settings({"exposure": 13000, "gain": 1.7, "persist": True})

            snapshot = CameraSettingsStore(store.path).snapshot()
            self.assertTrue(result["persisted"])
            self.assertEqual(result["settingResults"]["exposure"]["actual"], 13000.0)
            self.assertEqual(snapshot["multispectral"]["exposure"], 13000.0)
            self.assertEqual(snapshot["multispectral"]["gain"], 1.7)
            self.assertEqual(snapshot["multispectral"]["lastActual"]["gain"], 1.7)

    def test_startup_restore_rgb_and_reconnect_restore(self):
        with tempfile.TemporaryDirectory(prefix="camera_settings_restore_rgb_") as tmp:
            store = self.store(tmp)
            store.update_rgb({
                "deviceStableId": "RGB-A",
                "deviceIndex": 1,
                "width": 1280,
                "height": 720,
                "fps": 15,
                "fourcc": "MJPG",
                "autoExposureEnabled": False,
                "exposure": -5,
            })
            rgb = RestoreRgbAdapter(stable_id="RGB-A")
            manager = CameraManager(rgb_camera=rgb, multispectral_camera=RestoreDvp2Adapter(), settings_store=store)

            manager.probe_rgb()
            rgb.close()
            manager.probe_rgb()

            self.assertEqual(len(rgb.apply_calls), 2)
            self.assertEqual(rgb.apply_calls[-1]["config"]["width"], 1280)
            self.assertEqual(manager.status()["rgb"]["settingsRestoreState"]["state"], "restored")

    def test_startup_restore_dvp2_and_device_mismatch(self):
        with tempfile.TemporaryDirectory(prefix="camera_settings_restore_dvp2_") as tmp:
            store = self.store(tmp)
            store.update_multispectral({"deviceStableId": "DSGP23400004963", "serialNumber": "GP23400004963", "exposure": 14000, "gain": 2.0})
            dvp2 = RestoreDvp2Adapter(stable_id="DSGP23400004963", serial="GP23400004963")
            manager = CameraManager(rgb_camera=RestoreRgbAdapter(), multispectral_camera=dvp2, settings_store=store)

            manager.probe_multispectral()

            self.assertEqual(dvp2.set_calls, [("exposure", 14000.0), ("gain", 2.0)])
            self.assertEqual(manager.status()["multispectral"]["settingsRestoreState"]["state"], "restored")

            mismatch = RestoreDvp2Adapter(stable_id="DSGP00000000000", serial="GP00000000000")
            mismatch_manager = CameraManager(rgb_camera=RestoreRgbAdapter(), multispectral_camera=mismatch, settings_store=store)
            result = mismatch_manager.probe_multispectral()

            self.assertEqual(mismatch.set_calls, [])
            self.assertEqual(result["status"]["settingsRestoreState"]["state"], "device_mismatch")
            self.assertEqual(result["status"]["settingsRestoreState"]["restoreError"], "CAMERA_SETTINGS_DEVICE_MISMATCH")

    def test_legacy_migration_does_not_overwrite_existing_backend_config(self):
        with tempfile.TemporaryDirectory(prefix="camera_settings_migration_") as tmp:
            store = self.store(tmp)
            migrated = store.migrate_legacy_rgb({"deviceIndex": 2, "width": 1920, "height": 1080, "fps": 15})
            self.assertTrue(migrated["migrated"])
            self.assertEqual(CameraSettingsStore(store.path).snapshot()["rgb"]["width"], 1920)

            skipped = store.migrate_legacy_rgb({"deviceIndex": 4, "width": 640, "height": 480})
            self.assertFalse(skipped["migrated"])
            self.assertEqual(CameraSettingsStore(store.path).snapshot()["rgb"]["deviceIndex"], 2)

    def test_capture_metadata_records_settings_source_and_restore_state(self):
        with tempfile.TemporaryDirectory(prefix="camera_settings_metadata_") as tmp:
            store = self.store(tmp)
            store.update_multispectral({"deviceStableId": "DSGP23400004963", "exposure": 11000, "gain": 1.2})
            dvp2 = RestoreDvp2Adapter()
            manager = CameraManager(rgb_camera=RestoreRgbAdapter(), multispectral_camera=dvp2, settings_store=store)

            _, metadata = manager.capture_multispectral_frame()

            self.assertEqual(metadata["settingsSource"], "persistent")
            self.assertEqual(metadata["settingsRestoreState"]["state"], "restored")
            self.assertEqual(metadata["actualSettings"]["exposure"], 11000.0)


if __name__ == "__main__":
    unittest.main()
