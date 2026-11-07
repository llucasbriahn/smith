#!/usr/bin/env python3
"""Stage source assets; AppImageBuilder supplies the interpreter and libraries."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_packages import ROOT, tree

destination = ROOT / "build/SMITH.AppDir"
if destination.exists():
    raise SystemExit("AppDir já existe; use um diretório de build limpo.")
for name, (content, mode) in tree().items():
    path = destination / name.lstrip("/")
    if content is None:
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(mode)
print("AppDir preparado:", destination)
