# Multispectral Quality Training Framework

This folder is for offline model development only. The normal desktop UI does
not train models when the user opens SSC, TA or pH pages.

## Data Unit

One training row is one fruit sample, not one image. A sample folder should
contain one or more RGB images and one image for each enabled multispectral
wavelength.

Recommended structure:

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
      450.png
      560.png
      670.png
    white/
      450.png
      560.png
      670.png
  metadata.json
```

The development filter profile currently enables 450, 560 and 670 nm only so
the existing offline capture flow can still be validated. Replace
`quality_algorithm/filter_config.development.json` with the final measured
filter wheel configuration before formal training.

## Labels

`labels.csv` format:

```csv
sample_id,ssc,ta,ph
sample_001,11.6,0.43,3.58
sample_002,10.2,0.51,3.43
```

SSC uses `°Brix`; TA uses the unit selected by the experiment protocol; pH uses
pH. If a label is missing, that sample is skipped for that target.

## Algorithm Flow

```text
raw sample folder
-> dark/white calibration R = (sample - dark) / (white - dark)
-> RGB ROI mask
-> one mean reflectance feature per enabled band
-> RAW/SNV/MSC preprocessing
-> PLSR/SVR/RF regression
-> evaluation
-> model.joblib + metadata.json
-> UI inference through quality_prediction.py
```

Dark/White is physical reflectance calibration. SNV/MSC are spectral
preprocessing methods. PLSR/SVR/RF are regression models.

## Build Features

```powershell
python training/build_dataset.py --samples D:\FruitData\samples --labels D:\FruitData\labels.csv --output D:\FruitData\features.csv
```

If there are no usable real samples or labels, the script fails with
`Insufficient training dataset`. It never creates fake labels.

## Train One Model

```powershell
python training/train.py --features D:\FruitData\features.csv --target ssc --preprocessing RAW --model PLSR --output-dir trained_models\ssc
```

PLSR searches `n_components` within a valid range. SVR and RF are comparison
baselines, not an ensemble.

## Run The Experiment Matrix

```powershell
python training/train.py --features D:\FruitData\features.csv --target ssc --matrix-output D:\FruitData\ssc_matrix.csv
```

The matrix covers RAW/SNV/MSC x PLSR/SVR/RF and reports R2, RMSE, MAE and RPD
when valid.

## Saved Model Metadata

Each saved model folder contains:

```text
model.joblib
metadata.json
```

Metadata stores target, model type, version, preprocessing, preprocessing
state, wavelengths, feature names, training date, sample count, validation
method, R2, RMSE, MAE, optional RPD, calibration requirement and software
version.

