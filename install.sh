#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SMITH_PYTHON=${SMITH_PYTHON:-/usr/bin/python3}
if ! "$SMITH_PYTHON" -c 'import sys;raise SystemExit(sys.version_info < (3,10))' 2>/dev/null; then
    if command -v "$SMITH_PYTHON" >/dev/null 2>&1; then
        echo 'O Python disponível é antigo. É necessário Python 3.10+; consulte README.md.' >&2
        exit 1
    fi
    SMITH_YES=no
    SMITH_DRY=no
    for SMITH_ARG do
        case "$SMITH_ARG" in --yes|-y) SMITH_YES=yes;; --dry-run) SMITH_DRY=yes;; esac
    done
    if [ -e /run/ostree-booted ] || [ -e /etc/NIXOS ]; then
        echo 'Sistema imutável sem Python disponível. Consulte a opção Flatpak no README.' >&2
        exit 1
    fi
    echo 'É necessário instalar primeiro o Python 3 da distribuição.'
    if [ "$SMITH_DRY" = yes ]; then
        echo 'Simulação: o gerenciador nativo instalará python3 (python no Arch); depois o plano completo será calculado.'
        exit 0
    fi
    if [ "$SMITH_YES" != yes ]; then
        printf 'Confirmar instalação do Python? [s/N] '
        read -r SMITH_REPLY
        case "$SMITH_REPLY" in s|S|sim|y|yes) :;; *) exit 1;; esac
    fi
    smith_admin() {
        if [ "$(id -u)" -eq 0 ]; then "$@"
        elif command -v sudo >/dev/null 2>&1; then sudo -- "$@"
        elif command -v doas >/dev/null 2>&1; then doas -- "$@"
        else echo 'sudo ou doas é necessário para instalar pacotes.' >&2; return 1; fi
    }
    if command -v apt-get >/dev/null 2>&1; then smith_admin apt-get update; smith_admin apt-get install -y python3
    elif command -v dnf >/dev/null 2>&1; then smith_admin dnf install -y python3
    elif command -v zypper >/dev/null 2>&1; then smith_admin zypper --non-interactive install python3
    elif command -v pacman >/dev/null 2>&1; then smith_admin pacman -Syu --needed --noconfirm python
    elif command -v apk >/dev/null 2>&1; then smith_admin apk add python3
    else echo 'Gerenciador desconhecido. Instale Python 3.10+ manualmente.' >&2; exit 1; fi
    "$SMITH_PYTHON" -c 'import sys;raise SystemExit(sys.version_info < (3,10))' || exit 1
fi
exec "$SMITH_PYTHON" -I "$APP_DIR/packaging/installer.py" "$@"
