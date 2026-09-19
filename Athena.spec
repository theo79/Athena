# Hand-maintained Windows one-file console build. Never collect project data files.
from pathlib import Path

root = Path(SPECPATH)
a = Analysis([str(root / 'agent.py')], pathex=[str(root)], binaries=[], datas=[],
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['pytest'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='Athena',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, disable_windowed_traceback=False)
