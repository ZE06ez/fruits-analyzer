# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('index.html', '.'),
        ('styles.css', '.'),
        ('app.js', '.'),
        ('assets', 'assets'),
        ('sample_data', 'sample_data'),
        ('model_studio', 'model_studio'),
    ],
    hiddenimports=[
        'PIL',
        'PIL.Image',
        'numpy',
        'cv2',
        'pipeline_v2',
        'model_studio.service',
        'training.train',
        'quality_algorithm.dataset',
        'quality_algorithm.filters',
        'quality_algorithm.spectral_features',
        'quality_algorithm.model_io',
        'quality_algorithm.preprocessing',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pytest', 'sphinx', 'docutils', 'lxml'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='FruitTasteAnalyzer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app_icon.ico'],
)
