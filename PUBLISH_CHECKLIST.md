# GitHub publication checklist

- [ ] Confirm the repository is still Private while reviewing the first upload.
- [ ] Confirm there are no DICOM, NIfTI, patient spreadsheets, CAT12 outputs, generated reports, or logs.
- [ ] Search the repository for `/Users/`, email addresses, patient names, and local absolute paths.
- [ ] Verify `src/app.py` and `src/launcher.sh` contain no personal machine path.
- [ ] Review README and THIRD_PARTY documentation.
- [ ] Choose and add an open-source LICENSE before switching the repository to Public.
- [ ] Create a GitHub Release for the packaged `.app.zip` rather than storing the app bundle in the source tree.
