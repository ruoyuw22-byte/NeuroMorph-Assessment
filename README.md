# NeuroMorph Assessment (NMA)

## Screenshot

![NeuroMorph Assessment Overview](resources/nma_overview.png)

## Overview

NeuroMorph Assessment (NMA) is an automated structural MRI morphometry
and CAT12-based brain volume analysis system.

The software integrates MRI data organization, CAT12 processing,
quantitative brain tissue measurement, visualization, and automated
report generation into a unified workflow.

## Key Features

### Automated MRI Workflow

-   MRI data import and organization
-   T1-weighted image identification
-   CAT12 processing integration
-   Brain tissue segmentation
-   Quantitative volume extraction
-   Visualization
-   Automated report generation

### Quantitative Morphometry

NMA extracts structural measurements including:

-   Total intracranial volume (TIV)
-   Gray matter volume (GM)
-   White matter volume (WM)
-   Cerebrospinal fluid volume (CSF)

The system includes quantitative consistency validation during result
extraction.

### Visualization and Reporting

Features include:

-   MRI three-plane visualization
-   Quantitative result preview
-   Processing progress monitoring
-   Automated PDF report generation

## Workflow

``` text
MRI Data
   |
   v
Data Import
   |
   v
T1 Image Detection
   |
   v
CAT12 Processing
   |
   v
Tissue Segmentation
   |
   v
Volume Quantification
   |
   v
Visualization and Report
```

## Project Structure

``` text
NeuroMorph-Assessment/

├── src/
│   ├── app.py
│   ├── ui.html
│   ├── NMAWebView.m
│   ├── launcher.sh
│   └── Info.plist
│
├── resources/
├── scripts/
├── examples/
├── docs/
├── requirements.txt
└── README.md
```

## Software Requirements

NeuroMorph Assessment requires MATLAB together with SPM12 and CAT12 for
structural MRI processing.

The software was developed and tested under the following environment:

  Software   Version
  ---------- ---------
  MATLAB     R2022b
  SPM12      SPM12
  CAT12      CAT12.8

------------------------------------------------------------------------

## MATLAB

MATLAB is required for running SPM12 and CAT12 neuroimaging pipelines.

Recommended version:

``` text
MATLAB R2022b
```

Official website:

https://www.mathworks.com/products/matlab.html

------------------------------------------------------------------------

## SPM12

SPM12 (Statistical Parametric Mapping) is a MATLAB-based neuroimaging
analysis toolbox required by NeuroMorph Assessment.

SPM12 is used for:

-   MRI preprocessing framework
-   Statistical parametric mapping functions
-   Integration with CAT12 toolbox

Official download:

https://www.fil.ion.ucl.ac.uk/spm/software/spm12/

After installation, add the SPM12 directory to MATLAB:

``` matlab
addpath('/path/to/spm12')

spm('Defaults','fMRI')
spm_jobman('initcfg')
```

------------------------------------------------------------------------

## CAT12

CAT12 (Computational Anatomy Toolbox 12) is an extension toolbox for
SPM12.

NeuroMorph Assessment uses CAT12 for:

-   Brain tissue segmentation
-   Gray matter estimation
-   White matter estimation
-   Cerebrospinal fluid estimation
-   Total intracranial volume (TIV) extraction

Official website:

https://neuro-jena.github.io/cat/

Recommended installation structure:

``` text
spm12/
│
└── toolbox/
    │
    └── cat12/
```

Add CAT12 path in MATLAB:

``` matlab
addpath('/path/to/spm12')
addpath('/path/to/spm12/toolbox/cat12')
```

------------------------------------------------------------------------

## Configuration

Before running NeuroMorph Assessment, please ensure:

1.  MATLAB is correctly installed.
2.  SPM12 is available in MATLAB path.
3.  CAT12 is installed under the SPM12 toolbox directory.
4.  Required paths are correctly configured in the application.


## Installation

Requirements:

-   macOS
-   Python 3.x
-   MATLAB with SPM12
-   CAT12 toolbox
-   dcm2niix

Python dependencies are provided in:

``` text
requirements.txt
```

## Usage

1.  Import MRI data.
2.  Configure analysis environment.
3.  Run the processing workflow.
4.  Review quantitative results.
5.  Generate reports.

## Documentation

Additional documentation is available in:

``` text
docs/
```

## Version

Current version:

``` text
v0.11.1
```

## Citation

Citation information will be updated with the final publication or
software registration information.

## License

License information will be added after dependency review.

## Acknowledgements

This project uses open-source neuroimaging tools including SPM12 and
CAT12.
