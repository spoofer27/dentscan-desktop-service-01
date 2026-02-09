# import service_config
# from pathlib import Path
# import pydicom
# ds = pydicom.dcmread(str(file_path))
# sop_uid = str(getattr(ds, "SOPInstanceUID", ""))
# ds.file_meta.ImplementationVersionName = 'ROMEXIS_10'
# ds.InstitutionName = service_config.SERVICE_BRANCH_NAME

# def validate_dicom(self, file_path: Path) -> bool:
#         """Validate if file is a valid DICOM file"""
#         try:
#             import pydicom
#             pydicom.dcmread(file_path)
#             return True
#         except:
#             return False