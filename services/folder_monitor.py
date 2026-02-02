from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

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

        return today_folder
