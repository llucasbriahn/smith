#!/usr/bin/env python3
"""Reproducible architecture-independent DEB and RPM v4 packages.

Uses only Python's standard library; validates RPM with the system rpm tool
when available. The format implementation follows rpm.org's v4 specification.
"""
import argparse
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer import ROOT, FAMILIES, payload_files, version

EPOCH = int(os.environ.get("SOURCE_DATE_EPOCH", "1794009600"))


def tree(root=ROOT, app_dir="/usr/share/smith-soundboard"):
    files = {}
    for path in payload_files(root):
        relative = path.relative_to(root)
        files[app_dir + "/" + str(relative)] = (path.read_bytes(), 0o755 if path.suffix == ".sh" else 0o644)
    files["/usr/bin/smith-soundboard"] = (("#!/bin/sh\nexec /usr/bin/python3 -I " + app_dir + '/launch.py "$@"\n').encode(), 0o755)
    for name, directory in (("org.smith.Soundboard.desktop", "/usr/share/applications"),
                            ("org.smith.Soundboard.svg", "/usr/share/icons/hicolor/scalable/apps"),
                            ("org.smith.Soundboard.metainfo.xml", "/usr/share/metainfo")):
        files[directory + "/" + name] = ((root / "data" / name).read_bytes(), 0o644)
    for name in ("README.md", "CHANGELOG.md"):
        if (root / name).exists():
            files["/usr/share/doc/smith-soundboard/" + name] = ((root / name).read_bytes(), 0o644)
    files["/usr/share/licenses/smith-soundboard/LICENSE"] = ((root / "LICENSE").read_bytes(), 0o644)
    # Own private directories, while leaving shared /usr paths to the distro.
    for name in list(files):
        parent = Path(name).parent
        while str(parent).startswith((app_dir, "/usr/share/doc/smith-soundboard", "/usr/share/licenses/smith-soundboard")):
            files.setdefault(str(parent), (None, 0o755))
            parent = parent.parent
    return dict(sorted(files.items()))


