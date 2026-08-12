# Model Studio

Model Studio is the local model-training and data-management backend for the
fruit RGB + multispectral quality analyzer. It shares the same HTTP backend as
the detection workstation and reuses `quality_algorithm/` and `training/`.

Start the normal analyzer, then open:

```text
http://127.0.0.1:<port>/model-studio
```

SQLite database:

```text
host_software/static_ui_prototype_bin/model_studio/database/model_studio.sqlite
```

Runtime artifacts:

```text
model_studio/artifacts/features/
model_studio/models/candidates/
trained_models/{ssc,ta,ph}/
```

Only manually published Production models are copied into `trained_models/`.
Candidate and test models remain isolated in Model Studio.

