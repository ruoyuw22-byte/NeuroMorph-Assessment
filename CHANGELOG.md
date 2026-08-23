# Changelog

## v0.11.1

- Added TIV consistency validation.
- When a directly parsed TIV value has an abnormal scale or is inconsistent with absolute tissue volumes, the application validates it against `CSF + GM + WM` and uses the consistent absolute-volume result.
- Preserved CAT12 result reuse, DICOM/NIfTI workflow, MRI three-view rendering, scale display, and PDF report generation.

## v0.11.0

- Established the isolated backend session model for the macOS application.
- Added native path authorization and improved CAT12 result reuse detection.
- Added environment status, seven-stage task tracking, MRI preview, and report output workflow.
