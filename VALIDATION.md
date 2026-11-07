# Validação da versão 1.1.0

Data: 2026-11-07. Ambiente: openSUSE Tumbleweed x86_64, Python 3.13, GTK 3 e GStreamer 1.28.8, executados dentro de um ambiente restrito.

## Verificado localmente

- Suíte automatizada: 55 testes. Resultado completo em TEST-RESULTS.txt no diretório de distribuição.
- Biblioteca com 12 mil registros, busca, persistência, paginação, duplicatas, favoritos, categorias, restauração e exportação/importação de listas.
- Decodificação real WAV/MP3/FLAC/OGG/Opus/AAC-M4A/WMA, análise ReplayGain e forma de onda.
- Reprodução em saídas de teste, pausa/continuação, término no intervalo correto e corte de MP3.
- Exportação real WAV/FLAC/MP3, preservação das amostras PCM com graves e agudos e perfil original sem normalização automática.
- Dois sinais presentes simultaneamente na mistura digital; o teste calcula suas amplitudes sem confundir deslocamento de fase com perda de volume.
- Classificação de microfones com índices inválidos numéricos ou `n/a`, seleção automática, falha de troca preservando a entrada atual e reconexão de dispositivo com índice novo.
- Rotas de captura/default restauradas sem alterar mute/volume do microfone físico, usando um servidor Pulse simulado para cobrir casos de falha.
- Instalação real em prefixo temporário com espaços e caracteres especiais, launcher executável, validação do desktop entry, atualização, rollback e remoção preservando dados.
- Recusa a sobrescrever launcher externo; recuperação da instalação anterior após falha simulada de escrita.
- Instalador autoextraível com validação SHA256 e recusa de conteúdo corrompido.
- Planos de pacotes das cinco famílias, parsing de os-release sem executar shell e checagem das dependências locais.
- DEB: estrutura ar/control/data, permissões e conteúdo conferidos por extração.
- RPM Fedora/openSUSE: `rpm --checksig` confirma digests; `rpm -qpR`/`rpm -qpl` conferem metadados; payload é extraível por `rpm2cpio`/`cpio`.
- Builds nativos e fonte reproduzíveis com o mesmo conteúdo e SOURCE_DATE_EPOCH.

## Pendente de máquina/sessão real

- Janela GTK aberta e interação visual. Importação do código e parsing do CSS passaram, mas não equivalem a abrir a janela.
- Captura de microfone físico, desconexão USB/Bluetooth e voz + efeitos recebidos por Discord/jogos.
- Cadeia virtual real pelo servidor PulseAudio/PipeWire. O ambiente bloqueia o acesso aos sockets da sessão com `Operation not permitted`.
- Instalação de dependências via APT/DNF/Zypper/Pacman/APK em sistemas limpos. Os comandos e metadados foram exercitados localmente; transações em outras distros ainda não foram executadas.
- Análise com lintian/rpmlint e assinatura por um mantenedor público.
- Binário AppImage: faltam as ferramentas externas de construção, e downloads estão indisponíveis por falha de resolução de host no ambiente restrito.
- Binário Flatpak: `build-init` e `build-finish` funcionaram, mas `build-export` foi interrompido pelo bloqueio do socket NETLINK_ROUTE durante validação do ícone por bwrap. A validação não foi desativada.

## Reproduzir

```sh
sh run.sh --self-test
sh run.sh --check
python3 packaging/build_release.py
python3 packaging/smoke_gui.py      # requer desktop ou Xvfb
sh run.sh --self-test-routing      # requer servidor de áudio; aplicativo fechado
```

As limitações acima acompanham a versão para que distribuidores não confundam testes de mídia/empacotamento com certificação de todas as combinações de hardware e desktop.
