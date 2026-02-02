import datetime
import time
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

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

def main(stop_event=None):
    logger.info("Service CodeIWantToRun is starting...")
    try:
        folder_path = FolderMonitor.from_config().ensure_today_folder()
        logger.info("FolderMonitor started. Folder: %s", folder_path)
    except Exception as exc:
        logger.warning("FolderMonitor failed to start: %s", exc)
    while True:
        logger.info(log_message)
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