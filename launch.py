#!/usr/bin/env python3
"""Entry point shared by native packages, user installs and portable builds."""
from pathlib import Path
import sys

if sys.version_info < (3, 10):
    raise SystemExit("SMITH Soundboard requer Python 3.10 ou superior.")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from soundboard.__main__ import main
raise SystemExit(main())
