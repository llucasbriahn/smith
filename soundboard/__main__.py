import os
from pathlib import Path
import sys


def main():
    if "--version" in sys.argv:
        from . import __version__
        print("SMITH Soundboard " + __version__)
        return 0
    if "--self-test" in sys.argv:
        import unittest
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent.parent / "tests"))
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    try:
        if "--check" in sys.argv or "--check-audio" in sys.argv:
            from .checks import check, format_report
            result = check(audio="--check-audio" in sys.argv)
            if "--json" in sys.argv:
                import json
                print(json.dumps(result, ensure_ascii=False))
            else:
                print(format_report(result))
            return 0 if result["ok"] else 1
        if "--diagnose" in sys.argv:
            from .diagnostics import report
            print(report())
            return 0
        if "--self-test-routing" in sys.argv:
            from .diagnostics import test_routing
            print(test_routing())
            return 0
        if "--help" in sys.argv or "-h" in sys.argv:
            print("SMITH Soundboard — interface nativa e áudio no microfone\n\n"
                  "Uso: ./run.sh [arquivos…]\n"
                  "  --import ARQUIVOS…    Adicionar arquivos ou pastas à biblioteca\n"
                  "  --play ID             Reproduzir áudio pelo ID da biblioteca\n"
                  "  --pause               Pausar / continuar\n"
                  "  --stop                Parar reprodução\n"
                  "  --diagnose            Verificar dependências e áudio da sessão\n"
                  "  --check [--json]      Validar bibliotecas e codecs sem abrir áudio\n"
                  "  --check-audio         Validar também a conexão com o áudio da sessão\n"
                  "  --self-test           Rodar testes locais sem hardware de áudio\n"
                  "  --self-test-routing   Testar tom digital pelo microfone virtual\n"
                  "  --version             Mostrar versão\n\n"
                  "Execute como teu usuário de desktop, sem sudo.")
            return 0
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        if not Gtk.init_check()[0]:
            print("Não foi possível acessar o desktop. Execute ./run.sh num terminal da sessão gráfica.\n"
                  "Para diagnóstico sem interface: ./run.sh --diagnose", file=sys.stderr)
            return 1
        from .gui import Application
        return Application().run(sys.argv)
    except (ImportError, ValueError) as exc:
        print("Dependência indisponível: " + str(exc) + "\nConsulte as instruções de instalação em README.md.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
