#!/usr/bin/env python3
"""Bundle a data-only Python app using the installed GNOME runtime (no SDK build)."""
import argparse
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_packages import ROOT, tree, version


def build(destination, runtime_version="50", arch=None):
    destination.mkdir(parents=True, exist_ok=True)
    arch = arch or platform.machine()
    with tempfile.TemporaryDirectory(prefix="smith-flatpak-") as work:
        work = Path(work)
        app, repository = work / "App", work / "repo"
        subprocess.run(["flatpak", "build-init", "--arch=" + arch, str(app), "org.smith.Soundboard",
                        "org.gnome.Platform", "org.gnome.Platform", runtime_version], check=True)
        for name, (content, mode) in tree(app_dir="/app/share/smith-soundboard").items():
            relative = name.removeprefix("/app/") if name.startswith("/app/") else name.removeprefix("/usr/")
            path = app / "files" / relative
            if content is None:
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                path.chmod(mode)
        subprocess.run(["flatpak", "build-finish", "--command=smith-soundboard", "--share=ipc", "--socket=wayland",
                        "--socket=fallback-x11", "--socket=pulseaudio", "--filesystem=home", str(app)], check=True)
        subprocess.run(["flatpak", "build-export", "--arch=" + arch, str(repository), str(app), "stable"], check=True)
        output = destination / f"smith-soundboard-{version()}-{arch}.flatpak"
        subprocess.run(["flatpak", "build-bundle", "--arch=" + arch,
                        "--runtime-repo=https://flathub.org/repo/flathub.flatpakrepo", str(repository), str(output),
                        "org.smith.Soundboard", "stable"], check=True)
        return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--runtime", default="50")
    parser.add_argument("--arch", default=None)
    args = parser.parse_args()
    print(build(args.out, args.runtime, args.arch))
