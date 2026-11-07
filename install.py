#!/usr/bin/python3
"""Compatibility entry point for the complete per-user installer."""
import os
from pathlib import Path
import shutil
import subprocess

import sys
os.execv(sys.executable, [sys.executable, "-I", str(Path(__file__).resolve().parent / "packaging/installer.py"), *sys.argv[1:]])
