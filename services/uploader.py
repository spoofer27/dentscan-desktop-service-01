from __future__ import annotations

from pathlib import Path
import requests

import service_config


class OrthancUploader:
    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 10.0,
    ):
        if not base_url:
            raise ValueError("Orthanc base_url is required")
        self.base_url = base_url.rstrip("/")
        self.auth = (username, password) if username and password else None
        self.timeout = timeout
        self.session = requests.Session()

    @classmethod
    def from_config(cls) -> "OrthancUploader":
        url = getattr(service_config, "ORTHANC_URL", "")
        username = getattr(service_config, "ORTHANC_USERNAME", "")
        password = getattr(service_config, "ORTHANC_PASSWORD", "")
        return cls(url, username or None, password or None)

    def system_info(self) -> dict:
        resp = self.session.get(
            f"{self.base_url}/system",
            auth=self.auth,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def upload_file(self, path: Path) -> dict:
        with path.open("rb") as handle:
            data = handle.read()
        resp = self.session.post(
            f"{self.base_url}/instances",
            data=data,
            headers={"Content-Type": "application/dicom"},
            auth=self.auth,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def upload_folder(self, folder: Path) -> dict:
        if not folder.exists():
            return {"uploaded": 0, "failed": 0, "failures": []}

        files = [
            p for p in folder.rglob("*")
            if p.is_file() and p.suffix.lower() == ".dcm"
        ]

        uploaded = 0
        failures = []
        for path in sorted(files):
            try:
                self.upload_file(path)
                uploaded += 1
            except Exception as exc:
                failures.append({"path": str(path), "error": str(exc)})

        return {"uploaded": uploaded, "failed": len(failures), "failures": failures}