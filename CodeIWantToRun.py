import datetime
import time
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

# Creates a log every 10 seconds.
log_message = "Testing Service"

def write_to_log(message):
    log_file_path = Path(__file__).resolve().parent / "log.txt"

    logger = logging.getLogger("ServiceLog")
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

    logger.info(message)

def main(stop_event=None):
    while True:
        write_to_log(log_message)
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