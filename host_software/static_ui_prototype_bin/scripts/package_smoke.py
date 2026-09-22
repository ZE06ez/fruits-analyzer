from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import APP_VERSION, RUNTIME_ENV


def get_json(url: str, *, method: str = "GET") -> dict:
    request = Request(url, method=method)
    with urlopen(request, timeout=3) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP status: {response.status}")
        return json.loads(response.read().decode("utf-8"))


def get_text(url: str) -> str:
    with urlopen(url, timeout=3) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP status: {response.status}")
        return response.read().decode("utf-8")


def tail(path: Path, lines: int = 80) -> str:
    if not path.is_file():
        return "<missing>"
    try:
        return "".join(path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[-lines:])
    except OSError as exc:
        return f"<read failed: {exc}>"


def tree_summary(root: Path, limit: int = 80) -> str:
    if not root.exists():
        return "<missing>"
    entries = []
    for path in sorted(root.rglob("*")):
        if len(entries) >= limit:
            entries.append("...")
            break
        relative = path.relative_to(root)
        entries.append(str(relative) + ("/" if path.is_dir() else ""))
    return "\n".join(entries) or "<empty>"


def failure_details(
    *,
    exe: Path,
    process: subprocess.Popen,
    runtime_root: Path,
    runtime_path: Path,
    startup_log: Path,
    app_log: Path,
) -> str:
    return (
        "packaged smoke failed\n"
        f"EXE path: {exe}\n"
        f"PID: {process.pid}\n"
        f"process exit code: {process.poll()}\n"
        f"runtime root: {runtime_root}\n"
        f"expected runtime.json path: {runtime_path}\n"
        f"startup.log path: {startup_log}\n"
        f"application log path: {app_log}\n"
        f"runtime.json:\n{tail(runtime_path)}\n"
        f"startup.log tail:\n{tail(startup_log)}"
        f"application log tail:\n{tail(app_log)}"
        f"directory tree summary:\n{tree_summary(runtime_root)}"
    )


