from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import json
import time
import importlib
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
    _ACTIVE_UPLOADS_LOCK = threading.Lock()
    _ACTIVE_UPLOAD_FOLDERS: set[str] = set()

    def __init__(
        self,
        base_url: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        timeout: float = 15.0,
        max_upload_bps: int | None = None,  # bytes per second, None = unlimited
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
        self.max_upload_bps = max_upload_bps if (max_upload_bps and max_upload_bps > 0) else None
        self.session = requests.Session()
        self._token = _TokenState()
        self._service_config_path = Path(getattr(service_config, "__file__", ""))
        self._service_config_mtime = None
        self._service_config_last_check = 0.0

    def _folder_key(self, folder: Path) -> str:
        try:
            return os.path.normcase(str(folder.resolve()))
        except Exception:
            return os.path.normcase(str(folder))

    def _is_upload_active_locally(self, folder_key: str) -> bool:
        with self._ACTIVE_UPLOADS_LOCK:
            return folder_key in self._ACTIVE_UPLOAD_FOLDERS

    def _mark_upload_active(self, folder_key: str) -> None:
        with self._ACTIVE_UPLOADS_LOCK:
            self._ACTIVE_UPLOAD_FOLDERS.add(folder_key)

    def _mark_upload_inactive(self, folder_key: str) -> None:
        with self._ACTIVE_UPLOADS_LOCK:
            self._ACTIVE_UPLOAD_FOLDERS.discard(folder_key)

    def _cleanup_upload_artifacts(self, lock_path: Path, progress_path: Path, temp_folder: Path) -> None:
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            progress_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            if temp_folder.exists():
                shutil.rmtree(temp_folder, ignore_errors=True)
        except Exception:
            pass

    @classmethod
    def from_config(cls) -> "PacsUploader":
        base_url = os.getenv("PACS_BASE_URL")
        token_url = os.getenv("PACS_TOKEN_URL")
        client_id = os.getenv("PACS_CLIENT_ID")
        client_secret = os.getenv("PACS_CLIENT_SECRET")
        max_upload_kbps_env = os.getenv("PACS_MAX_UPLOAD_BPS")
        max_upload_kbps_cfg = getattr(service_config, "PACS_MAX_UPLOAD_BPS", None)
        try:
            max_upload_kbps = int(max_upload_kbps_env or max_upload_kbps_cfg) if (max_upload_kbps_env or max_upload_kbps_cfg) else None
        except Exception:
            max_upload_kbps = None
        max_upload_bps = (max_upload_kbps * 1024) if (max_upload_kbps and max_upload_kbps > 0) else None

        return cls(
            base_url=base_url or getattr(service_config, "PACS_BASE_URL", ""),
            token_url=token_url or getattr(service_config, "PACS_TOKEN_URL", ""),
            client_id=client_id or getattr(service_config, "PACS_CLIENT_ID", ""),
            client_secret=client_secret or getattr(service_config, "PACS_CLIENT_SECRET", ""),
            max_upload_bps=max_upload_bps,
            )

    def _refresh_runtime_config(self) -> None:
        now = time.monotonic()
        if now - self._service_config_last_check < 0.5:
            return
        self._service_config_last_check = now
        try:
            if not self._service_config_path.exists():
                return
            mtime = self._service_config_path.stat().st_mtime
            if self._service_config_mtime is None:
                self._service_config_mtime = mtime
                return
            if mtime != self._service_config_mtime:
                importlib.reload(service_config)
                self._service_config_mtime = mtime
        except Exception:
            pass

    def _get_runtime_max_upload_bps(self) -> int | None:
        self._refresh_runtime_config()
        raw = os.getenv("PACS_MAX_UPLOAD_BPS")
        if raw is None or str(raw).strip() == "":
            raw = getattr(service_config, "PACS_MAX_UPLOAD_BPS", None)
        try:
            kbps = int(raw) if (raw is not None and str(raw).strip() != "") else None
        except Exception:
            kbps = None
        if kbps and kbps > 0:
            return kbps * 1024
        return self.max_upload_bps
    
    def _post_ui_log(self, message: str, source: str = "PacsUploader", color: str | None = None):
        host = getattr(service_config, "SERVICE_API_HOST", "127.0.0.1")
        port = int(getattr(service_config, "SERVICE_API_PORT", 8085))
        url = f"http://{host}:{port}/api/ui-log"
        try:
            payload = {"message": message, "source": source}
            if color:
                payload["color"] = color
            data = json.dumps(payload).encode("utf-8")
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
            def __init__(self, file_path: Path, cb, max_bps_getter):
                self._handle = file_path.open("rb")
                self._total = total_bytes
                self._sent = 0
                self._cb = cb
                self._max_bps_getter = max_bps_getter
                self._start = time.monotonic()
                if self._cb:
                    self._cb(self._sent, self._total)

            def __len__(self):
                return self._total

            def read(self, size=-1):
                chunk = self._handle.read(size)
                if chunk:
                    self._sent += len(chunk)

                    # throttle: keep average rate <= max_bps
                    max_bps = self._max_bps_getter() if self._max_bps_getter else None
                    if max_bps:
                        elapsed = time.monotonic() - self._start
                        expected = self._sent / float(max_bps)
                        if expected > elapsed:
                            time.sleep(expected - elapsed)

                    if self._cb:
                        self._cb(self._sent, self._total)
                return chunk

            def reset(self):
                self._handle.seek(0)
                self._sent = 0
                self._start = time.monotonic()
                if self._cb:
                    self._cb(self._sent, self._total)

            def close(self):
                self._handle.close()

        progress_file = _ProgressFile(path, progress_cb, self._get_runtime_max_upload_bps)
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
                # timeout=self.timeout,
                timeout=2000
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
                    # timeout=self.timeout,
                    timeout=2000
                )
            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                body = (resp.text or "").strip()
                body = body[:2000] if body else "<empty>"
                self._post_ui_log(
                    f"PACS upload failed for {path.name}: {resp.status_code} {body}",
                    color="red"
                )
                self._post_ui_log(f"exc: {exc}", color="red")
                raise exc
            return resp.json()
        finally:
            progress_file.close()

    def _get_sop_instance_uid(self, path: Path) -> str | None:
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
            sop = ds.get("SOPInstanceUID", None)
            return sop
        except Exception as exc:
            self._post_ui_log(f"PACS SOP UID read failed for {path.name}: {exc}", color="red")
            return None
    
    def _get_series_instance_uid(self, path: Path) -> str | None:
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
            series = ds.get("SeriesInstanceUID", None)
            return series
        except Exception as exc:
            self._post_ui_log(f"PACS Series UID read failed for {path.name}: {exc}", color="red")
            return None
        

    def _instance_exists_by_uid(self, sop_instance_uid: str, series_instance_uid: str) -> bool:
        if not sop_instance_uid:
            return False
        token = self._get_token()
        payload = {
            "Level": "Instance",
            "Query": {"SOPInstanceUID": sop_instance_uid},
            "Limit": 1,
        }
        # SeriesInstanceUID
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
            if bool(resp.json()):
                payload2 = {
                    "Level": "Instance",
                    "Query": {"SeriesInstanceUID": series_instance_uid},
                    "Limit": 1,
                }
                try:
                    resp2 = self.session.post(
                        f"{self.base_url}/tools/find",
                        json=payload2,
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=self.timeout,
                    )
                    resp2.raise_for_status()
                    return bool(resp2.json())
                except Exception as exc:
                    self._post_ui_log(
                        f"PACS lookup failed for SeriesInstanceUID {series_instance_uid}: {exc}",
                        color="red"
                    )
                    return False
            else:
                return False
            
        except Exception as exc:
            self._post_ui_log(
                f"PACS lookup failed for SOPInstanceUID {sop_instance_uid}: {exc}",
                color="red"
            )
            return False
    
    def _confirm_instance_uploaded(
        self,
        sop_instance_uid: str,
        series_instance_uid: str,
        attempts: int = 3,
        delay: float = 0.5,
    ) -> bool:
        for _ in range(max(1, attempts)):
            if self._instance_exists_by_uid(sop_instance_uid, series_instance_uid):
                return True
            time.sleep(delay)
        return False

    def add_label(self, study_uid: str, label: str) -> bool:
        """
        Add a label to a study in PACS.

        Parameters
        ----------
        study_uid : str
            The Study Instance UID to label.
        label : str
            The label text to add.

        Returns
        -------
        bool
            True if successful, False otherwise.
        """
        if not study_uid or not label:
            self._post_ui_log(f"Invalid study_uid or label for PACS labeling", color="red")
            return False
        
        try:
            token = self._get_token()
            payload = {
                "Level": "Study",
                "Query": {
                    "StudyInstanceUID": study_uid
                }
            }
            
            try:
                lookup = self.session.post(
                    f"{self.base_url}/tools/find",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except Exception as exc:
                self._post_ui_log(f"Lookup failed: {exc}", color="red")
                return False

            if lookup.status_code == 401:
                self._token = _TokenState()
                token = self._get_token()
                lookup = self.session.get(
                    f"{self.base_url}/tools/find/{study_uid}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self.timeout,
                )
            
            try:
                lookup_results = lookup.json()  
            except Exception as exc:
                self._post_ui_log(f"Lookup JSON parsing failed: {exc}", color="red")
                return False
            
            if not lookup_results:
                self._post_ui_log(f"Study {study_uid} not found in PACS", color="red")
                return False
            try:
                orthanc_id = lookup_results[0]
            except (IndexError, KeyError) as exc:
                self._post_ui_log(f"Failed to extract Orthanc ID for study {study_uid}: {exc}", color="red")
                return False

            # Add the label to the study
            try:
                resp = self.session.put(
                    f"{self.base_url}/studies/{orthanc_id}/labels/{label}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self.timeout,
                )
            except Exception as exc:
                self._post_ui_log(f"Label add failed: {exc}", color="red")
                return False

            if resp.status_code == 401:
                self._token = _TokenState()
                token = self._get_token()
                resp = self.session.post(
                    f"{self.base_url}/studies/{orthanc_id}/labels/{label}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self.timeout,
                )

            meout=self.timeout,
            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                body = (resp.text or "").strip()
                body = body[:2000] if body else "<empty>"
                self._post_ui_log(
                    f"PACS label add failed for study {study_uid} (orthanc_id: {orthanc_id}): {resp.status_code} {body}",
                    color="red"
                )
                self._post_ui_log(f"exc: {exc}", color="red")
                return False
            
            self._post_ui_log(f"PACS label added for {study_uid} (orthanc_id: {orthanc_id}): {label}", color="green")
            return True
        except Exception as exc:
            self._post_ui_log(f"PACS label add error for study {study_uid}: {exc}", color="red")
            return False

    def _extract_study_uid_from_folder(self, folder: Path) -> str | None:
        """Extract StudyInstanceUID from first DICOM file in folder."""
        try:
            for path in folder.rglob("*.dcm"):
                if ".pacs" not in str(path):  # Skip temp/progress files
                    ds = pydicom.dcmread(path, stop_before_pixels=True)
                    StudyInstanceUID = ds.get("StudyInstanceUID", None)
                    return StudyInstanceUID
        except Exception as exc:
            self._post_ui_log(f"Failed to extract StudyInstanceUID from folder {folder}: {exc}", color="red")
            pass
        return None

    def upload_folder_async(self, folder: Path, case_name: str = "", labels: list[str] = None) -> dict:
        if labels is None:
            labels = []
        if not folder.exists():
            return {"started": False, "reason": "missing-folder"}

        folder_key = self._folder_key(folder)
        lock_path = folder / ".pacs_uploading"
        progress_path = folder / ".pacs_progress"
        temp_folder = folder / "temp"

        if self._is_upload_active_locally(folder_key):
            self._post_ui_log("PACS upload already running in current service process")
            return {"started": False, "reason": "in-progress"}

        if lock_path.exists():
            try:
                percent = progress_path.read_text(encoding="utf-8").strip()
            except Exception:
                percent = ""
            if percent:
                self._post_ui_log(
                    f"Detected interrupted PACS upload state ({percent}%). Restarting and resuming where possible."
                )
            else:
                self._post_ui_log(
                    "Detected interrupted PACS upload state. Restarting and resuming where possible."
                )
            self._cleanup_upload_artifacts(lock_path, progress_path, temp_folder)

        try:
            lock_path.write_text(
                time.strftime("%Y-%m-%d %H:%M:%S"),
                encoding="utf-8",
            )
        except Exception:
            pass
        try:
            progress_path.write_text("0", encoding="utf-8")
        except Exception:
            pass

        self._mark_upload_active(folder_key)

        worker = threading.Thread(
            target=self._upload_folder_worker,
            args=(folder, case_name, labels, folder_key),
            daemon=True,
        )
        worker.start()
        return {"started": True}

    def _upload_folder_worker(self, folder: Path, case_name: str, labels: list[str] = None, folder_key: str = "") -> None:
        if labels is None:
            labels = []
        lock_path = folder / ".pacs_uploading"
        progress_path = folder / ".pacs_progress"
        temp_folder = folder / "temp"
        try:
            try:
                if temp_folder.exists():
                    shutil.rmtree(temp_folder, ignore_errors=True)
            except Exception:
                pass
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
                return

            self._post_ui_log(f"PACS upload started{label}: {total} file(s)")

            for index, path in enumerate(sorted(files), start=1):
                try:
                    sop_instance_uid = self._get_sop_instance_uid(path)
                    series_instance_uid = self._get_series_instance_uid(path)
                    if sop_instance_uid and self._instance_exists_by_uid(sop_instance_uid, series_instance_uid):
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
                        if self._confirm_instance_uploaded(sop_instance_uid, series_instance_uid):
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

                self._post_ui_log(f"PACS upload completed{label}: {uploaded} file(s)")

            # After successful upload, add labels to the study
            if labels and not failures:
                study_uid = self._extract_study_uid_from_folder(folder)
                if study_uid:
                    for label in labels:
                        try:
                            self.add_label(study_uid, label)
                        except Exception as exc:
                            self._post_ui_log(f"Failed to add label '{label}' to study: {exc}")
        finally:
            self._cleanup_upload_artifacts(lock_path, progress_path, temp_folder)
            if folder_key:
                self._mark_upload_inactive(folder_key)