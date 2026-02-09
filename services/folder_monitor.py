from __future__ import annotations
# from uploader import OrthancUploader
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shutil
import pydicom
from pydicom.errors import InvalidDicomError
from pydicom.misc import is_dicom
import logging
from logging.handlers import RotatingFileHandler
import json
from urllib import request
from urllib.error import URLError
import service_config
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.encaps import encapsulate, generate_pixel_data_frame
from pydicom.uid import (
    ExplicitVRLittleEndian,
    EncapsulatedPDFStorage,
    SecondaryCaptureImageStorage,
    PYDICOM_IMPLEMENTATION_UID,
    generate_uid,
)


@dataclass(frozen=True)
class FolderMonitor:
    # Root path where the date folder will be created (e.g., Desktop).
    root_path: Path
    # Staging path for future use (e.g., temporary processing location).
    staging_path: Path
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
        return cls(
            root_path=Path(service_config.SERVICE_ROOT_PATH),
            staging_path=Path(service_config.SERVICE_STAGING_PATH),
        )
    
    @classmethod
    def staging_from_config(cls) -> "FolderMonitor":
        # Build the monitor using the staging path from service_config.
        return cls(
            root_path=Path(service_config.SERVICE_ROOT_PATH),
            staging_path=Path(service_config.SERVICE_STAGING_PATH),
        )

    def ensure_today_folder(self) -> Path:
        logger = self._get_logger()
        # Create (or find) today's folder under root_path and return its path.
        today_folder_name = datetime.now().strftime(self.date_format)
        today_folder = self.root_path / today_folder_name
        today_folder.mkdir(parents=True, exist_ok=True)
        return today_folder

    def ensure_today_staging_folder(self) -> Path:
        logger = self._get_logger()
        now = datetime.now()

        staging_root = self.staging_path / "Staging"
        year_staging_folder = staging_root / now.strftime("%Y")
        month_staging_folder = year_staging_folder / now.strftime("%m-%Y")
        today_staging_folder = month_staging_folder / now.strftime("%d-%m-%Y")

        today_staging_folder.mkdir(parents=True, exist_ok=True)

        return today_staging_folder

    def _extract_study_info(self, ds) -> dict:
        return {
            "study_uid": getattr(ds, "StudyInstanceUID", None),
            "patient_name": getattr(ds, "PatientName", ""),
            "patient_id": getattr(ds, "PatientID", ""),
            "patient_birth_date": getattr(ds, "PatientBirthDate", ""),
            "patient_sex": getattr(ds, "PatientSex", ""),
            "study_date": getattr(ds, "StudyDate", ""),
            "study_time": getattr(ds, "StudyTime", ""),
            "accession_number": getattr(ds, "AccessionNumber", ""),
            "study_description": getattr(ds, "StudyDescription", ""),
        }

    def _build_file_meta(self, sop_class_uid, sop_instance_uid) -> FileMetaDataset:
        meta = FileMetaDataset()
        meta.FileMetaInformationVersion = b"\x00\x01"
        meta.MediaStorageSOPClassUID = sop_class_uid
        meta.MediaStorageSOPInstanceUID = sop_instance_uid
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
        return meta

    def _create_pdf_dicom(self, pdf_path: Path, out_path: Path, study_info: dict):
        with pdf_path.open("rb") as f:
            pdf_bytes = f.read()

        sop_instance_uid = generate_uid()
        file_meta = self._build_file_meta(EncapsulatedPDFStorage, sop_instance_uid)
        ds = FileDataset(str(out_path), {}, file_meta=file_meta, preamble=b"\x00" * 128)
        ds.is_little_endian = True
        ds.is_implicit_VR = False

        now = datetime.now()
        ds.SOPClassUID = EncapsulatedPDFStorage
        ds.SOPInstanceUID = sop_instance_uid
        ds.StudyInstanceUID = study_info.get("study_uid") or generate_uid()
        ds.SeriesInstanceUID = generate_uid()
        ds.Modality = "DOC"
        ds.SeriesNumber = 1
        ds.InstanceNumber = 1
        ds.ContentDate = now.strftime("%Y%m%d")
        ds.ContentTime = now.strftime("%H%M%S")
        ds.MIMETypeOfEncapsulatedDocument = "application/pdf"
        ds.EncapsulatedDocument = encapsulate([pdf_bytes])
        ds.EncapsulatedDocumentLength = len(pdf_bytes)

        ds.PatientName = study_info.get("patient_name", "")
        ds.PatientID = study_info.get("patient_id", "")
        ds.PatientBirthDate = study_info.get("patient_birth_date", "")
        ds.PatientSex = study_info.get("patient_sex", "")
        ds.StudyDate = study_info.get("study_date", "")
        ds.StudyTime = study_info.get("study_time", "")
        ds.AccessionNumber = study_info.get("accession_number", "")
        ds.StudyDescription = study_info.get("study_description", "")

        ds.save_as(out_path, write_like_original=False)

    def _create_image_dicom(self, image_path: Path, out_path: Path, study_info: dict):
        try:
            from PIL import Image  # type: ignore[import-not-found]
        except Exception:
            return

        image = Image.open(image_path).convert("RGB")
        pixel_bytes = image.tobytes()
        rows, cols = image.size[1], image.size[0]

        sop_instance_uid = generate_uid()
        file_meta = self._build_file_meta(SecondaryCaptureImageStorage, sop_instance_uid)
        ds = FileDataset(str(out_path), {}, file_meta=file_meta, preamble=b"\x00" * 128)
        ds.is_little_endian = True
        ds.is_implicit_VR = False

        now = datetime.now()
        ds.SOPClassUID = SecondaryCaptureImageStorage
        ds.SOPInstanceUID = sop_instance_uid
        ds.StudyInstanceUID = study_info.get("study_uid") or generate_uid()
        ds.SeriesInstanceUID = generate_uid()
        ds.Modality = "SC"
        ds.SeriesNumber = 1
        ds.InstanceNumber = 1
        ds.ContentDate = now.strftime("%Y%m%d")
        ds.ContentTime = now.strftime("%H%M%S")

        ds.PatientName = study_info.get("patient_name", "")
        ds.PatientID = study_info.get("patient_id", "")
        ds.PatientBirthDate = study_info.get("patient_birth_date", "")
        ds.PatientSex = study_info.get("patient_sex", "")
        ds.StudyDate = study_info.get("study_date", "")
        ds.StudyTime = study_info.get("study_time", "")
        ds.AccessionNumber = study_info.get("accession_number", "")
        ds.StudyDescription = study_info.get("study_description", "")

        ds.SamplesPerPixel = 3
        ds.PhotometricInterpretation = "RGB"
        ds.PlanarConfiguration = 0
        ds.Rows = rows
        ds.Columns = cols
        ds.BitsAllocated = 8
        ds.BitsStored = 8
        ds.HighBit = 7
        ds.PixelRepresentation = 0
        ds.PixelData = pixel_bytes

        ds.save_as(out_path, write_like_original=False)

    def _convert_multi_file_to_multiframe(self, dicom_paths, out_path):
        """
        Convert multiple single-frame DICOM files into one multi-frame DICOM.

        Parameters
        ----------
        dicom_paths : list[str]
            Paths to single-frame DICOM files (same series).
        out_path : str
            Output path for the multi-frame DICOM.
        """
        try:
            import numpy as np
        except Exception as e:
            self._post_ui_log(f"NumPy import failed: {e}", source="FolderMonitor")

        if not dicom_paths:
            raise ValueError("dicom_paths cannot be empty")

        # Load all DICOMs
        try:
            datasets = [pydicom.dcmread(p) for p in dicom_paths]
        except Exception as e:
            self._post_ui_log(f"Failed to read DICOM files: {e}", source="FolderMonitor")
            raise

        # Sort by InstanceNumber if present (important!)
        try:
            datasets.sort(key=lambda d: getattr(d, "InstanceNumber", 0))
        except Exception as e:
            self._post_ui_log(f"Failed to sort DICOM files: {e}", source="FolderMonitor")
            raise

        try:
            first_ds = datasets[0]
        except Exception as e:
            self._post_ui_log(f"Failed to get first DICOM dataset: {e}", source="FolderMonitor")
            raise

        # Stack pixel data into (num_frames, rows, cols)
        
        try:
            pixel_arrays = [ds.pixel_array for ds in datasets]
        except Exception as e:
            self._post_ui_log(f"Failed to extract pixel data: {e}", source="FolderMonitor")
            raise

        try:
            pixel_stack = np.stack(pixel_arrays, axis=0)
        except Exception as e:
            self._post_ui_log(f"Failed to stack pixel data: {e}", source="FolderMonitor")
            raise

        # Create new dataset based on first DICOM
        try:
            multi_ds = first_ds.copy()
        except Exception as e:
            self._post_ui_log(f"Failed to copy first DICOM dataset: {e}", source="FolderMonitor")
            raise

        # Update required multi-frame attributes
        try:
            multi_ds.NumberOfFrames = pixel_stack.shape[0]
            multi_ds.PixelData = pixel_stack.tobytes()
        except Exception as e:
            self._post_ui_log(f"Failed to set multi-frame attributes: {e}", source="FolderMonitor")
            raise

        # Generate new UIDs
        try:
            multi_ds.SOPInstanceUID = generate_uid()
            multi_ds.file_meta.MediaStorageSOPInstanceUID = multi_ds.SOPInstanceUID
        except Exception as e:
            self._post_ui_log(f"Failed to generate new UIDs: {e}", source="FolderMonitor")
            raise

        # Remove single-frame–specific attributes if present
        if "InstanceNumber" in multi_ds:
            del multi_ds.InstanceNumber

        # Functional Groups (basic — can be expanded if needed)
        if hasattr(multi_ds, "PerFrameFunctionalGroupsSequence"):
            del multi_ds.PerFrameFunctionalGroupsSequence

        # Save as multi-frame DICOM
        multi_ds.save_as(out_path, write_like_original=False)
        self._post_ui_log(f"Saved multi-frame DICOM to {out_path}", source="FolderMonitor")

    def _format_case_date(self, ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%d-%m-%Y")

    def _format_case_time(self, ts: float) -> str:
        dt = datetime.fromtimestamp(ts)
        hour = dt.strftime("%I").lstrip("0") or "12"
        minute = dt.strftime("%M")
        suffix = dt.strftime("%p").lower()
        return f"{hour}:{minute}{suffix}"

    def _upload_orthanc_folder(self, orthanc_folder: Path, case_name: str):
        marker_path = orthanc_folder / ".uploaded"
        if marker_path.exists():
            return

        dicom_files = [
            p for p in orthanc_folder.rglob("*")
            if p.is_file() and p.suffix.lower() == ".dcm"
        ]
        if not dicom_files:
            return

        try:
            from uploader import OrthancUploader
            uploader = OrthancUploader.from_config()
        except Exception as exc:
            self._post_ui_log(
                f"Orthanc upload skipped for {case_name}: {exc}",
                source="FolderMonitor",
            )
            return

        result = uploader.upload_folder(orthanc_folder)
        if result["uploaded"] == 0:
            return

        if result["failed"] == 0:
            try:
                marker_path.write_text(
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    encoding="utf-8",
                )
            except Exception:
                pass
            self._post_ui_log(
                f"Uploaded {result['uploaded']} DICOM(s) to Orthanc for case {case_name}",
                source="FolderMonitor",
            )
        else:
            self._post_ui_log(
                f"Orthanc upload had {result['failed']} failure(s) for case {case_name}",
                source="FolderMonitor",
            )

    def find_cases(self):
        """
        Search today's folder for direct case folders.
        If a case folder has any files or folders -> a case.
        If empty -> not a case.
        Returns (count, cases_list).
        """
        today_staging_folder = self.ensure_today_staging_folder()
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
            pdf_files = []
            image_files = []
            case_staging_folder = today_staging_folder / case.name
            attachments_folder = case_staging_folder / "Attachments"
            dicoms_folder = case_staging_folder / "Dicoms"
            attachments_folder.mkdir(parents=True, exist_ok=True)
            dicoms_folder.mkdir(parents=True, exist_ok=True)
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
                            pdf_files.append(item)
                            try:
                                shutil.copy2(item, attachments_folder / item.name)
                            except Exception:
                                pass
                        elif ext in IMAGE_EXTS:
                            image_count += 1
                            image_files.append(item)
                            try:
                                shutil.copy2(item, attachments_folder / item.name)
                            except Exception:
                                pass

            except Exception:
                pdf_count = 0
                image_count = 0
            
            # if case has dicom  or dicom project files
            single_dicom_count = 0
            multiple_dicom_count = 0 
            project_count = 0
            has_single_dicom = False
            has_multiple_dicom = False
            has_project = False
            romexis = False
            sop_uids = set()
            study_info = None
            dicom_files = []
            single_dicom_files = []
            multi_series = {}
            try:
                stack = [case]
                while stack:
                    current = stack.pop()

                    for item in current.iterdir():

                        # if folder, add to stack
                        if item.is_dir():
                            stack.append(item)
                            continue

                        # if not file, skip
                        if not item.is_file():
                            continue
                        
                        # check dicom or project
                        if not is_dicom(item):
                            continue
                        try:
                            ds = pydicom.dcmread(item, stop_before_pixels=True)
                        except InvalidDicomError:
                            continue

                        if study_info is None:
                            study_info = self._extract_study_info(ds)

                        sop_uid = getattr(ds, "SOPInstanceUID", None)
                        if sop_uid is not None:
                            if sop_uid in sop_uids:
                                continue
                            sop_uids.add(sop_uid)

                        # if item is a project or single dicom
                        number_of_frames = getattr(ds, "NumberOfFrames", None)
                        if number_of_frames is not None:
                            if int(number_of_frames) > 1:
                                # item is single dicom
                                has_single_dicom = True
                                single_dicom_count += 1
                                single_dicom_files.append(item)
                            else:
                                # item is a project (multi-frame)
                                has_project = True
                                project_count += 1
                        else:
                            # item is multiple dicom (multi-file series)
                            has_multiple_dicom = True
                            series_uid = getattr(ds, "SeriesInstanceUID", None)
                            if not series_uid:
                                series_uid = f"unknown-{case.name}"
                            multi_series.setdefault(series_uid, []).append(item)

                        dicom_files.append(item)

                        try:
                            shutil.copy2(item, dicoms_folder / item.name)
                        except Exception:
                            pass

                        # if dicom has romexis tag
                        impl_version = getattr(getattr(ds, "file_meta", None), "ImplementationVersionName", "")
                        if "ROMEXIS" in str(impl_version).upper():
                            romexis = True

            except Exception:
                single_dicom_count = 0
                multiple_dicom_count = 0
                project_count = 0
                has_single_dicom = False
                has_multiple_dicom = False
                has_project = False
                romexis = False
            

            # getting counts
            has_pdf = pdf_count > 0
            has_images = image_count > 0
            if has_multiple_dicom:
                multiple_dicom_count = len(multi_series)

            multi_dicom_files = []
            if multi_series:
                multi_dicom_files = max(multi_series.values(), key=len)

            has_any_dicom = has_single_dicom or has_multiple_dicom or has_project

            orthanc_folder = case_staging_folder / "Orthanc"
            orthanc_folder.mkdir(parents=True, exist_ok=True)
            if has_single_dicom and romexis:
                for dicom_path in single_dicom_files:
                    try:
                        out_name = f"{dicom_path.stem} DCM {dicom_path.suffix or '.dcm'}"
                        shutil.copy2(dicom_path, orthanc_folder / out_name)
                    except Exception:
                        pass
                self._post_ui_log(
                    f"Copied {len(single_dicom_files)} single Romexis DICOM(s) to Orthanc for case {case.name}"
                )
            elif has_single_dicom and not romexis:
                for dicom_path in single_dicom_files:
                    try:
                        ds = pydicom.dcmread(dicom_path)
                        if not getattr(ds, "file_meta", None):
                            ds.file_meta = FileMetaDataset()
                        ds.file_meta.ImplementationVersionName = "ROMEXIS_10"
                        out_name = f"{dicom_path.stem} DCM {dicom_path.suffix or '.dcm'}"
                        ds.save_as(orthanc_folder / out_name, write_like_original=False)
                    except Exception:
                        pass
                self._post_ui_log(
                    f"Updated ImplementationVersionName and copied {len(single_dicom_files)} single DICOM(s) to Orthanc for case {case.name}"
                )
            elif (not has_single_dicom) and (not romexis) and multi_dicom_files:
                try:
                    out_name = f"{case.name} DCM.dcm"
                    out_path = orthanc_folder / out_name
                    success = self._convert_multi_file_to_multiframe(multi_dicom_files, out_path)
                    if success:
                        self._post_ui_log(
                            f"Converted {len(multi_dicom_files)} multi-file DICOM(s) to multi-frame and copied to Orthanc for case {case.name}"
                        )
                    else:
                        self._post_ui_log(
                            f"Failed to convert multi-file DICOM(s) to multi-frame for case {case.name}"
                        )
                except Exception:
                    self._post_ui_log(
                        f"Error while converting multi-file DICOM(s) for case {case.name}"
                    )
            
            if pdf_files or image_files:
                if study_info is None:
                    study_info = {"study_uid": generate_uid()}
                elif not study_info.get("study_uid"):
                    study_info["study_uid"] = generate_uid()

                orthanc_folder = case_staging_folder / "Orthanc"
                orthanc_folder.mkdir(parents=True, exist_ok=True)

                for pdf_path in pdf_files:
                    try:
                        out_path = orthanc_folder / f"{pdf_path.stem} PDF.dcm"
                        self._create_pdf_dicom(pdf_path, out_path, study_info)
                    except Exception:
                        pass

                for image_path in image_files:
                    try:
                        out_path = orthanc_folder / f"{image_path.stem} IMG.dcm"
                        self._create_image_dicom(image_path, out_path, study_info)
                    except Exception:
                        pass
            
            self._upload_orthanc_folder(orthanc_folder, case.name)

            cases.append({
                "name": case.name, 
                "date": case_date, 
                "time": case_time,
                "has_pdf": has_pdf,
                "pdf_count": pdf_count,
                "has_images": has_images,
                "image_count": image_count,
                "has_single_dicom": has_single_dicom,
                "single_dicom_count": single_dicom_count,
                "has_multiple_dicom": has_multiple_dicom,
                "multiple_dicom_count": multiple_dicom_count,
                "romexis": romexis,
                "has_project": has_project,
                "project_count": project_count
                })

        return len(cases), cases