def tar_bytes(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as output:
        for name, (data, mode) in sorted(files.items()):
            entry = tarfile.TarInfo("." + name if name.startswith("/") else name)
            entry.uid = entry.gid = 0
            entry.uname = entry.gname = "root"
            entry.mtime, entry.mode = EPOCH, mode
            if data is None:
                entry.type = tarfile.DIRTYPE
                output.addfile(entry)
            else:
                entry.size = len(data)
                output.addfile(entry, io.BytesIO(data))
    return gzip.compress(buffer.getvalue(), mtime=0)


def ar_bytes(members):
    output = bytearray(b"!<arch>\n")
    for name, data in members:
        output += (f"{name + '/':<16}{EPOCH:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n").encode("ascii")
        output += data
        if len(data) % 2:
            output += b"\n"
    return bytes(output)


def build_deb(destination, root=ROOT):
    files = tree(root)
    dependencies = ["python3 (>= 3.10)"] + [name for name in FAMILIES["debian"]["core"] if name != "python3"]
    control = (f"Package: smith-soundboard\nVersion: {version(root)}\nArchitecture: all\nSection: sound\nPriority: optional\n"
               "Maintainer: " + os.environ.get("SMITH_MAINTAINER", "SMITH Soundboard contributors") + "\n"
               "Depends: " + ", ".join(dependencies) + "\nRecommends: " + ", ".join(FAMILIES["debian"]["codecs"]) + "\n"
               f"Installed-Size: {math.ceil(sum(len(data) for data, mode in files.values() if data is not None) / 1024)}\n"
               "Description: Native Linux soundboard and virtual microphone\n"
               " Play audio clips while speaking through a virtual microphone.\n"
               " Includes a GTK interface, sound library, recording, trimming and hotkeys.\n")
    checksums = "".join(hashlib.md5(data, usedforsecurity=False).hexdigest() + "  " + name.lstrip("/") + "\n"
                        for name, (data, mode) in files.items() if data is not None)
    controls = {"control": (control.encode(), 0o644), "md5sums": (checksums.encode(), 0o644)}
    target = destination / f"smith-soundboard_{version(root)}_all.deb"
    target.write_bytes(ar_bytes([("debian-binary", b"2.0\n"), ("control.tar.gz", tar_bytes(controls)), ("data.tar.gz", tar_bytes(files))]))
    if shutil.which("dpkg-deb"):
        subprocess.run(["dpkg-deb", "--info", str(target)], check=True)
    return target


def header(tags, region):
    """Serialize a single immutable header region, including its signed trailer."""
    data, index = bytearray(), []
    for tag, kind, value in sorted(tags):
        if kind in (3, 4, 5):
            values = value if isinstance(value, (tuple, list)) else [value]
            alignment, code = {3: (2, "H"), 4: (4, "I"), 5: (8, "Q")}[kind]
            data += b"\0" * (-len(data) % alignment)
            encoded = struct.pack(">" + code * len(values), *values)
            count = len(values)
        elif kind in (6, 8, 9):
            values = [value] if isinstance(value, str) else value
            encoded, count = b"".join(item.encode("utf-8") + b"\0" for item in values), len(values)
        elif kind == 7:
            encoded, count = value, len(value)
        else:
            raise ValueError("Unsupported RPM header type")
        index.append(struct.pack(">IIiI", tag, kind, len(data), count))
        data += encoded
    region_index = struct.pack(">IIiI", region, 7, len(data), 16)
    data += struct.pack(">IIiI", region, 7, -(len(index) + 1) * 16, 16)
    return b"\x8e\xad\xe8\x01\0\0\0\0" + struct.pack(">II", len(index) + 1, len(data)) + region_index + b"".join(index) + data


def cpio_bytes(files):
    output = bytearray()
    for ino, (name, (content, mode)) in enumerate(list(files.items()) + [("TRAILER!!!", (b"", 0))], 1):
        filename = ("." + name if name.startswith("/") else name).encode() + b"\0"
        kind = stat.S_IFDIR if content is None else stat.S_IFREG
        content = content or b""
        fields = (ino, mode | kind, 0, 0, 1, EPOCH, len(content), 0, 0, 0, 0, len(filename), 0)
        output += b"070701" + "".join(f"{field:08x}" for field in fields).encode()
        output += filename
        output += b"\0" * (-len(output) % 4)
        output += content
        output += b"\0" * (-len(output) % 4)
    return bytes(output)


def build_rpm(destination, family="fedora", root=ROOT):
    files = tree(root)
    names = list(files)
    content = [files[name][0] for name in names]
    modes = [files[name][1] | (stat.S_IFDIR if files[name][0] is None else stat.S_IFREG) for name in names]
    directories = sorted({str(Path(name).parent) + "/" for name in names})
    archive = cpio_bytes(files)
    payload = gzip.compress(archive, mtime=0)
    dependencies = list(FAMILIES[family]["core"])
    optional = list(FAMILIES[family]["codecs"])
    release = "1." + family
    tags = [
        (100, 8, ["C"]), (1000, 6, "smith-soundboard"), (1001, 6, version(root)), (1002, 6, release),
        (1004, 9, ["Native Linux soundboard and virtual microphone"]),
        (1005, 9, ["Play audio clips and voice simultaneously through a virtual microphone. GTK sound library, hotkeys, recording and trimming."]),
        (1006, 4, EPOCH), (1007, 6, "reproducible-build"), (1009, 4, sum(len(item) for item in content if item)),
        (1011, 6, "SMITH Soundboard contributors"), (1014, 6, "MIT"), (1016, 9, ["Applications/Multimedia"]),
        (1021, 6, "linux"), (1022, 6, "noarch"), (1028, 4, [len(item) if item else 0 for item in content]),
        (1030, 3, modes), (1033, 3, [0] * len(names)), (1034, 4, [EPOCH] * len(names)),
        (1035, 8, [hashlib.sha256(item).hexdigest() if item is not None else "" for item in content]),
        (1036, 8, [""] * len(names)), (1037, 4, [128 if name.endswith("/LICENSE") else 0 for name in names]),
        (1039, 8, ["root"] * len(names)), (1040, 8, ["root"] * len(names)),
        (1045, 4, [0xffffffff] * len(names)), (1046, 4, len(archive)),
        (1047, 8, ["smith-soundboard"]), (1112, 4, [8]), (1113, 8, [version(root) + "-" + release]),
        (1049, 8, dependencies), (1050, 8, ["3.10" if item == "python3" else "" for item in dependencies]),
        (1048, 4, [12 if item == "python3" else 0 for item in dependencies]),
        (1095, 4, [1] * len(names)), (1096, 4, list(range(1, len(names) + 1))),
        (1116, 4, [directories.index(str(Path(name).parent) + "/") for name in names]),
        (1117, 8, [Path(name).name for name in names]), (1118, 8, directories),
        (1124, 6, "cpio"), (1125, 6, "gzip"), (1126, 6, "9"), (5011, 4, 8),
        (5046, 8, optional), (5047, 8, [""] * len(optional)), (5048, 4, [0] * len(optional)),
        (5062, 6, "utf-8"), (5092, 8, [hashlib.sha256(payload).hexdigest()]), (5093, 4, 8),
        (5097, 8, [hashlib.sha256(archive).hexdigest()]),
    ]
    main = header(tags, 63)
    signature = header([(1000, 4, len(main) + len(payload)),
                        (1004, 7, hashlib.md5(main + payload, usedforsecurity=False).digest()),
                        (269, 6, hashlib.sha1(main, usedforsecurity=False).hexdigest()),
                        (272, 6, hashlib.sha256(main).hexdigest())], 62)
    signature += b"\0" * (-len(signature) % 8)
    lead_name = ("smith-soundboard-" + version(root)).encode()[:65].ljust(66, b"\0")
    lead = struct.pack(">4sBBHH66sHH16s", b"\xed\xab\xee\xdb", 3, 0, 0, 255, lead_name, 1, 5, b"\0" * 16)
    target = destination / f"smith-soundboard-{version(root)}-{release}.noarch.rpm"
    target.write_bytes(lead + signature + main + payload)
    if shutil.which("rpm"):
        subprocess.run(["rpm", "--checksig", str(target)], check=True)
        subprocess.run(["rpm", "-qp", "--queryformat", "%{NAME} %{VERSION} %{ARCH}\n", str(target)], check=True)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--format", choices=("all", "deb", "rpm"), default="all")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.format in ("all", "deb"):
        print(build_deb(args.out))
    if args.format in ("all", "rpm"):
        for family in ("fedora", "suse"):
            print(build_rpm(args.out, family))


if __name__ == "__main__":
    main()
