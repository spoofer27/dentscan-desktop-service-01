from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
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
    # Institution name for the monitor.
    institution_name: str = ""

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
            institution_name=service_config.INSTITUTION_NAME,
        )
    
    @classmethod
    def staging_from_config(cls) -> "FolderMonitor":
        # Build the monitor using the staging path from service_config.
        return cls(
            root_path=Path(service_config.SERVICE_ROOT_PATH),
            staging_path=Path(service_config.SERVICE_STAGING_PATH),
            institution_name=service_config.INSTITUTION_NAME,
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

    def ensure_yesterday_folder(self) -> Path:
        yesterday = datetime.now() - timedelta(days=1)
        yesterday_folder_name = yesterday.strftime(self.date_format)
        yesterday_folder = self.root_path / yesterday_folder_name
        return yesterday_folder

    def ensure_yesterday_staging_folder(self) -> Path:
        yesterday = datetime.now() - timedelta(days=1)
        staging_root = self.staging_path / "Staging"
        year_staging_folder = staging_root / yesterday.strftime("%Y")
        month_staging_folder = year_staging_folder / yesterday.strftime("%m-%Y")
        yesterday_staging_folder = month_staging_folder / yesterday.strftime("%d-%m-%Y")
        yesterday_staging_folder.mkdir(parents=True, exist_ok=True)
        return yesterday_staging_folder

    def _extract_study_info(self, ds) -> dict:
        return {
            "sop_uid": getattr(ds.file_meta, "MediaStorageSOPInstanceUID", None),
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

    def _create_pdf_dicom(self, pdf_path: Path, out_path: Path, study_info: dict, case_name: str = "", labels: list[str] = []):
        labels = []
        with pdf_path.open("rb") as f:
            pdf_bytes = f.read()

        sop_instance_uid = study_info.get("sop_uid") or generate_uid()
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
        ds.InstitutionName = self.institution_name
        ds.SeriesNumber = 1
        ds.InstanceNumber = 1
        ds.ContentDate = now.strftime("%Y%m%d")
        ds.ContentTime = now.strftime("%H%M%S")
        ds.MIMETypeOfEncapsulatedDocument = "application/pdf"
        ds.EncapsulatedDocument = encapsulate([pdf_bytes])
        ds.EncapsulatedDocumentLength = len(pdf_bytes)

        patient_name = study_info.get("patient_name", None)
        ds.PatientName = patient_name if patient_name else case_name
        ds.PatientID = study_info.get("patient_id", "")
        ds.PatientBirthDate = study_info.get("patient_birth_date", "")
        ds.PatientSex = study_info.get("patient_sex", "")
        ds.StudyDate = study_info.get("study_date", "")
        ds.StudyTime = study_info.get("study_time", "")
        ds.AccessionNumber = study_info.get("accession_number", "")
        ds.StudyDescription = study_info.get("study_description", "")

        ds.save_as(out_path, write_like_original=False)
        self._post_ui_log(f"Created PDF DICOM for {pdf_path.name} in Orthanc staging for case {case_name}", source="FolderMonitor")

    def _create_image_dicom(self, image_path: Path, out_path: Path, study_info: dict, case_name: str = ""):
        try:
            from PIL import Image  # type: ignore[import-not-found]
        except Exception:
            return

        image = Image.open(image_path).convert("RGB")
        pixel_bytes = image.tobytes()
        rows, cols = image.size[1], image.size[0]

        sop_instance_uid = study_info.get("sop_uid") or generate_uid()
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
        ds.InstitutionName = self.institution_name

        patient_name = study_info.get("patient_name", None)
        ds.PatientName = patient_name if patient_name else case_name
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
            multi_ds.InstitutionName = self.institution_name
        except Exception as e:
            self._post_ui_log(f"Failed to set multi-frame attributes: {e}", source="FolderMonitor")
            raise

        # Generate new UIDs
        try:
            multi_ds.SOPInstanceUID = first_ds.get("SOPInstanceUID", generate_uid())
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
        # self._post_ui_log(f"Saved multi-frame DICOM to {out_path}", source="FolderMonitor")

    def _format_case_date(self, ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%d-%m-%Y")

    def _format_case_time(self, ts: float) -> str:
        dt = datetime.fromtimestamp(ts)
        hour = dt.strftime("%I").lstrip("0") or "12"
        minute = dt.strftime("%M")
        suffix = dt.strftime("%p").lower()
        return f"{hour}:{minute}{suffix}"

    def _upload_pacs_folder(self, orthanc_folder: Path, case_name: str, labels: list[str] = None):
        if labels is None:
            labels = []
        try:
            from pacs_uploader import PacsUploader
            uploader = PacsUploader.from_config()
        except Exception as exc:
            self._post_ui_log(
                f"PACS upload skipped for {case_name}: {exc}",
                source="FolderMonitor",
            )
            return

        uploader.upload_folder_async(orthanc_folder, case_name, labels=labels)

    def _is_case_staged(self, case_name: str, staging_folder: Path) -> bool:
        case_staging_folder = staging_folder / case_name
        orthanc_folder = case_staging_folder / "Orthanc"
        if not orthanc_folder.exists():
            return False
        try:
            return any(orthanc_folder.iterdir())
        except Exception:
            return False

    def _is_case_uploaded_to_pacs(self, orthanc_folder: Path) -> bool:
        if not orthanc_folder.exists():
            return False
        try:
            from pacs_uploader import PacsUploader
            uploader = PacsUploader.from_config()
        except Exception:
            return False

        try:
            for dicom_path in orthanc_folder.rglob("*.dcm"):
                if dicom_path.name.startswith("."):
                    continue
                try:
                    ds = pydicom.dcmread(dicom_path, stop_before_pixels=True)
                    sop_uid = getattr(ds, "SOPInstanceUID", None)
                    series_uid = getattr(ds, "SeriesInstanceUID", None)
                    if sop_uid and series_uid:
                        if uploader._instance_exists_by_uid(sop_uid, series_uid):
                            return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _add_case_label(self, study_uid: str, label: str):
        try:
            from pacs_uploader import PacsUploader
            uploader = PacsUploader.from_config()
        except Exception as exc:
            self._post_ui_log(
                f"PACS label skipped for {study_uid}: {exc}",
                source="FolderMonitor",
            )
            return

        uploader.add_label(study_uid, label)

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
            if not case.is_dir(): # if not a folder, skip
                # self._post_ui_log(f"Skipping non-folder item in today's folder: {case.name}", source="FolderMonitor")
                continue
            
            # if folder name is in excluded names, skip
            folder_name = case.name.strip()
            folder_name_lower = folder_name.lower()
            if folder_name_lower in EXCLUDED_NAMES or " " not in folder_name:
                # self._post_ui_log(f"Skipping folder with excluded name or no space: {case.name}", source="FolderMonitor")   
                continue
            
            # if folder is empty, skip
            try:
                has_contents = any(case.iterdir())
            except Exception:
                has_contents = False
                
            if not has_contents:
                # self._post_ui_log(f"Skipping empty folder in today's folder: {case.name}", source="FolderMonitor")
                continue

            # date and last modified time
            try:
                stat = case.stat()
                case_date = self._format_case_date(stat.st_ctime)
                case_time = self._format_case_time(stat.st_mtime)
                # self._post_ui_log(f"Found case folder: {case.name} - Date: {case_date} - Time: {case_time}", source="FolderMonitor")
            except Exception:
                self._post_ui_log(f"Found case folder: {case.name} - Date/Time info unavailable", source="FolderMonitor")
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
            
            try: # trying to check pdfs and images
                stack = [case]
                while stack:
                    current = stack.pop()
                    for item in current.iterdir():
                        if item.is_dir(): # skip viewers entirely
                            if item.name.lower() in IGNORED_SUBFOLDERS:
                                continue
                            stack.append(item)  
                            continue

                        if not item.is_file(): # process files
                            continue
                        
                        ext = item.suffix.lower()
                        if ext in PDF_EXTS: # check pdf extensions
                            pdf_count += 1
                            pdf_files.append(item)
                            try:
                                dest_path = attachments_folder / item.name
                                if dest_path.exists():
                                    if dest_path.stat().st_size == item.stat().st_size:
                                        continue
                                shutil.copy2(item, dest_path)
                                continue
                            except Exception as exc:
                                self._post_ui_log(f"Failed to copy PDF file {item.name}: {exc}", source="FolderMonitor")
                                pass
                        
                        elif ext in IMAGE_EXTS: # check image extensions
                            image_count += 1
                            image_files.append(item)
                            try:
                                dest_path = attachments_folder / item.name
                                if dest_path.exists():
                                    if dest_path.stat().st_size == item.stat().st_size:
                                        continue
                                shutil.copy2(item, dest_path)
                                continue
                            except Exception as exc:
                                self._post_ui_log(f"Failed to copy image file {item.name}: {exc}", source="FolderMonitor")
                                pass
            except Exception as exc:
                self._post_ui_log(f"Error while scanning for PDFs/images in case {case.name}: {exc}", source="FolderMonitor")
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
            dicom_2d_files = []
            single_dicom_files = []
            project_files = []
            multi_series = {}
            
            try: # trying to check dicoms
                stack = [(case, None)]  # Stack now contains tuples of (path, relative_path_from_case)
                while stack:
                    current, rel_path = stack.pop()
                    for item in current.iterdir():   
                        # self._post_ui_log(f"Checking item in case {case.name}: {item.name}", source="FolderMonitor")                      
                        if item.is_dir():
                            new_rel_path = f"{rel_path}/{item.name}" if rel_path else item.name
                            stack.append((item, new_rel_path))
                            # self._post_ui_log(f"Found subfolder in case {case.name}: {item.name}", source="FolderMonitor")
                            continue
                            # self._post_ui_log(f"Found subfolder in case {case.name}: {item.name}", source="FolderMonitor")
                            # if "ondemand" not in item.name.lower():
                            #     self._post_ui_log(f" NOT Adding subfolder to stack for case {case.name}: {item.name}", source="FolderMonitor")
                            #     continue
                            # else:
                            #     self._post_ui_log(f" Adding subfolder to stack for case {case.name}: {item.name}", source="FolderMonitor")
                            #     stack.append((item, new_rel_path))
                            #     continue
                        
                        if not item.is_file(): # if not file, skip
                            self._post_ui_log(f"Skipping non-file item in case {case.name}: {item.name}", source="FolderMonitor")
                            continue
                        
                        if not is_dicom(item): 
                            # self._post_ui_log(f"Skipping non-DICOM file in case {case.name}: {item.name}", source="FolderMonitor")  
                            continue
                        
                        try: # trying to read dicom
                            ds = pydicom.dcmread(item, stop_before_pixels=True, force=True)
                            # self._post_ui_log(f"Read DICOM file in case {case.name}: {item.name}", source="FolderMonitor")
                            # if dicom has romexis tag
                            impl_version = getattr(getattr(ds, "file_meta", None), "ImplementationVersionName", "")
                            if romexis == False:
                                if "ROMEXIS" in str(impl_version).upper():
                                    romexis = True
                        except InvalidDicomError as exc:
                            self._post_ui_log(f"Invalid DICOM file {item.name}, skipping: {exc}", source="FolderMonitor")
                            continue

                        if study_info is None:
                            # self._post_ui_log(f"Extracting study info from DICOM file in case {case.name}: {item.name}", source="FolderMonitor")
                            study_info = self._extract_study_info(ds)

                        sop_uid = getattr(ds, "SOPInstanceUID", None)
                        if sop_uid is not None:
                            if sop_uid in sop_uids:
                                continue
                            sop_uids.add(sop_uid)

                        # if item is a project or single dicom
                        number_of_frames = getattr(ds, "NumberOfFrames", None)
                        modality = getattr(ds, "Modality", None)
                        is_from_ondemand = rel_path and "ondemand 3d" in rel_path.lower()
                        
                        # is_from_ondemand = rel_path and "ondemand 3d" in rel_path.lower()
                        # Rule: Only append DICOMs with modality 'CT' from OnDemand 3D folder
                        # if parent_folder and parent_folder.lower() == "ondemand 3d":
                        #     if modality and modality.upper() != "CT":
                        #         continue
                        
                        if number_of_frames is not None: # number_of_frames exist
                            if int(number_of_frames) > 1:  # item is single dicom with multiple frames
                                if modality and modality.upper() == "CT" and is_from_ondemand:
                                    has_single_dicom = True
                                    single_dicom_count += 1
                                    single_dicom_files.append(item)
                                else:
                                    continue
                            else:  # item is a project (multi-frame)
                                if modality and modality.upper() == "CT" and is_from_ondemand:
                                    has_project = True
                                    project_files.append(item)
                                    project_count += 1
                                else:
                                    continue
                        else:  # item is multiple dicom (multi-file series) or 2D dicom
                            if modality.upper() != "CT":
                                dicom_2d_files.append(item)
                            else:
                                has_multiple_dicom = True
                                series_uid = getattr(ds, "SeriesInstanceUID", None)
                                if not series_uid:
                                    series_uid = f"unknown-{case.name}"
                                multi_series.setdefault(series_uid, []).append(item)
                                
                        dicom_files.append(item)

                        try: # trying to copy dicom to staging dicom folder
                            dest_path = dicoms_folder / item.name
                            if dest_path.exists():
                                if dest_path.stat().st_size == item.stat().st_size:
                                    continue
                            shutil.copy2(item, dest_path)
                        except Exception as exc:
                            self._post_ui_log(f"Failed to copy DICOM file {item.name} to dicoms for case {case.name}: {exc}", source="FolderMonitor")

                        # if dicom has romexis tag
                        impl_version = getattr(getattr(ds, "file_meta", None), "ImplementationVersionName", "")
                        if romexis == False:
                            if "ROMEXIS" in str(impl_version).upper():
                                romexis = True

            except Exception as exc:
                self._post_ui_log(f"Error while scanning for DICOMs in case {case.name}: {exc}", source="FolderMonitor")
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
            multi_dicom_files = []
            if has_multiple_dicom:
                multiple_dicom_count = len(multi_series)  
            if multi_series:
                multi_dicom_files = max(multi_series.values(), key=len)

            has_any_dicom = has_single_dicom or has_multiple_dicom or has_project
            orthanc_folder = case_staging_folder / "Orthanc"
            orthanc_folder.mkdir(parents=True, exist_ok=True)

            # orthanc stagging logic:
            case_labels = []

            if has_single_dicom and romexis: # if single dicom(s) and romexis -> copy as is
                case_labels.append("3D-DICOM")
                for dicom_path in single_dicom_files:
                    try:
                        out_name = f"{dicom_path.stem} DCM {dicom_path.suffix or '.dcm'}"
                        out_path = orthanc_folder / out_name
                        if out_path.exists():
                            continue
                        ds = pydicom.dcmread(dicom_path, stop_before_pixels=True, force=True)
                        if not getattr(ds, "file_meta", None):
                            ds.file_meta = FileMetaDataset()
                        ds.InstitutionName = self.institution_name
                        shutil.copy2(dicom_path, out_path)
                    except Exception as exc:
                        self._post_ui_log(f"Failed to copy {dicom_path.name} to Orthanc staging for case {case.name}: {exc}", source="FolderMonitor")
                        pass
                    
            elif has_single_dicom and not romexis: # if single and ! romexis -> fix and copy
                case_labels.append("3D-DICOM")
                for dicom_path in single_dicom_files:
                    try:
                        out_name = f"{dicom_path.stem} DCM {dicom_path.suffix or '.dcm'}"
                        out_path = orthanc_folder / out_name
                        if out_path.exists():
                            continue
                        ds = pydicom.dcmread(dicom_path)
                        if not getattr(ds, "file_meta", None):
                            ds.file_meta = FileMetaDataset()
                        ds.file_meta.ImplementationVersionName = "ROMEXIS_10"
                        ds.InstitutionName = self.institution_name
                        ds.save_as(out_path, write_like_original=False)
                    except Exception as exc:
                        self._post_ui_log(f"Failed to update Implementation and copy {dicom_path.name} to Orthanc staging for case {case.name}: {exc}", source="FolderMonitor")
                        pass

            elif (not has_single_dicom) and (not romexis) and multi_dicom_files:  # if multiple and ! romexis -> convert and copy
                case_labels.append("3D-DICOM")
                ds = pydicom.dcmread(multi_dicom_files[0], stop_before_pixels=True, force=True)
                if ds.Modality.upper() == "CT": # cheking if it's CBCT or 2D dicom
                    try:
                        out_name = f"{case.name} DCM.dcm"
                        out_path = orthanc_folder / out_name
                        if out_path.exists():
                                continue
                        try:
                            success = self._convert_multi_file_to_multiframe(multi_dicom_files, out_path)
                        except Exception as exc:
                            self._post_ui_log(f"Multi-frame conversion error for case {case.name}: {exc}", source="FolderMonitor")
                            success = False
                    except Exception as exc:
                        self._post_ui_log(
                            f"Error while converting multi-file DICOM(s) for case {case.name}: {exc}", source="FolderMonitor")

            if has_project: # if project -> copy as is
                case_labels.append("OD3D")
                for project_path in project_files:
                    try:
                        out_name = f"{project_path.stem} DCM {project_path.suffix or '.dcm'}"
                        out_path = orthanc_folder / out_name
                        if out_path.exists():
                            continue
                        ds = pydicom.dcmread(project_path, stop_before_pixels=True, force=True)
                        if not getattr(ds, "file_meta", None):  
                            ds.file_meta = FileMetaDataset()
                        ds.InstitutionName = self.institution_name
                        shutil.copy2(project_path, out_path)
                    except Exception as exc:
                        self._post_ui_log(f"Failed to copy {project_path.name} to Orthanc staging for case {case.name}: {exc}", source="FolderMonitor")
                        pass

            elif dicom_2d_files: # if 2D dicom copy as is 
                case_labels.append("2D-DICOM")
                for dicom_path in dicom_2d_files:
                    try:
                        out_name = f"{dicom_path.stem} DCM {dicom_path.suffix or '.dcm'}"
                        out_path = orthanc_folder / out_name
                        if out_path.exists():
                            continue
                        ds = pydicom.dcmread(dicom_path, stop_before_pixels=True, force=True)
                        if not getattr(ds, "file_meta", None):  
                            ds.file_meta = FileMetaDataset()
                        ds.InstitutionName = self.institution_name
                        shutil.copy2(dicom_path, out_path)
                    except Exception as exc:
                        self._post_ui_log(f"Failed to copy image DICOM {dicom_path.name} to Orthanc staging for case {case.name}: {exc}", source="FolderMonitor")
                        pass

            if pdf_files or image_files: #  create studyinfo
                if study_info is None:
                    study_info = {"study_uid": generate_uid()}
                elif not study_info.get("study_uid"):
                    study_info["study_uid"] = generate_uid()

                orthanc_folder = case_staging_folder / "Orthanc"
                orthanc_folder.mkdir(parents=True, exist_ok=True)

                for pdf_path in pdf_files:
                    try:
                        out_path = orthanc_folder / f"{pdf_path.stem} PDF.dcm"
                        if out_path.exists():
                            continue
                        self._create_pdf_dicom(pdf_path, out_path, study_info, case.name)
                        case_labels.append("PDF")
                    except Exception as exc:
                        self._post_ui_log(f"Failed to create PDF DICOM for {pdf_path.name}: {exc}", source="FolderMonitor")
                        pass

                for image_path in image_files:
                    try:
                        out_path = orthanc_folder / f"{image_path.stem} IMG.dcm"
                        if out_path.exists():
                            continue
                        self._create_image_dicom(image_path, out_path, study_info, case.name)
                        case_labels.append("Image")
                    except Exception as exc:
                        self._post_ui_log(f"Failed to create image DICOM for {image_path.name}: {exc}", source="FolderMonitor")
                        pass
            

            # Upload to PACS
            self._upload_pacs_folder(orthanc_folder, case.name, labels=case_labels)

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

    def find_yesterday_cases(self):
        """
        Process yesterday's cases: check if they are staged and uploaded to PACS.
        If not staged -> stage them.
        If staged but not uploaded -> upload them.
        Returns (processed_count, cases_list).
        """
        yesterday_folder = self.ensure_yesterday_folder()
        yesterday_staging_folder = self.ensure_yesterday_staging_folder()
        
        if not yesterday_folder.exists():
            self._post_ui_log("Yesterday's folder not found, skipping yesterday processing", source="FolderMonitor")
            return 0, []

        EXCLUDED_NAMES = {"cbct", "new folder"}
        processed_cases = []
        
        for case in yesterday_folder.iterdir():
            if not case.is_dir():
                continue
            
            folder_name = case.name.strip()
            folder_name_lower = folder_name.lower()
            if folder_name_lower in EXCLUDED_NAMES or " " not in folder_name:
                continue
            
            try:
                has_contents = any(case.iterdir())
            except Exception:
                has_contents = False
                
            if not has_contents:
                continue

            case_staging_folder = yesterday_staging_folder / case.name
            orthanc_folder = case_staging_folder / "Orthanc"
            
            # Check if case is already staged
            is_staged = self._is_case_staged(case.name, yesterday_staging_folder)
            
            # Check if case is already uploaded to PACS
            is_uploaded = False
            if is_staged:
                is_uploaded = self._is_case_uploaded_to_pacs(orthanc_folder)
            
            # Decision logic
            if is_uploaded:
                # Already uploaded, skip
                continue
            elif is_staged and not is_uploaded:
                # Staged but not uploaded -> upload
                self._post_ui_log(f"Yesterday case '{case.name}' is staged but not uploaded. Uploading now...", source="FolderMonitor")
                self._upload_pacs_folder(orthanc_folder, case.name, labels=["Yesterday-Recovery"])
                processed_cases.append({"name": case.name, "action": "uploaded"})
            else:
                # Not staged -> stage and upload (reuse find_cases logic for this specific case)
                self._post_ui_log(f"Yesterday case '{case.name}' not staged. Processing and uploading...", source="FolderMonitor")
                
                # Process this case using the same logic as find_cases
                # This is a simplified approach - we call the staging/upload logic
                try:
                    self._process_single_case(case, yesterday_staging_folder)
                    processed_cases.append({"name": case.name, "action": "staged-and-uploaded"})
                except Exception as exc:
                    self._post_ui_log(f"Failed to process yesterday case '{case.name}': {exc}", source="FolderMonitor")
                    processed_cases.append({"name": case.name, "action": "failed"})
        
        if processed_cases:
            self._post_ui_log(f"Yesterday processing: {len(processed_cases)} case(s) processed", source="FolderMonitor")
        
        return len(processed_cases), processed_cases

    def _process_single_case(self, case: Path, staging_folder: Path):
        """
        Process a single case: stage it and upload to PACS.
        This method contains the core staging logic extracted from find_cases.
        """
        case_staging_folder = staging_folder / case.name
        attachments_folder = case_staging_folder / "Attachments"
        dicoms_folder = case_staging_folder / "Dicoms"
        orthanc_folder = case_staging_folder / "Orthanc"
        
        attachments_folder.mkdir(parents=True, exist_ok=True)
        dicoms_folder.mkdir(parents=True, exist_ok=True)
        orthanc_folder.mkdir(parents=True, exist_ok=True)

        # Scan for PDFs, images, DICOMs (same as find_cases)
        IGNORED_SUBFOLDERS = {"planmeca romexis", "ondemand 3d"}
        PDF_EXTS = {".pdf"}
        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
        
        pdf_files = []
        image_files = []
        dicom_2d_files = []
        single_dicom_files = []
        project_files = []
        multi_series = {}
        study_info = None
        sop_uids = set()
        romexis = False
        
        # Scan for PDFs and images (skip IGNORED_SUBFOLDERS)
        try:
            stack = [case]
            while stack:
                current = stack.pop()
                for item in current.iterdir():
                    if item.is_dir():
                        if item.name.lower() in IGNORED_SUBFOLDERS:
                            continue
                        stack.append(item)
                        continue
                    
                    if not item.is_file():
                        continue
                    
                    ext = item.suffix.lower()
                    if ext in PDF_EXTS:
                        pdf_files.append(item)
                        try:
                            dest_path = attachments_folder / item.name
                            if not dest_path.exists():
                                shutil.copy2(item, dest_path)
                        except Exception:
                            pass
                    elif ext in IMAGE_EXTS:
                        image_files.append(item)
                        try:
                            dest_path = attachments_folder / item.name
                            if not dest_path.exists():
                                shutil.copy2(item, dest_path)
                        except Exception:
                            pass
        except Exception:
            pass
        
        # Scan for DICOMs (scan all folders including IGNORED_SUBFOLDERS)
        stack = [(case, None)]
        while stack:
            current, rel_path = stack.pop()
            for item in current.iterdir():
                if item.is_dir() and 'ondemand' in item.name.lower():
                    new_rel_path = f"{rel_path}/{item.name}" if rel_path else item.name
                    stack.append((item, new_rel_path))
                    continue
                
                if not item.is_file():
                    continue
                
                # Handle DICOMs
                if is_dicom(item):
                    try:
                        ds = pydicom.dcmread(item, stop_before_pixels=True, force=True)
                        
                        if study_info is None:
                            study_info = self._extract_study_info(ds)
                        
                        sop_uid = getattr(ds, "SOPInstanceUID", None)
                        if sop_uid and sop_uid not in sop_uids:
                            sop_uids.add(sop_uid)
                        else:
                            continue
                        
                        impl_version = getattr(getattr(ds, "file_meta", None), "ImplementationVersionName", "")
                        if not romexis and "ROMEXIS" in str(impl_version).upper():
                            romexis = True
                        
                        number_of_frames = getattr(ds, "NumberOfFrames", None)
                        modality = getattr(ds, "Modality", None)
                        is_from_ondemand = rel_path and "ondemand 3d" in rel_path.lower()
                        
                        if number_of_frames is not None:
                            if int(number_of_frames) > 1:
                                if modality and modality.upper() == "CT" and is_from_ondemand:
                                    single_dicom_files.append(item)
                            else:
                                if modality and modality.upper() == "CT" and is_from_ondemand:
                                    project_files.append(item)
                        else:
                            if modality and modality.upper() != "CT":
                                dicom_2d_files.append(item)
                            else:
                                series_uid = getattr(ds, "SeriesInstanceUID", None)
                                if not series_uid:
                                    series_uid = f"unknown-{case.name}"
                                multi_series.setdefault(series_uid, []).append(item)
                        
                        # Copy to dicoms folder
                        try:
                            dest_path = dicoms_folder / item.name
                            if not dest_path.exists():
                                shutil.copy2(item, dest_path)
                        except Exception:
                            pass
                            
                    except Exception:
                        pass
        
        # Stage DICOMs to Orthanc folder
        case_labels = []
        
        # Handle single DICOMs
        if single_dicom_files:
            case_labels.append("3D-DICOM")
            for dicom_path in single_dicom_files:
                try:
                    out_name = f"{dicom_path.stem} DCM {dicom_path.suffix or '.dcm'}"
                    out_path = orthanc_folder / out_name
                    if out_path.exists():
                        continue
                    
                    if romexis:
                        shutil.copy2(dicom_path, out_path)
                    else:
                        ds = pydicom.dcmread(dicom_path)
                        if not getattr(ds, "file_meta", None):
                            ds.file_meta = FileMetaDataset()
                        ds.file_meta.ImplementationVersionName = "ROMEXIS_10"
                        ds.InstitutionName = self.institution_name
                        ds.save_as(out_path, write_like_original=False)
                except Exception:
                    pass
        
        # Handle multi-file series
        elif multi_series:
            case_labels.append("3D-DICOM")
            multi_dicom_files = max(multi_series.values(), key=len)
            if multi_dicom_files:
                try:
                    out_name = f"{case.name} DCM.dcm"
                    out_path = orthanc_folder / out_name
                    if not out_path.exists():
                        self._convert_multi_file_to_multiframe(multi_dicom_files, out_path)
                except Exception:
                    pass
        
        # Handle projects
        if project_files:
            case_labels.append("OD3D")
            for project_path in project_files:
                try:
                    out_name = f"{project_path.stem} DCM {project_path.suffix or '.dcm'}"
                    out_path = orthanc_folder / out_name
                    if out_path.exists():
                        continue
                    shutil.copy2(project_path, out_path)
                except Exception:
                    pass
        
        # Handle 2D DICOMs
        if dicom_2d_files:
            case_labels.append("2D-DICOM")
            for dicom_path in dicom_2d_files:
                try:
                    out_name = f"{dicom_path.stem} DCM {dicom_path.suffix or '.dcm'}"
                    out_path = orthanc_folder / out_name
                    if out_path.exists():
                        continue
                    shutil.copy2(dicom_path, out_path)
                except Exception:
                    pass
        
        # Handle PDFs and images
        if pdf_files or image_files:
            if study_info is None:
                study_info = {"study_uid": generate_uid()}
            elif not study_info.get("study_uid"):
                study_info["study_uid"] = generate_uid()
            
            for pdf_path in pdf_files:
                try:
                    out_path = orthanc_folder / f"{pdf_path.stem} PDF.dcm"
                    if not out_path.exists():
                        self._create_pdf_dicom(pdf_path, out_path, study_info, case.name)
                        case_labels.append("PDF")
                except Exception:
                    pass
            
            for image_path in image_files:
                try:
                    out_path = orthanc_folder / f"{image_path.stem} IMG.dcm"
                    if not out_path.exists():
                        self._create_image_dicom(image_path, out_path, study_info, case.name)
                        case_labels.append("Image")
                except Exception:
                    pass
        
        # Upload to PACS
        case_labels.append("Yesterday-Recovery")
        self._upload_pacs_folder(orthanc_folder, case.name, labels=case_labels)
