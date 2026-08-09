# Quality Algorithm Framework

This module is the first RGB + multispectral quality-analysis framework for the
fruit analyzer. It follows the project paper notes: screen a small set of
multispectral bands from prior hyperspectral work, then train traditional
regression baselines before considering deep learning.

## Flow

```text
Sample Folder
-> Validation by enabled filter bands
-> Dark/White calibration
-> RGB ROI mask
-> Band ROI means
-> RAW/SNV/MSC preprocessing
-> PLSR/SVR/RF model
-> PredictionResult
-> Existing UI
```

## Sample Structure

```text
sample_001/
  rgb/
    rgb_000.png
  multispectral/
    450.png
    560.png
    670.png
  calibration/
    dark/
    white/
  metadata.json
```

One sample is one fruit or one configured fruit group. It can contain one or
more RGB images and one multispectral image per enabled wavelength. RGB count
does not need to equal multispectral count.

## Modules

- `filters.py`: filter wheel / band configuration.
- `calibration.py`: physical dark/white reflectance correction.
- `roi.py`: replaceable RGB fruit mask and identity registration mode.
- `spectral_features.py`: sample validation and feature extraction.
- `preprocessing.py`: RAW, SNV and MSC transforms.
- `dataset.py`: labels and feature CSV generation.
- `model_io.py`: saved model bundles and metadata checks.

No calibration images means `UNCALIBRATED`; development feature extraction may
continue only when explicitly allowed. A saved model can require calibration and
will reject uncalibrated samples.

