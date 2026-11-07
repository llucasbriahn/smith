import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import installer
import build_packages
import build_release


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.prefix = self.root / "prefix with spaces $ and 'quote"

    def tearDown(self):
        self.temp.cleanup()

    def test_derivative_distro_detection(self):
        for info, expected in (({"ID": "linuxmint"}, "debian"), ({"ID": "custom", "ID_LIKE": "arch"}, "arch"),
                               ({"ID": "opensuse-tumbleweed"}, "suse"), ({"ID": "rocky"}, "fedora"),
                               ({"ID": "alpine"}, "alpine"), ({"ID": "unknown"}, None)):
            self.assertEqual(installer.family_for(info), expected)

    def test_os_release_is_data_and_never_shell_code(self):
        path = self.root / "os-release"
        marker = self.root / "do-not-create"
        path.write_text('ID="debian"\nNAME="$(touch ' + str(marker) + ')"\n')
        self.assertEqual(installer.os_release(path)["ID"], "debian")
        self.assertFalse(marker.exists())

    def test_package_plan_uses_native_package_managers(self):
        for family, record in installer.FAMILIES.items():
            commands, codecs = installer.package_commands(family)
            self.assertEqual(commands[0][0], record["manager"])
            self.assertTrue(codecs)
            self.assertFalse(any("pip" in value for command in commands for value in command))
        self.assertIn("-Syu", installer.package_commands("arch")[0][0])

    def test_install_launcher_is_relocatable_and_dependencies_check(self):
        launcher = installer.deploy(self.prefix)
        result = subprocess.run([str(launcher), "--version"], text=True, capture_output=True, check=True)
        self.assertIn("1.1.0", result.stdout)
        subprocess.run([str(launcher), "--check"], check=True, stdout=subprocess.DEVNULL)
        desktop = self.prefix / "share/applications/org.smith.Soundboard.desktop"
        if shutil.which("desktop-file-validate"):
            subprocess.run(["desktop-file-validate", str(desktop)], check=True)

    def test_install_does_not_overwrite_foreign_launcher(self):
        launcher = self.prefix / "bin/smith-soundboard"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("unrelated file")
        with self.assertRaisesRegex(RuntimeError, "não pertence"):
            installer.deploy(self.prefix)
        self.assertEqual(launcher.read_text(), "unrelated file")
        self.assertFalse((self.prefix / "lib/smith-soundboard").exists())

    def test_remove_preserves_library_and_modified_launcher(self):
        launcher = installer.deploy(self.prefix)
        library = self.prefix / "share/smith-soundboard/recordings/test.wav"
        library.parent.mkdir(parents=True)
        library.write_bytes(b"my recording")
        launcher.write_text("custom wrapper")
        with contextlib.redirect_stdout(io.StringIO()):
            installer.uninstall(self.prefix)
        self.assertEqual(library.read_bytes(), b"my recording")
        self.assertEqual(launcher.read_text(), "custom wrapper")

    def test_upgrade_and_rollback(self):
        installer.deploy(self.prefix)
        base = self.prefix / "lib/smith-soundboard"
        first = os.readlink(base / "current")
        source = self.root / "new source"
        source.mkdir()
        for path in installer.payload_files():
            target = source / path.relative_to(installer.ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        version_file = source / "soundboard/__init__.py"
        version_file.write_text(version_file.read_text().replace('"1.1.0"', '"1.1.1"'))
        launcher = installer.deploy(self.prefix, source)
        self.assertNotEqual(os.readlink(base / "current"), first)
        shutil.rmtree(source)
        self.assertIn("1.1.1", subprocess.check_output([str(launcher), "--version"], text=True))
        installer.uninstall(self.prefix, rollback=True)
        self.assertEqual(os.readlink(base / "current"), first)
        self.assertIn("1.1.0", subprocess.check_output([str(launcher), "--version"], text=True))

    def test_failed_upgrade_restores_previous_files_and_link(self):
        launcher = installer.deploy(self.prefix)
        before = launcher.read_bytes()
        current = os.readlink(self.prefix / "lib/smith-soundboard/current")
        original = installer.atomic_write
        failed = [False]
        def failing(path, content, mode=0o644):
            if path.suffix == ".desktop" and not failed[0]:
                failed[0] = True
                raise OSError("simulated full disk")
            return original(path, content, mode)
        with patch.object(installer, "atomic_write", side_effect=failing):
            with self.assertRaisesRegex(OSError, "full disk"):
                installer.deploy(self.prefix)
        self.assertEqual(launcher.read_bytes(), before)
        self.assertEqual(os.readlink(self.prefix / "lib/smith-soundboard/current"), current)

    def test_source_archive_is_deterministic_and_contains_no_personal_files(self):
        archive = build_release.source_archive()
        self.assertEqual(archive, build_release.source_archive())
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
            names = source.getnames()
            self.assertTrue(all(name.startswith("smith-soundboard-1.1.0/") for name in names))
            self.assertFalse(any("__pycache__" in name or "/dist/" in name or name.endswith(".sqlite3") for name in names))

    def test_self_extract_verifies_integrity_before_execution(self):
        content = build_release.self_extract(build_release.source_archive())
        target = self.root / "installer.run"
        target.write_bytes(content)
        result = subprocess.run(["sh", str(target), "--dry-run", "--no-deps", "--prefix", str(self.prefix)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.prefix.exists())
        target.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
        result = subprocess.run(["sh", str(target), "--yes", "--no-deps"], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA256", result.stderr)

    def test_deb_contains_valid_control_and_application_payload(self):
        target = build_packages.build_deb(self.root)
        members = {}
        raw, offset = target.read_bytes(), 8
        self.assertEqual(raw[:8], b"!<arch>\n")
        while offset < len(raw):
            header = raw[offset:offset + 60]
            length = int(header[48:58])
            members[header[:16].decode().strip().rstrip("/")] = raw[offset + 60:offset + 60 + length]
            offset += 60 + length + length % 2
        self.assertEqual(members["debian-binary"], b"2.0\n")
        with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"])) as control:
            text = control.extractfile("control").read().decode()
            self.assertIn("python3 (>= 3.10)", text)
            self.assertIn("Architecture: all", text)
        with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"])) as data:
            self.assertEqual(data.extractfile("./usr/share/smith-soundboard/launch.py").read(), (installer.ROOT / "launch.py").read_bytes())
            self.assertEqual(data.getmember("./usr/bin/smith-soundboard").mode, 0o755)

    def test_rpm_digests_dependencies_and_reproducibility(self):
        if not shutil.which("rpm"):
            self.skipTest("rpm não instalado neste ambiente")
        for family in ("suse", "fedora"):
            target = build_packages.build_rpm(self.root, family)
            data = target.read_bytes()
            build_packages.build_rpm(self.root, family)
            self.assertEqual(data, target.read_bytes())
            requirements = subprocess.check_output(["rpm", "-qpR", str(target)], text=True)
            self.assertIn("python3 >= 3.10", requirements)
            self.assertIn(installer.FAMILIES[family]["core"][1], requirements)
