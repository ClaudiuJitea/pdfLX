from PyInstaller.utils.hooks import collect_all

signing_datas, signing_binaries, signing_imports = [], [], []
for package in ("pyhanko", "pyhanko_certvalidator", "asn1crypto"):
    data, binaries, imports = collect_all(package)
    signing_datas.extend(data)
    signing_binaries.extend(binaries)
    signing_imports.extend(imports)

a = Analysis(
    ['run_pdflx.py'],
    pathex=[],
    binaries=signing_binaries,
    datas=[('pdflx/img', 'pdflx/img'), ('pdflx/icons', 'pdflx/icons'), ('pdflx/COPYING', 'pdflx'),
           ('pdflx/style.css', 'pdflx')] + signing_datas,
    hiddenimports=['gi.repository.Gtk', 'gi.repository.Gio', 'gi.repository.GLib', 'gi.repository.Adw', 'gi.repository.Gdk', 'gi.repository.GdkPixbuf', 'gi.repository.Pango', 'gi.repository.PangoCairo', 'cairo'] + signing_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='pdflx',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='pdflx',
)