def wait_for_runtime(
    process: subprocess.Popen,
    *,
    exe: Path,
    runtime_root: Path,
    runtime_path: Path,
    startup_log: Path,
    app_log: Path,
    timeout: float,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"process exited before runtime: {process.returncode}")
        if runtime_path.is_file():
            try:
                info = json.loads(runtime_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(0.5)
                continue
            # A one-file bootloader launches the Python application as a child.
            recorded = psutil.Process(int(info["pid"]))
            ancestors = {parent.pid for parent in recorded.parents()}
            if recorded.pid != process.pid and process.pid not in ancestors:
                raise RuntimeError("runtime PID does not belong to launched process tree")
            if Path(recorded.exe()).resolve() != exe:
                raise RuntimeError("runtime PID executable mismatch")
            if info.get("softwareVersion") != APP_VERSION:
                raise RuntimeError("runtime software version mismatch")
            port = int(info["port"])
            if not 1 <= port <= 65535 or info.get("url") != f"http://127.0.0.1:{port}/":
                raise RuntimeError("invalid runtime endpoint")
            return info
        if process.poll() is not None:
            raise RuntimeError(
                failure_details(
                    exe=exe,
                    process=process,
                    runtime_root=runtime_root,
                    runtime_path=runtime_path,
                    startup_log=startup_log,
                    app_log=app_log,
                )
            )
        time.sleep(0.2)
    raise RuntimeError(
        failure_details(
            exe=exe,
            process=process,
            runtime_root=runtime_root,
            runtime_path=runtime_path,
            startup_log=startup_log,
            app_log=app_log,
        )
    )


def validate_instance(
    *,
    exe: Path,
    process: subprocess.Popen,
    runtime_root: Path,
    timeout: float,
    phase,
) -> dict:
    deadline = time.monotonic() + timeout
    runtime_path = runtime_root / "runtime.json"
    startup_log = runtime_root / "startup.log"
    app_root = runtime_root / "app_data"
    app_log = app_root / "logs" / "fruit_taste_analyzer.log"
    info = wait_for_runtime(
        process,
        exe=exe,
        runtime_root=runtime_root,
        runtime_path=runtime_path,
        startup_log=startup_log,
        app_log=app_log,
        timeout=timeout,
    )
    base = str(info["url"]).rstrip("/")
    phase("WAIT_HEALTH")
    health = None
    health_error = None
    health_deadline = deadline
    while time.monotonic() < health_deadline:
        if process.poll() is not None or not psutil.pid_exists(int(info["pid"])):
            raise RuntimeError(f"process exited before health ready: {process.poll()}")
        try:
            health = get_json(base + "/api/health")
            break
        except Exception as exc:
            health_error = exc
            if process.poll() is not None:
                break
            time.sleep(0.5)
    if health is None:
        raise RuntimeError(
            f"backend health endpoint was not ready: {health_error}\n"
            + failure_details(
                exe=exe,
                process=process,
                runtime_root=runtime_root,
                runtime_path=runtime_path,
                startup_log=startup_log,
                app_log=app_log,
            )
        )
    if (
        health.get("status") != "ok"
        or health.get("softwareVersion") != info.get("softwareVersion")
        or not health.get("databases", {}).get("modelStudio")
        or not health["databases"].get("inspection")
    ):
        raise RuntimeError(f"unhealthy packaged backend: {health}")
    phase("VERIFY_STATIC", info)
    for path, marker in (("/", "app.js"), ("/app.js", ""), ("/styles.css", ""), ("/model-studio", "model_studio")):
        text = get_text(base + path)
        if marker and marker not in text:
            raise RuntimeError(f"static resource validation failed: {path}")
    phase("VERIFY_RUNTIME")
    required = ("config", "database", "logs", "outputs", "backups")
    missing = [name for name in required if not (app_root / name).is_dir()]
    if missing:
        raise RuntimeError(f"runtime directories missing: {missing}")
    databases = {path.name for path in (app_root / "database").glob("*.sqlite")}
    if not {"model_studio.sqlite", "inspection.sqlite"}.issubset(databases):
        raise RuntimeError(f"runtime databases missing: {sorted(databases)}")
    return {"runtime": info, "health": health, "runtimeRoot": str(app_root)}


def shutdown_instance(process: subprocess.Popen, runtime_root: Path, phase, timeout: float) -> None:
    info = json.loads((runtime_root / "runtime.json").read_text(encoding="utf-8"))
    phase("REQUEST_SHUTDOWN")
    if get_json(str(info["url"]).rstrip("/") + "/api/shutdown", method="POST").get("ok") is not True:
        raise RuntimeError("shutdown rejected")
    phase("WAIT_EXIT")
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"packaged process did not exit within {timeout:g}s after POST /api/shutdown (pid={process.pid})") from exc
    if process.poll() is None:
        raise RuntimeError("packaged process did not exit after POST /api/shutdown")
    if process.returncode != 0:
        raise RuntimeError(f"nonzero exit: {process.returncode}")
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", int(info["port"]))) == 0:
            raise RuntimeError("backend port still listening after exit")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a packaged FruitTasteAnalyzer EXE without hardware.")
    parser.add_argument("exe", type=Path)
    parser.add_argument("--startup-timeout", type=float, default=180.0)
    parser.add_argument("--shutdown-timeout", type=float, default=30.0)
    args = parser.parse_args()
    exe = args.exe.resolve()
    if not exe.is_file():
        raise SystemExit(f"EXE not found: {exe}")
    if args.startup_timeout <= 0 or args.shutdown_timeout <= 0:
        parser.error("timeouts must be positive")
    results = []
    for generation in range(1, 3):
        with tempfile.TemporaryDirectory(prefix="fta_package_smoke_", ignore_cleanup_errors=True) as local_app_data:
            env = {**os.environ, "LOCALAPPDATA": local_app_data, "APPDATA": local_app_data}
            env.pop(RUNTIME_ENV, None)
            runtime_root = Path(local_app_data) / "FruitTasteAnalyzer"
            assert not runtime_root.exists(), "runtime must be fresh"
            process = None
            ready_info = None
            current_phase = "WAIT_PROCESS_START"
            started = time.monotonic()

            def phase(name, info=None):
                nonlocal current_phase, ready_info
                current_phase = name
                if info is not None:
                    ready_info = info
                print(f"generation={generation} phase={name} elapsed={time.monotonic() - started:.1f}s", flush=True)

            try:
                phase("WAIT_PROCESS_START")
                process = subprocess.Popen([str(exe)], env=env, cwd=local_app_data)
                phase("WAIT_RUNTIME_JSON")
                result = validate_instance(
                    exe=exe,
                    process=process,
                    runtime_root=runtime_root,
                    timeout=args.startup_timeout,
                    phase=phase,
                )
                shutdown_instance(process, runtime_root, phase, args.shutdown_timeout)
                results.append({**result, "exitCode": process.returncode, "portReleased": True})
            except Exception as exc:
                print(f"phase={current_phase} elapsed={time.monotonic() - started:.1f}s error={exc}", file=sys.stderr)
                if process is not None:
                    print(failure_details(exe=exe, process=process, runtime_root=runtime_root,
                        runtime_path=runtime_root / "runtime.json", startup_log=runtime_root / "startup.log",
                        app_log=runtime_root / "app_data/logs/fruit_taste_analyzer.log"), file=sys.stderr)
                return 1
            finally:
                if process is not None and process.poll() is None:
                    if ready_info is not None:
                        try:
                            get_json(ready_info["url"].rstrip("/") + "/api/shutdown", method="POST")
                            process.wait(timeout=args.shutdown_timeout)
                        except Exception:
                            pass
                    if process.poll() is None:
                        try:
                            children = psutil.Process(process.pid).children(recursive=True)
                        except psutil.NoSuchProcess:
                            children = []
                        for child in reversed(children):
                            try:
                                child.terminate()
                            except psutil.NoSuchProcess:
                                pass
                        process.terminate()
                        process.wait(timeout=10)
                        psutil.wait_procs(children, timeout=10)
    print(json.dumps({"ok": True, "restart": True, "generations": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
