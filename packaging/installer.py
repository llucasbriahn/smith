#!/usr/bin/env python3
"""Distribution-aware dependency installation and reversible per-user deployment."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
FAMILIES = json.loads((ROOT / "packaging/dependencies.json").read_text())
GROUPS = {"debian": {"debian", "ubuntu", "linuxmint", "pop", "neon", "zorin"},
          "fedora": {"fedora", "rhel", "centos", "rocky", "almalinux", "nobara"},
          "suse": {"suse", "opensuse", "opensuse-tumbleweed", "opensuse-leap"},
          "arch": {"arch", "manjaro", "endeavouros", "garuda", "cachyos"}, "alpine": {"alpine"}}


def os_release(path=Path("/etc/os-release")):
    result = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            try:
                parsed = shlex.split(value)
                result[key] = " ".join(parsed)
            except ValueError:
                continue
    return result


def family_for(info):
    for identifier in [info.get("ID", "")] + info.get("ID_LIKE", "").split():
        for family, identifiers in GROUPS.items():
            if identifier in identifiers:
                return family
    return None


def immutable(info):
    return (Path("/run/ostree-booted").exists() or info.get("ID") == "nixos" or
            info.get("VARIANT_ID") in ("silverblue", "kinoite", "sericea", "onyx", "aeon", "kalpa") or
            info.get("ID") in ("opensuse-microos", "aeon", "kalpa"))


def package_commands(family, codecs=True):
    data = FAMILIES[family]
    packages = data["core"]
    if family == "debian":
        commands = [["apt-get", "update"], ["apt-get", "install", "-y", "--no-install-recommends", *packages]]
        optional = [["apt-get", "install", "-y", "--no-install-recommends", package] for package in data["codecs"]]
    elif family == "fedora":
        commands = [["dnf", "install", "-y", *packages]]
        optional = [["dnf", "install", "-y", package] for package in data["codecs"]]
    elif family == "suse":
        commands = [["zypper", "--non-interactive", "install", "--no-recommends", *packages]]
        optional = [["zypper", "--non-interactive", "install", "--no-recommends", package] for package in data["codecs"]]
    elif family == "arch":
        # Arch does not support partial upgrades. Show the complete command first.
        commands = [["pacman", "-Syu", "--needed", "--noconfirm", *packages]]
        optional = [["pacman", "-S", "--needed", "--noconfirm", package] for package in data["codecs"]]
    else:
        commands = [["apk", "add", *packages]]
        optional = [["apk", "add", package] for package in data["codecs"]]
    return commands, optional if codecs else []


def dependency_check(root=ROOT):
    result = subprocess.run([sys.executable, "-I", str(root / "launch.py"), "--check", "--json"], text=True, capture_output=True)
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {"ok": False, "missing": [result.stderr.strip() or result.stdout.strip()], "codecs": {}}


def install_dependencies(family, codecs=True):
    commands, optional = package_commands(family, codecs)
    manager = FAMILIES[family]["manager"]
    if not shutil.which(manager):
        raise RuntimeError("Gerenciador não encontrado: " + manager)
    elevation = []
    if os.geteuid() != 0:
        if shutil.which("sudo"):
            elevation = ["sudo", "--"]
        elif shutil.which("doas"):
            elevation = ["doas", "--"]
        else:
            raise RuntimeError("Instale as dependências com o administrador ou disponibilize sudo/doas. Consulte --dry-run.")
    for command in commands:
        subprocess.run(elevation + command, check=True)
    unavailable = []
    for command in optional:
        if subprocess.run(elevation + command, check=False).returncode:
            unavailable.append(command[-1])
    if unavailable:
        print("Codecs adicionais indisponíveis nos repositórios atuais: " + ", ".join(unavailable))
        print("Nenhum repositório de terceiros foi ativado. Confira os formatos com --check.")


def version(root=ROOT):
    for node in ast.parse((root / "soundboard/__init__.py").read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("Versão não encontrada.")


def payload_files(root=ROOT):
    for directory in ("soundboard", "tests", "packaging", "data"):
        for path in sorted((root / directory).rglob("*")):
            if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                yield path
    for name in ("launch.py", "run.sh", "install.sh", "install.py", "Makefile", "README.md", "LICENSE", "CHANGELOG.md", "VALIDATION.md"):
        path = root / name
        if path.is_file():
            yield path


def desktop_quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%") + '"'


def atomic_write(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".smith-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            os.fchmod(output.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def point_link(path, target):
    temporary = path.with_name(path.name + ".new")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    temporary.replace(path)


def deploy(prefix, root=ROOT):
    prefix = Path(prefix).expanduser().absolute()
    base = prefix / "lib/smith-soundboard"
    state_path = base / "installation.json"
    if base.is_symlink() or (base.exists() and not state_path.exists()):
        raise RuntimeError("Diretório de instalação não gerenciado pelo SMITH: " + str(base))
    previous = json.loads(state_path.read_text()) if state_path.exists() else {}
    if previous and previous.get("format") != "smith-install-v1":
        raise RuntimeError("Registro de instalação incompatível.")
    files = list(payload_files(root))
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    release = version(root) + "-" + digest.hexdigest()[:12]
    release_path = base / "releases" / release
    launcher = prefix / "bin/smith-soundboard"
    desktop = prefix / "share/applications/org.smith.Soundboard.desktop"
    icon = prefix / "share/icons/hicolor/scalable/apps/org.smith.Soundboard.svg"
    wrapper = ("#!/bin/sh\n# Managed by SMITH Soundboard\nexec " + shlex.quote(sys.executable) + " -I " +
               shlex.quote(str(base / "current/launch.py")) + ' "$@"\n').encode()
    desktop_data = (root / "data/org.smith.Soundboard.desktop").read_text().replace("Exec=smith-soundboard %F", "Exec=" + desktop_quote(launcher) + " %F").encode()
    contents = [(launcher, wrapper, 0o755), (desktop, desktop_data, 0o644),
                (icon, (root / "data/org.smith.Soundboard.svg").read_bytes(), 0o644)]
    snapshots = {}
    for path, content, mode in contents:
        if path.is_symlink():
            raise RuntimeError("Arquivo de integração é um link não gerenciado: " + str(path))
        if path.exists() and str(path) not in previous.get("files", {}):
            # The old 1.0 launcher had no install manifest.
            if path != desktop or "Name=SMITH Soundboard" not in path.read_text():
                raise RuntimeError("Arquivo já existe e não pertence a esta instalação: " + str(path))
        snapshots[path] = (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
    current = base / "current"
    old_target = os.readlink(current) if current.is_symlink() else None
    if current.exists() and not current.is_symlink():
        raise RuntimeError("O caminho current não é um link gerenciado.")
    try:
        release_path.parent.mkdir(parents=True, exist_ok=True)
        if not release_path.exists():
            staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=release_path.parent))
            try:
                for path in files:
                    destination = staging / path.relative_to(root)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
                    destination.chmod(0o755 if path.suffix == ".sh" else 0o644)
                staging.replace(release_path)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        point_link(current, "releases/" + release)
        for path, content, mode in contents:
            atomic_write(path, content, mode)
        state = {"format": "smith-install-v1", "version": version(root), "current": release,
                 "previous": previous.get("current") if previous.get("current") != release else previous.get("previous"),
                 "files": {str(path): hashlib.sha256(content).hexdigest() for path, content, mode in contents}}
        atomic_write(state_path, json.dumps(state, indent=2).encode())
    except Exception:
        if old_target:
            point_link(current, old_target)
        else:
            current.unlink(missing_ok=True)
        for path, saved in snapshots.items():
            if saved:
                atomic_write(path, *saved)
            else:
                path.unlink(missing_ok=True)
        if not previous and base.exists():
            shutil.rmtree(base)
        raise
    return launcher


def uninstall(prefix, rollback=False):
    base = Path(prefix).expanduser().absolute() / "lib/smith-soundboard"
    state_path = base / "installation.json"
    if base.is_symlink() or not state_path.is_file():
        raise RuntimeError("Instalação gerenciada não encontrada: " + str(base))
    state = json.loads(state_path.read_text())
    if state.get("format") != "smith-install-v1":
        raise RuntimeError("Registro de instalação incompatível.")
    if rollback:
        previous = state.get("previous")
        if not previous or Path(previous).name != previous or not (base / "releases" / previous).is_dir():
            raise RuntimeError("Nenhuma versão anterior disponível.")
        point_link(base / "current", "releases/" + previous)
        state["current"], state["previous"] = previous, state["current"]
        atomic_write(state_path, json.dumps(state, indent=2).encode())
        return
    prefix = Path(prefix).expanduser().absolute()
    for filename, digest in state["files"].items():
        path = Path(filename)
        if not path.is_relative_to(prefix) or path.is_symlink():
            raise RuntimeError("Caminho de integração inválido: " + filename)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            path.unlink()
        elif path.exists():
            print("Preservado porque foi modificado:", path)
    shutil.rmtree(base)
    print("Aplicativo removido. Biblioteca, gravações e dependências foram preservadas.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Instalador SMITH Soundboard. Execute sem sudo; só dependências usam elevação.")
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    parser.add_argument("--yes", "-y", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-deps", action="store_true")
    parser.add_argument("--no-codecs", action="store_true")
    parser.add_argument("--deps-only", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--family", choices=sorted(FAMILIES), help="Forçar família de pacotes (avançado)")
    args = parser.parse_args(argv)
    info = os_release()
    family = args.family or family_for(info)
    removing = args.uninstall or args.rollback
    needs_deps = not removing and not args.no_deps
    print("SMITH Soundboard", version(), "·", info.get("PRETTY_NAME", "Linux"))
    print("Destino:", args.prefix)
    if needs_deps:
        if immutable(info) and not args.family:
            raise RuntimeError("Sistema imutável detectado. Consulte a receita Flatpak no README ou prepare as dependências e execute --no-deps.")
        if not family:
            raise RuntimeError("Família de distribuição desconhecida. Use Flatpak ou prepare as dependências e execute --no-deps.")
        commands, optional = package_commands(family, not args.no_codecs)
        print("Dependências essenciais:")
        for command in commands:
            print(" ", shlex.join(command))
        print("Codecs adicionais (tentativa nos repositórios já configurados):")
        for command in optional:
            print(" ", shlex.join(command))
    if args.dry_run:
        return 0
    if os.geteuid() == 0 and not args.deps_only:
        raise RuntimeError("Execute como teu usuário, sem sudo. O instalador pedirá elevação somente para os pacotes.")
    if not args.yes:
        if not sys.stdin.isatty() or input("Confirmar esta operação? [s/N] ").strip().lower() not in ("s", "sim", "y", "yes"):
            print("Cancelado. Use --yes para uma execução automatizada.")
            return 1
    if removing:
        uninstall(args.prefix, rollback=args.rollback)
        return 0
    if needs_deps:
        install_dependencies(family, codecs=not args.no_codecs)
    result = dependency_check()
    if not result["ok"]:
        raise RuntimeError("Dependências ainda ausentes:\n" + "\n".join(result["missing"]))
    missing_codecs = [name for name, ok in result["codecs"].items() if not ok]
    if missing_codecs:
        print("Formatos indisponíveis:", ", ".join(missing_codecs))
    if args.deps_only:
        print("Dependências verificadas.")
        return 0
    launcher = deploy(args.prefix)
    for command, path in (("update-desktop-database", args.prefix / "share/applications"),
                          ("gtk-update-icon-cache", args.prefix / "share/icons/hicolor")):
        if shutil.which(command):
            subprocess.run([command, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    print("Instalado:", launcher)
    print("Abra SMITH Soundboard no menu ou execute:", launcher)
    print("Valide o áudio na sessão gráfica:", launcher, "--check-audio")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print("Instalação interrompida:", exc, file=sys.stderr)
        raise SystemExit(1)
