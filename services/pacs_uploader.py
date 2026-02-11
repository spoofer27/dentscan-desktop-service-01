from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import json
import time
import threading
import shutil
from urllib import request
from urllib.error import URLError

import requests
import pydicom

import service_config


@dataclass
class _TokenState:
    access_token: str = ""
    expires_at: float = 0.0


class PacsUploader:
    def __init__(
        self,
        base_url: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        timeout: float = 15.0,
    ):
        if not base_url:
            raise ValueError("PACS base_url is required")
        if not token_url:
            raise ValueError("PACS token_url is required")
        if not client_id:
            raise ValueError("PACS client_id is required")
        if not client_secret:
            raise ValueError("PACS client_secret is required")

        self.base_url = base_url.rstrip("/")
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self.session = requests.Session()
        self._token = _TokenState()

    @classmethod
    def from_config(cls) -> "PacsUploader":
        base_url = os.getenv("PACS_BASE_URL")
        token_url = os.getenv("PACS_TOKEN_URL")
        client_id = os.getenv("PACS_CLIENT_ID")
        client_secret = os.getenv("PACS_CLIENT_SECRET")
        return cls(
            base_url=base_url or getattr(service_config, "PACS_BASE_URL", ""),
            token_url=token_url or getattr(service_config, "PACS_TOKEN_URL", ""),
            client_id=client_id or getattr(service_config, "PACS_CLIENT_ID", ""),
            client_secret=client_secret or getattr(service_config, "PACS_CLIENT_SECRET", ""),
        )

    def _post_ui_log(self, message: str, source: str = "PacsUploader"):
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

    def _fetch_token(self) -> _TokenState:
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        resp = self.session.post(self.token_url, data=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        access_token = data.get("access_token", "")
        expires_in = float(data.get("expires_in", 0))
        if not access_token:
            raise ValueError("PACS token response missing access_token")
        expires_at = time.time() + max(0.0, expires_in - 30.0)
        return _TokenState(access_token=access_token, expires_at=expires_at)

    def _get_token(self) -> str:
        if self._token.access_token and time.time() < self._token.expires_at:
            return self._token.access_token
        self._token = self._fetch_token()
        return self._token.access_token

    def upload_file(self, path: Path, progress_cb=None) -> dict:
        token = self._get_token()
        total_bytes = path.stat().st_size

        class _ProgressFile:
            def __init__(self, file_path: Path, cb):
                self._handle = file_path.open("rb")
                self._total = total_bytes
                self._sent = 0
                self._cb = cb
                if self._cb:
                    self._cb(self._sent, self._total)

            def __len__(self):
                return self._total

            def read(self, size=-1):
                chunk = self._handle.read(size)
                if chunk:
                    self._sent += len(chunk)
                    if self._cb:
                        self._cb(self._sent, self._total)
                return chunk

            def reset(self):
                self._handle.seek(0)
                self._sent = 0
                if self._cb:
                    self._cb(self._sent, self._total)

            def close(self):
                self._handle.close()

        progress_file = _ProgressFile(path, progress_cb)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/dicom",
            "Content-Length": str(len(progress_file)),
        }
        try:
            resp = self.session.post(
                f"{self.base_url}/instances",
                data=progress_file,
                headers=headers,
                timeout=self.timeout,
            )
            if resp.status_code == 401:
                self._token = _TokenState()
                token = self._get_token()
                headers["Authorization"] = f"Bearer {token}"
                progress_file.reset()
                resp = self.session.post(
                    f"{self.base_url}/instances",
                    data=progress_file,
                    headers=headers,
                    timeout=self.timeout,
                )
            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                body = (resp.text or "").strip()
                body = body[:2000] if body else "<empty>"
                self._post_ui_log(
                    f"PACS upload failed for {path.name}: {resp.status_code} {body}"
                )
                raise exc
            return resp.json()
        finally:
            progress_file.close()

    def _get_sop_instance_uid(self, path: Path) -> str | None:
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
            return getattr(ds, "SOPInstanceUID", None)
        except Exception as exc:
            self._post_ui_log(f"PACS SOP UID read failed for {path.name}: {exc}")
            return None

    def _instance_exists_by_uid(self, sop_instance_uid: str) -> bool:
        if not sop_instance_uid:
            return False
        token = self._get_token()
        payload = {
            "Level": "Instance",
            "Query": {"SOPInstanceUID": sop_instance_uid},
            "Limit": 1,
        }
        try:
            resp = self.session.post(
                f"{self.base_url}/tools/find",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
            if resp.status_code == 401:
                self._token = _TokenState()
                token = self._get_token()
                resp = self.session.post(
                    f"{self.base_url}/tools/find",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self.timeout,
                )
            if resp.status_code == 404:
                return False
            resp.raise_for_status()
            return bool(resp.json())
        except Exception as exc:
            self._post_ui_log(
                f"PACS lookup failed for SOPInstanceUID {sop_instance_uid}: {exc}"
            )
            return False

    def _confirm_instance_uploaded(
        self,
        sop_instance_uid: str,
        attempts: int = 3,
        delay: float = 0.5,
    ) -> bool:
        for _ in range(max(1, attempts)):
            if self._instance_exists_by_uid(sop_instance_uid):
                return True
            time.sleep(delay)
        return False

    def upload_folder_async(self, folder: Path, case_name: str = "") -> dict:
        if not folder.exists():
            return {"started": False, "reason": "missing-folder"}

        marker_path = folder / ".pacs_uploaded"
        if marker_path.exists():
            return {"started": False, "reason": "already-uploaded"}

        lock_path = folder / ".pacs_uploading"
        progress_path = folder / ".pacs_progress"
        if lock_path.exists():
            try:
                percent = progress_path.read_text(encoding="utf-8").strip()
            except Exception:
                percent = ""
            percent_value = None
            if percent:
                try:
                    percent_value = int(percent)
                except Exception:
                    percent_value = None
            if percent_value == 100 and not marker_path.exists():
                try:
                    stale = (time.time() - lock_path.stat().st_mtime) > 900
                except Exception:
                    stale = True
                if stale:
                    try:
                        lock_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    try:
                        progress_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    self._post_ui_log("Cleared stale PACS upload state")
                else:
                    self._post_ui_log(f"PACS upload in progress: {percent}%")
                    return {"started": False, "reason": "in-progress"}
            elif percent:
                self._post_ui_log(f"PACS upload in progress: {percent}%")
                return {"started": False, "reason": "in-progress"}
            else:
                self._post_ui_log("PACS upload already in progress")
                return {"started": False, "reason": "in-progress"}

        try:
            lock_path.write_text(
                time.strftime("%Y-%m-%d %H:%M:%S"),
                encoding="utf-8",
            )
        except Exception:
            pass

        worker = threading.Thread(
            target=self._upload_folder_worker,
            args=(folder, case_name),
            daemon=True,
        )
        worker.start()
        return {"started": True}

    def _upload_folder_worker(self, folder: Path, case_name: str) -> None:
        lock_path = folder / ".pacs_uploading"
        progress_path = folder / ".pacs_progress"
        marker_path = folder / ".pacs_uploaded"
        temp_folder = folder / "temp"
        try:
            temp_folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        files = [
            p for p in folder.rglob("*")
            if p.is_file()
            and p.suffix.lower() == ".dcm"
            and temp_folder not in p.parents
        ]

        total = len(files)
        uploaded = 0
        failures = []
        label = f" for case {case_name}" if case_name else ""

        if not total:
            try:
                lock_path.unlink(missing_ok=True)
            except Exception:
                pass
            return

        self._post_ui_log(f"PACS upload started{label}: {total} file(s)")

        for index, path in enumerate(sorted(files), start=1):
            try:
                sop_instance_uid = self._get_sop_instance_uid(path)
                if sop_instance_uid and self._instance_exists_by_uid(sop_instance_uid):
                    self._post_ui_log(
                        f"PACS already has{label}: {path.name}, skipping"
                    )
                    continue

                dest_path = temp_folder / path.name
                if dest_path.exists():
                    try:
                        if dest_path.stat().st_size != path.stat().st_size:
                            dest_path = temp_folder / (
                                f"{path.stem}_{int(time.time() * 1000)}"
                                f"{path.suffix or '.dcm'}"
                            )
                    except Exception:
                        dest_path = temp_folder / (
                            f"{path.stem}_{int(time.time() * 1000)}"
                            f"{path.suffix or '.dcm'}"
                        )
                shutil.copy2(path, dest_path)

                last_percent = -1

                def progress_cb(sent, total_bytes):
                    nonlocal last_percent
                    if total_bytes <= 0:
                        return
                    percent = int(sent / total_bytes * 100)
                    if percent == last_percent:
                        return
                    last_percent = percent
                    try:
                        progress_path.write_text(str(percent), encoding="utf-8")
                    except Exception:
                        pass
                    self._post_ui_log(
                        f"PACS upload progress{label}: {percent}% ({path.name})"
                    )

                self.upload_file(dest_path, progress_cb=progress_cb)
                uploaded += 1
                if sop_instance_uid:
                    if self._confirm_instance_uploaded(sop_instance_uid):
                        self._post_ui_log(
                            f"PACS upload confirmed{label}: {path.name}"
                        )
                    else:
                        failures.append(
                            {"path": str(path), "error": "upload-not-confirmed"}
                        )
                        self._post_ui_log(
                            f"PACS upload not confirmed{label}: {path.name}"
                        )
                else:
                    self._post_ui_log(
                        f"PACS upload completed{label}: {path.name} (no SOPInstanceUID)"
                    )
            except Exception as exc:
                failures.append({"path": str(path), "error": str(exc)})

        if failures:
            for f in failures:
                self._post_ui_log(
                    f"PACS upload failed{label}: {f['path']} - {f['error']}"
                )
            self._post_ui_log(
                f"PACS upload completed{label} with {len(failures)} failure(s) out of {len(files)}"
            )
        else:
            try:
                progress_path.write_text("100", encoding="utf-8")
            except Exception:
                pass
            try:
                marker_path.write_text(
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    encoding="utf-8",
                )
            except Exception:
                pass
            self._post_ui_log(f"PACS upload completed{label}: {uploaded} file(s)")

        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            progress_path.unlink(missing_ok=True)
        except Exception:
            pass
