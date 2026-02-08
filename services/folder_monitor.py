from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import json
from urllib import request
from urllib.error import URLError

import service_config


@dataclass(frozen=True)
class FolderMonitor:
    # Root path where the date folder will be created (e.g., Desktop).
    root_path: Path
    # Format for the monitored folder name (default: dd-mm-YYYY).
    date_format: str = "%d-%m-%Y"

    def _get_logger(self) -> logging.Logger:
        logger = logging.getLogger("ServiceLog")
        if not logger.handlers:
            log_file_path = Path(__file__).resolve().parent / "log.txt"
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

    def _post_ui_log(self, message: str, source: str = "FolderMonitor"):
        host = getattr(service_config, "SERVICE_API_HOST", "127.0.0.1")
        port = int(getattr(service_config, "SERVICE_API_PORT", 8085))
        url = f"http://{host}:{port}/api/ui-log"
        try:
            data = json.dumps({"message": message, "source": source}).encode("utf-8")
            req = request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json; charset=utf-8")
            with request.urlopen(req, timeout=0.5) as resp:
                resp.read(0)
        except URLError:
            pass
        except Exception:
            pass

    @classmethod
    def from_config(cls) -> "FolderMonitor":
        # Build the monitor using the root path from service_config.
        return cls(root_path=Path(service_config.SERVICE_ROOT_PATH))

    def ensure_today_folder(self) -> Path:
        logger = self._get_logger()
        # Create (or find) today's folder under root_path and return its path.
        today_folder_name = datetime.now().strftime(self.date_format)
        today_folder = self.root_path / today_folder_name
        today_folder.mkdir(parents=True, exist_ok=True)

        logger.info("Monitor folder ready: %s", today_folder)
        # Best-effort: also send to UI log API
        self._post_ui_log(f"Monitor folder ready: {today_folder}")

        return today_folder

    def _format_case_date(self, ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%d-%m-%Y")

    def _format_case_time(self, ts: float) -> str:
        dt = datetime.fromtimestamp(ts)
        hour = dt.strftime("%I").lstrip("0") or "12"
        minute = dt.strftime("%M")
        suffix = dt.strftime("%p").lower()
        return f"{hour}:{minute}{suffix}"

    def find_cases(self):
        """
        Search today's folder for direct case folders.
        If a case folder has any files or folders -> a case.
        If empty -> not a case.
        Returns (count, cases_list).
        """
        today_folder_name = datetime.now().strftime(self.date_format)
        today_folder = self.root_path / today_folder_name
        if not today_folder.exists():
            return 0, []

        EXCLUDED_NAMES = {"cbct", "new folder"}
        cases = []
        for case in today_folder.iterdir():
             # if not a folder, skip
            if not case.is_dir():
                continue
            
            # if folder name is in excluded names, skip
            folder_name = case.name.strip()
            folder_name_lower = folder_name.lower()
            if folder_name_lower in EXCLUDED_NAMES or " " not in folder_name:
                continue
            
            # if folder is empty, skip
            try:
                has_contents = any(case.iterdir())
            except Exception:
                has_contents = False
            if not has_contents:
                continue

            # date and last modified time
            try:
                stat = case.stat()
                case_date = self._format_case_date(stat.st_ctime)
                case_time = self._format_case_time(stat.st_mtime)
            except Exception:
                case_date = case_time = ""

            # if case has pdf or image files
            IGNORED_SUBFOLDERS = {"planmeca romexis", "ondemand 3d"}
            PDF_EXTS = {".pdf"}
            IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
            pdf_count = 0
            image_count = 0
            try:
                stack = [case]
                while stack:
                    current = stack.pop()

                    for item in current.iterdir():

                        # skip viewers entirely
                        if item.is_dir():
                            if item.name.lower() in IGNORED_SUBFOLDERS:
                                continue
                            stack.append(item)
                            continue

                        # process files
                        if not item.is_file():
                            continue
                        
                        # check extensions
                        ext = item.suffix.lower()
                        if ext in PDF_EXTS:
                            pdf_count += 1
                        elif ext in IMAGE_EXTS:
                            image_count += 1

            except Exception:
                pdf_count = 0
                image_count = 0
            
            # getting counts
            has_pdf = pdf_count > 0
            has_images = image_count > 0

            cases.append({
                "name": case.name, 
                "date": case_date, 
                "time": case_time,
                "has_pdf": has_pdf,
                "pdf_count": pdf_count,
                "has_images": has_images,
                "image_count": image_count,
                })

        return len(cases), cases
