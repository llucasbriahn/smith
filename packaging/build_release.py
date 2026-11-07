#!/usr/bin/env python3
"""Produce a source archive, self-extracting installer and native packages."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer import ROOT, payload_files, version
from build_packages import EPOCH, build_deb, build_rpm


def source_archive(root=ROOT):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for path in sorted(payload_files(root)):
            data = path.read_bytes()
            entry = tarfile.TarInfo("smith-soundboard-" + version(root) + "/" + str(path.relative_to(root)))
            entry.size, entry.mtime = len(data), EPOCH
            entry.mode = 0o755 if path.suffix == ".sh" else 0o644
            entry.uid = entry.gid = 0
            entry.uname = entry.gname = "root"
            archive.addfile(entry, io.BytesIO(data))
    return gzip.compress(output.getvalue(), mtime=0)


def self_extract(payload):
    checksum = hashlib.sha256(payload).hexdigest()
    script = '''#!/bin/sh
set -eu
SMITH_WORK=$(mktemp -d "${TMPDIR:-/tmp}/smith-installer.XXXXXXXX")
trap 'rm -rf -- "$SMITH_WORK"' EXIT HUP INT TERM
SMITH_LINE=$(awk '/^__SMITH_PAYLOAD__$/ {print NR + 1; exit}' "$0")
tail -n +"$SMITH_LINE" "$0" > "$SMITH_WORK/payload.tar.gz"
if command -v sha256sum >/dev/null 2>&1; then
    SMITH_HASH=$(sha256sum "$SMITH_WORK/payload.tar.gz")
elif command -v shasum >/dev/null 2>&1; then
    SMITH_HASH=$(shasum -a 256 "$SMITH_WORK/payload.tar.gz")
else
    echo 'sha256sum ou shasum é necessário para conferir a integridade.' >&2
    exit 1
fi
SMITH_HASH=${SMITH_HASH%% *}
if [ "$SMITH_HASH" != "CHECKSUM" ]; then
    echo 'O pacote está corrompido: SHA256 incompatível.' >&2
    exit 1
fi
tar -xzf "$SMITH_WORK/payload.tar.gz" -C "$SMITH_WORK"
sh "$SMITH_WORK/smith-soundboard-VERSION/install.sh" "$@"
exit $?
__SMITH_PAYLOAD__
'''.replace("CHECKSUM", checksum).replace("VERSION", version())
    return script.encode() + payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    payload = source_archive()
    source = args.out / f"smith-soundboard-{version()}-source.tar.gz"
    source.write_bytes(payload)
    installer = args.out / f"smith-soundboard-{version()}-install.run"
    installer.write_bytes(self_extract(payload))
    installer.chmod(0o755)
    artifacts = [source, installer, build_deb(args.out), build_rpm(args.out, "fedora"), build_rpm(args.out, "suse")]
    manifest = {"version": version(), "artifacts": [{"file": path.name, "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in artifacts],
                "not_built": ["AppImage: ferramentas de build precisam de download; sem acesso de rede neste ambiente",
                              "Flatpak: validação de ícone pelo bwrap bloqueada pelo isolamento"],
                "validation": "Consulte VALIDATION.md no diretório de distribuição."}
    (args.out / "release.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    lines = [hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name for path in artifacts]
    (args.out / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    for path in artifacts:
        print(path)


if __name__ == "__main__":
    main()
