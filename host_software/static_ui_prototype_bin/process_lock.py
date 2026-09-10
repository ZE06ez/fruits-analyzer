from __future__ import annotations

import ctypes
import os
import re
import threading
from pathlib import Path
from typing import Any


ERROR_ALREADY_EXISTS = 183
_LOCAL_MUTEX_COUNTS: dict[str, int] = {}
_LOCAL_MUTEX_LOCK = threading.Lock()


def runtime_state_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "FruitTasteAnalyzer"
    return Path.home() / "FruitTasteAnalyzer"


def sanitize_mutex_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value or "").strip())
    return text[:180] or "unknown"


class NamedMutex:
    """Small Windows named mutex wrapper with a no-op fallback for tests."""

    def __init__(self, name: str, *, enabled: bool = True, initial_owner: bool = True) -> None:
        self.name = name
        self.enabled = bool(enabled and os.name == "nt")
        self.initial_owner = bool(initial_owner)
        self.handle: Any | None = None
        self.acquired = False
        self._reentrant = False

    def acquire(self) -> bool:
        if not self.enabled:
            self.acquired = True
            return True
        with _LOCAL_MUTEX_LOCK:
            if _LOCAL_MUTEX_COUNTS.get(self.name, 0) > 0:
                _LOCAL_MUTEX_COUNTS[self.name] += 1
                self.acquired = True
                self._reentrant = True
                return True
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, self.initial_owner, self.name)
        if not handle:
            raise OSError(ctypes.get_last_error(), f"CreateMutexW failed for {self.name}")
        self.handle = handle
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            self.handle = None
            self.acquired = False
            return False
        self.acquired = True
        with _LOCAL_MUTEX_LOCK:
            _LOCAL_MUTEX_COUNTS[self.name] = _LOCAL_MUTEX_COUNTS.get(self.name, 0) + 1
        return True

    def release(self) -> None:
        if not self.enabled:
            self.acquired = False
            return
        if self._reentrant:
            with _LOCAL_MUTEX_LOCK:
                count = _LOCAL_MUTEX_COUNTS.get(self.name, 0)
                if count <= 1:
                    _LOCAL_MUTEX_COUNTS.pop(self.name, None)
                else:
                    _LOCAL_MUTEX_COUNTS[self.name] = count - 1
            self._reentrant = False
            self.acquired = False
            return
        if self.handle:
            kernel32 = ctypes.windll.kernel32
            if self.acquired and self.initial_owner:
                kernel32.ReleaseMutex(self.handle)
            kernel32.CloseHandle(self.handle)
        if self.acquired:
            with _LOCAL_MUTEX_LOCK:
                count = _LOCAL_MUTEX_COUNTS.get(self.name, 0)
                if count <= 1:
                    _LOCAL_MUTEX_COUNTS.pop(self.name, None)
                else:
                    _LOCAL_MUTEX_COUNTS[self.name] = count - 1
        self.handle = None
        self.acquired = False

    def __enter__(self) -> "NamedMutex":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def app_single_instance_mutex() -> NamedMutex:
    return NamedMutex(r"Local\FruitTasteAnalyzer.SingleInstance", initial_owner=False)


def camera_device_mutex(role: str, identity: str) -> NamedMutex:
    name = rf"Local\FruitTasteAnalyzer.Camera.{sanitize_mutex_name(role)}.{sanitize_mutex_name(identity)}"
    return NamedMutex(name)
