import os
import sys
import shutil
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path


def resource_path(name: str) -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / name
    return Path(__file__).resolve().parent / name


def runtime_site_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "FruitTasteAnalyzer" / "www"
    return Path.home() / "FruitTasteAnalyzer" / "www"


def startup_log_path() -> Path:
    return runtime_site_dir().parent / "startup.log"


def runtime_json_path() -> Path:
    return runtime_site_dir().parent / "runtime.json"


def log_startup(message: str) -> None:
    try:
        path = startup_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def write_runtime_info(port: int) -> None:
    try:
        import json

        path = runtime_json_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": os.getpid(), "port": int(port), "url": f"http://127.0.0.1:{port}/"}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log_startup(f"runtime info write failed: {exc}")


def read_existing_runtime_url() -> str:
    try:
        import json

        payload = json.loads(runtime_json_path().read_text(encoding="utf-8"))
        port = int(payload.get("port") or 0)
        if port > 0:
            return f"http://127.0.0.1:{port}/"
    except Exception:
        return ""
    return ""


def prepare_runtime_site() -> Path:
    if not hasattr(sys, "_MEIPASS"):
        return resource_path(".")

    target = runtime_site_dir()
    target.mkdir(parents=True, exist_ok=True)
    log_startup(f"copy frontend resources to {target}")
    for filename in ("index.html", "styles.css", "app.js"):
        shutil.copy2(resource_path(filename), target / filename)

    for dirname in ("assets", "sample_data", "model_studio"):
        source_dir = resource_path(dirname)
        target_dir = target / dirname
        if source_dir.exists():
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(source_dir, target_dir)

    return target


def main() -> None:
    server = None
    mutex = None
    try:
        log_startup("launcher start")
        from process_lock import app_single_instance_mutex

        mutex = app_single_instance_mutex()
        if not mutex.acquire():
            url = read_existing_runtime_url()
            log_startup(f"second instance detected; opening existing url={url or '<unknown>'}")
            if url:
                webbrowser.open(url, new=1)
            return
        site_dir = prepare_runtime_site()
        output_dir = runtime_site_dir().parent / "outputs"
        log_startup(f"site_dir={site_dir}")
        log_startup("import backend_server")
        from backend_server import start_backend

        log_startup("start backend")
        server, port = start_backend(site_dir, output_dir, site_dir)
        url = f"http://127.0.0.1:{port}/"
        write_runtime_info(port)
        log_startup(f"backend listening at {url}")
        webbrowser.open(url, new=1)
        log_startup("browser open requested")
        while not getattr(server, "should_exit", False):
            # Keep the packaged app alive while the browser UI talks to the local API.
            import time

            time.sleep(0.5)
    except KeyboardInterrupt:
        if server is not None:
            server.shutdown()
    except Exception:
        log_startup("fatal error")
        log_startup(traceback.format_exc())
        raise
    finally:
        if server is not None:
            try:
                if getattr(server, "device_manager", None) is not None:
                    server.device_manager.camera_manager.release_all()
            except Exception:
                pass
        if mutex is not None:
            mutex.release()
        log_startup("launcher exit")


if __name__ == "__main__":
    main()
