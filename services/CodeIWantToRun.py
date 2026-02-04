import time
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import json
from urllib import request
from urllib.error import URLError

import service_config
from service_config import SERVICE_NAME
from folder_monitor import FolderMonitor


# Creates a log every 10 seconds.
log_message = "Testing Service"

_LOGGER_NAME = "ServiceLog"

def _init_logger():
    log_file_path = Path(__file__).resolve().parent / "log.txt"

    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = RotatingFileHandler(
            log_file_path,
            maxBytes=1_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        formatter = logging.Formatter("%(asctime)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

logger = _init_logger()

def _post_ui_log(message: str, source: str = "ServiceLog"):
    host = getattr(service_config, "SERVICE_API_HOST", "127.0.0.1")
    port = int(getattr(service_config, "SERVICE_API_PORT", 8085))
    url = f"http://{host}:{port}/api/ui-log"
    try:
        data = json.dumps({"message": message, "source": source}).encode("utf-8")
        req = request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        with request.urlopen(req, timeout=0.5) as resp:
            # Best-effort: ignore body
            resp.read(0)
    except URLError:
        pass
    except Exception:
        pass

def main(stop_event=None):
    logger.info("Service CodeIWantToRun is starting...")
    _post_ui_log("Service CodeIWantToRun is starting...", source="ServiceLog")
    monitor = None
    try:
        monitor = FolderMonitor.from_config()
        folder_path = monitor.ensure_today_folder()
        logger.info("FolderMonitor started. Folder: %s", folder_path)
        _post_ui_log(f"FolderMonitor started. Folder: {folder_path}", source="ServiceLog")
    except Exception as exc:
        logger.warning("FolderMonitor failed to start: %s", exc)
        _post_ui_log(f"FolderMonitor failed to start: {exc}", source="ServiceLog")
    while True:
        # Run folder monitor every 10 seconds
        if monitor is not None:
            try:
                folder_path = monitor.ensure_today_folder()
                logger.info("FolderMonitor check OK: %s", folder_path)
                _post_ui_log(f"FolderMonitor check OK: {folder_path}", source="ServiceLog")
            except Exception as exc:
                logger.warning("FolderMonitor check failed: %s", exc)
                _post_ui_log(f"FolderMonitor check failed: {exc}", source="ServiceLog")

        logger.info(log_message)
        _post_ui_log(log_message, source="ServiceLog")
        if stop_event is None:
            time.sleep(10)
            continue

        # Wait up to 10s but exit early if stop is requested.
        if hasattr(stop_event, "wait"):
            if stop_event.wait(timeout=10):
                return
        else:
            time.sleep(10)


if __name__ == "__main__":
    main()