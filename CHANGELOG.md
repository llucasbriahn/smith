# Histórico de versões

## 1.1.0 — 2026-11-07

- Instalação por usuário com detecção de Debian/Ubuntu, Fedora/RHEL, openSUSE, Arch e Alpine.
- Dependências nativas e codecs adicionais pelos repositórios já configurados; sem pip global.
- Pacotes DEB all e RPM noarch separados para Fedora e openSUSE, com dependências declaradas.
- Instalador autoextraível, fonte compactado e checksums reproduzíveis.
- Atualização com versões separadas, rollback e remoção que preserva biblioteca e gravações.
- Diagnóstico de dependências com `--check`, JSON e validação da sessão com `--check-audio`.
- Microfones reconhecidos com os diferentes valores de índice inválido do pactl.
- Atualizar dispositivos preserva a seleção e a voz. Reconexão automática a cada três segundos.
- Nova entrada validada antes de remover a anterior; uma falha de troca preserva a captura existente.
- Perfil original por padrão: normalização desligada, volumes de reprodução a 100%, sem reforço de graves/EQ.
- Atualização inicial desmarca o bloqueio da voz durante os efeitos.
- Receitas de construção Flatpak e AppImage. Esses binários ainda precisam ser construídos e testados fora do ambiente restrito.

## 1.0.0 — 2026-11-07

- Biblioteca SQLite sem limite numérico imposto, categorias, favoritos, busca e atalhos.
- Reprodução em fones/microfone/ambos, normalização opcional e mistura com voz.
- Gravação, edição sem alterar o original e exportação WAV/FLAC/MP3.
