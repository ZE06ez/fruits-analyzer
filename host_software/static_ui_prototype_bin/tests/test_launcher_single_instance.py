from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import launcher
import process_lock


class FakeAppMutex:
    def __init__(self, acquired: bool) -> None:
        self.acquire_result = acquired
        self.released = False

    def acquire(self) -> bool:
        return self.acquire_result

    def release(self) -> None:
        self.released = True


class FakeCameraManager:
    def __init__(self) -> None:
        self.released = False

    def release_all(self) -> None:
        self.released = True


class FakeDeviceManager:
    def __init__(self) -> None:
        self.camera_manager = FakeCameraManager()


class FakeServer:
    should_exit = True

    def __init__(self) -> None:
        self.device_manager = FakeDeviceManager()


class LauncherSingleInstanceTests(unittest.TestCase):
    def test_first_instance_starts_backend_writes_runtime_and_releases_resources(self):
        mutex = FakeAppMutex(True)
        server = FakeServer()
        opened: list[str] = []

        def start_backend(static_dir, outputs_dir, app_dir):
            return server, 54321

        with tempfile.TemporaryDirectory(prefix="fta_launcher_") as tmp:
            with mock.patch.dict(sys.modules, {"backend_server": types.SimpleNamespace(start_backend=start_backend)}):
                with mock.patch.dict("os.environ", {"LOCALAPPDATA": tmp}, clear=False):
                    with mock.patch.object(process_lock, "app_single_instance_mutex", return_value=mutex):
                        with mock.patch.object(launcher, "prepare_runtime_site", return_value=Path(tmp) / "www"):
                            with mock.patch.object(launcher.webbrowser, "open", side_effect=lambda url, new=0: opened.append(url)):
                                launcher.main()

            runtime = json.loads((Path(tmp) / "FruitTasteAnalyzer" / "runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(runtime["port"], 54321)
            self.assertEqual(opened, ["http://127.0.0.1:54321/"])
            self.assertTrue(server.device_manager.camera_manager.released)
            self.assertTrue(mutex.released)

    def test_second_instance_opens_existing_ui_without_starting_backend(self):
        mutex = FakeAppMutex(False)
        opened: list[str] = []

        def start_backend(static_dir, outputs_dir, app_dir):
            raise AssertionError("second instance must not start backend")

        with tempfile.TemporaryDirectory(prefix="fta_launcher_") as tmp:
            runtime_dir = Path(tmp) / "FruitTasteAnalyzer"
            runtime_dir.mkdir(parents=True)
            (runtime_dir / "runtime.json").write_text(json.dumps({"pid": 123, "port": 45678}), encoding="utf-8")
            with mock.patch.dict(sys.modules, {"backend_server": types.SimpleNamespace(start_backend=start_backend)}):
                with mock.patch.dict("os.environ", {"LOCALAPPDATA": tmp}, clear=False):
                    with mock.patch.object(process_lock, "app_single_instance_mutex", return_value=mutex):
                        with mock.patch.object(launcher, "prepare_runtime_site", side_effect=AssertionError("second instance must not prepare site")):
                            with mock.patch.object(launcher.webbrowser, "open", side_effect=lambda url, new=0: opened.append(url)):
                                launcher.main()

            self.assertEqual(opened, ["http://127.0.0.1:45678/"])
            self.assertTrue(mutex.released)

    def test_backend_start_failure_releases_single_instance_mutex(self):
        mutex = FakeAppMutex(True)

        def start_backend(static_dir, outputs_dir, app_dir):
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory(prefix="fta_launcher_") as tmp:
            with mock.patch.dict(sys.modules, {"backend_server": types.SimpleNamespace(start_backend=start_backend)}):
                with mock.patch.dict("os.environ", {"LOCALAPPDATA": tmp}, clear=False):
                    with mock.patch.object(process_lock, "app_single_instance_mutex", return_value=mutex):
                        with mock.patch.object(launcher, "prepare_runtime_site", return_value=Path(tmp) / "www"):
                            with self.assertRaises(RuntimeError):
                                launcher.main()

            self.assertTrue(mutex.released)
