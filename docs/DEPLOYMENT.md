# Windows Deployment

## Runtime Boundary

Application resources are the packaged Python modules, static UI, Model Studio static files, and `config/*.example.json`. A frozen EXE writes mutable data under `%LOCALAPPDATA%\FruitTasteAnalyzer\app_data`:

```text
config/
database/model_studio.sqlite
database/inspection.sqlite
model_studio_data/
model_studio_artifacts/
model_studio_models/
trained_models/
logs/
outputs/
backups/
```

This permits installation under Program Files and paths containing spaces or Unicode. Source development intentionally retains its existing local layout so old development datasets are not moved automatically.

## Install And Run

```powershell
cd host_software/static_ui_prototype_bin
python -m pip install -r requirements.txt
python launcher.py
```

Build the Windows executable from the same directory:

```powershell
pyinstaller --noconfirm --clean FruitTasteAnalyzer.spec
```

`GET /api/health` reports backend/database readiness. It does not probe or certify hardware.

## Recovery

Before a database schema upgrade, the software makes a SQLite backup in `backups/`. Do not delete a corrupt database to make the application start: `DATABASE_MIGRATION_FAILED` is intentional protection against silent data loss. After a process crash, pending training jobs are marked `Interrupted` and unfinished inspections `FAILED_RECOVERABLE`; camera/motor motion is never resumed automatically.
