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
log_message = "=========== 5 Seconds ============"

_LOGGER_NAME = ""

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
    staging_monitor = None
    try:
        monitor = FolderMonitor.from_config()
        folder_path = monitor.ensure_today_folder()
        staging_monitor = FolderMonitor.staging_from_config()
        staging_folder_path = staging_monitor.ensure_today_staging_folder()
    except Exception as exc:
        logger.warning("FolderMonitor failed to start: %s", exc)
        _post_ui_log(f"FolderMonitor failed to start: {exc}", source="ServiceLog")
    while True:
        # Run folder monitor every 5 seconds
        if monitor is not None:
            try:
                folder_path = monitor.ensure_today_folder()
                if staging_monitor is not None:
                    staging_folder_path = staging_monitor.ensure_today_staging_folder()
                case_count, cases = monitor.find_cases()
                now = time.localtime()
                date_str = time.strftime("%d-%m-%Y", now)
                hour = time.strftime("%I", now).lstrip("0") or "12"
                minute = time.strftime("%M", now)
                suffix = time.strftime("%p", now).lower()
                header_time = f"{hour}.{minute}{suffix}"
                _post_ui_log(f"{date_str} {header_time} - Found {case_count} Cases", source="FolderMonitor")
                for idx, case in enumerate(cases, start=1):
                    name = case.get("name", "")
                    case_date = case.get("date", "")
                    case_time = case.get("time", "")
                    case_has_pdf = case.get("has_pdf", False)
                    case_pdf_count = case.get("pdf_count", 0)
                    case_has_images = case.get("has_images", False)
                    case_image_count = case.get("image_count", 0)
                    case_has_single_dicom = case.get("has_single_dicom", False)
                    case_single_dicom_count = case.get("single_dicom_count", 0)
                    case_has_multiple_dicom = case.get("has_multiple_dicom", False)
                    case_multiple_dicom_count = case.get("multiple_dicom_count", 0)
                    case_has_project = case.get("has_project", False)
                    case_project_count = case.get("project_count", 0)
                    case_romexis = case.get("romexis", False)
                    _post_ui_log(f"         {idx}- {name} - {case_date} - {case_time} - PDFs: {case_pdf_count} - IMGs: {case_image_count} - DICOMs: {case_single_dicom_count} - M-DICOMs: {case_multiple_dicom_count} - Projs: {case_project_count} - Rmx: {case_romexis}", source="FolderMonitor")

            except Exception as exc:
                logger.warning("FolderMonitor check failed: %s", exc)
                _post_ui_log(f"FolderMonitor check failed: {exc}", source="ServiceLog")

        logger.info(log_message)
        _post_ui_log(log_message, source="ServiceLog")
        if stop_event is None:
            time.sleep(5)
            continue

        # Wait up to 5s but exit early if stop is requested.
        if hasattr(stop_event, "wait"):
            if stop_event.wait(timeout=5):
                return
        else:
            time.sleep(5)


if __name__ == "__main__":
    main()