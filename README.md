# SMITH Soundboard — Linux

Soundboard nativo em português para reproduzir arquivos de áudio pelo microfone enquanto você fala. Interface GTK 3, biblioteca SQLite sem limite numérico imposto, atalhos, gravação e cortes sem modificar o original. Implementação independente inspirada no fluxo do Soundpad, sob licença MIT.

**Versão 1.1.0 — candidata para testes da comunidade.** Os pacotes e testes automatizados estão disponíveis. A interface no desktop, a captura física e a instalação em cada distribuição ainda precisam de validação em máquinas reais. Não há promessa de compatibilidade com toda versão de toda distribuição. Veja [VALIDATION.md](VALIDATION.md).

## Escolha o formato

| Formato | Destino | Dependências |
| --- | --- | --- |
| `smith-soundboard_1.1.0_all.deb` | Debian 12+, Ubuntu 22.04+ e derivados compatíveis | APT resolve os pacotes declarados |
| `smith-soundboard-1.1.0-1.fedora.noarch.rpm` | Fedora e derivados com Python 3.10+ | DNF resolve as dependências Fedora |
| `smith-soundboard-1.1.0-1.suse.noarch.rpm` | openSUSE com Python 3.10+ | Zypper resolve as dependências openSUSE |
| `smith-soundboard-1.1.0-install.run` | Debian/Ubuntu, Fedora, openSUSE, Arch e Alpine | Detecta a família, instala dependências e copia o programa para a conta |
| `smith-soundboard-1.1.0-source.tar.gz` | Desenvolvimento e instalação a partir dos fontes | Mesmo instalador da versão `.run` |
| Receitas Flatpak/AppImage | Construção por mantenedores | Incluídas nos fontes; binários desses formatos ainda não foram validados nem publicados |

O código Python dos pacotes DEB/RPM é independente de arquitetura (`all`/`noarch`); as bibliotecas nativas são fornecidas pela distribuição. A disponibilidade delas determina as arquiteturas utilizáveis. As distribuições listadas têm planos de instalação implementados, o que não equivale a testes completos em todas elas.

Os pacotes usam as bibliotecas GTK/GStreamer da distribuição. Não instalam bibliotecas Python com `pip`, não ativam repositórios externos e não trocam o servidor de áudio existente. É necessário um desktop Linux com PulseAudio ou PipeWire **com `pipewire-pulse`**, além de Python 3.10 ou superior.

## Instalar DEB

Na pasta em que o arquivo foi baixado:

```sh
sudo apt install ./smith-soundboard_1.1.0_all.deb
smith-soundboard --check
```

O prefixo `./` é necessário para o APT tratar o argumento como arquivo local. Use APT para resolver as dependências. Os repositórios oficiais necessários, incluindo os componentes de multimídia da distro, devem estar habilitados.

## Instalar RPM

Fedora:

```sh
sudo dnf install ./smith-soundboard-1.1.0-1.fedora.noarch.rpm
smith-soundboard --check
```

openSUSE:

```sh
sudo zypper install ./smith-soundboard-1.1.0-1.suse.noarch.rpm
smith-soundboard --check
```

Os RPMs de Fedora e openSUSE são separados porque os nomes das dependências diferem. Os artefatos locais possuem hashes e integridade verificada; ainda não levam uma assinatura GPG de mantenedor. O gerenciador pode pedir confirmação sobre essa ausência. Para um repositório público, configure a assinatura conforme [o guia de publicação](packaging/RELEASING.md).

## Instalador portátil `.run`

```sh
sh smith-soundboard-1.1.0-install.run --dry-run
sh smith-soundboard-1.1.0-install.run
```

Execute como seu usuário de desktop. O instalador mostra os comandos e pede confirmação; `sudo` ou `doas` é usado somente para instalar pacotes do sistema. Depois, o aplicativo é copiado para `~/.local/lib/smith-soundboard`, e os atalhos são criados em `~/.local/bin` e `~/.local/share`. A pasta extraída e o `.run` podem ser removidos após a instalação.

O instalador confere o SHA256 do conteúdo antes de extraí-lo. Se Python estiver ausente, oferece instalar o Python da distribuição. Um Python existente anterior a 3.10 não é substituído automaticamente. Código e bibliotecas GI devem usar o mesmo Python da distribuição.

Opções:

```sh
sh smith-soundboard-1.1.0-install.run --yes                 # confirmação automatizada
sh smith-soundboard-1.1.0-install.run --no-deps              # dependências já presentes; ainda faz a verificação
sh smith-soundboard-1.1.0-install.run --deps-only            # instalar e validar apenas dependências
sh smith-soundboard-1.1.0-install.run --no-codecs            # não tentar instalar codecs adicionais
sh smith-soundboard-1.1.0-install.run --prefix "$HOME/apps/smith" # outro prefixo
```

No Arch, a transação principal usa `pacman -Syu` para evitar uma atualização parcial. Revise o plano antes de confirmar. Codecs adicionais são tentados separadamente: a ausência de um pacote opcional não transforma uma instalação básica em sucesso fictício; os formatos ausentes são mostrados por `--check`. Se faltar uma dependência essencial, a instalação para antes de ativar o aplicativo.

Sistemas imutáveis, NixOS e famílias desconhecidas não recebem alterações automáticas de pacotes. Consulte a receita Flatpak ou prepare as bibliotecas pelo método da distribuição e use `--no-deps`.

## Instalar a partir dos fontes

```sh
tar -xzf smith-soundboard-1.1.0-source.tar.gz
cd smith-soundboard-1.1.0
sh install.sh --dry-run
sh install.sh
```

Para executar sem instalar, com as dependências já presentes:

```sh
sh run.sh --check
sh run.sh
```

`make install` chama o mesmo instalador. `make` verifica sintaxe; não é necessário compilar o aplicativo Python.

## Abrir e configurar voz + áudios

Abra **SMITH Soundboard** no menu de aplicativos, ou execute:

```sh
smith-soundboard
# Se ~/.local/bin não estiver no PATH:
"$HOME/.local/bin/smith-soundboard"
```

1. Clique em **＋**, selecione arquivos ou adicione uma pasta. Também pode arrastar arquivos e pastas para a lista.
2. Expanda **Áudio e microfone virtual**.
3. Em **Tua voz**, mantenha **Automático · microfone padrão do sistema** ou escolha explicitamente o microfone USB, headset ou entrada desejada.
4. Escolha os fones em **Fones / saída** e clique em **Ativar microfone virtual**.
5. No Discord, Steam, jogo ou outro aplicativo, selecione **SMITH_Soundboard_Microphone** como entrada.
6. Escolha **Fones + microfone** ou **Somente microfone** e dê dois cliques em um som. Sua voz continua entrando simultaneamente.

O dispositivo físico continua disponível no sistema; o aplicativo não altera seu mute ou volume. A voz é copiada para a mistura virtual. Os volumes **Tua voz**, **Áudio no microfone** e **Áudio nos fones** são independentes. **Somente fones** serve para conferir um áudio antes de transmiti-lo.

**Sem voz · somente os áudios** é uma escolha explícita. As opções **Silenciar minha voz** e **Silenciar minha voz enquanto um som toca** ficam desligadas inicialmente. Na primeira execução da atualização 1.1, o bloqueio durante efeitos e a normalização são desativados, e os volumes de reprodução voltam a 100%; alterações posteriores são salvas normalmente. Ganhos individuais dos arquivos permanecem como foram configurados.

A lista de dispositivos é consultada em segundo plano a cada três segundos. Reconectar o microfone restaura a voz; se uma entrada preferida sumir e outra estiver disponível, a seleção automática pode usar a alternativa. Uma tentativa inválida de trocar a entrada preserva a captura que já estava funcionando. Atualizar a lista não limpa a preferência.

**Usar como entrada padrão** afeta novas capturas. Para um aplicativo que já esteja capturando, abra o teste de microfone nele e use **Enviar para um aplicativo…**. Ao desligar o SMITH, as rotas alteradas por ele são restauradas se ainda apontarem para a entrada virtual. O programa precisa permanecer aberto; pode ficar minimizado.

## Qualidade original

O padrão é **sem equalizador, reforço de graves, ganho automático ou normalização**. Ganho por arquivo começa em 0 dB; reprodução nos fones e efeitos no microfone começam em 100%. O programa não aumenta graves nem adiciona reverberação. Graves presentes na gravação original são preservados.

- MP3 e outros formatos comprimidos são decodificados diretamente para reprodução, sem uma recodificação intermediária.
- A mistura interna usa float32 estéreo a 48 kHz e reamostragem de qualidade 10. Fontes em outras taxas são reamostradas para a mistura; isso não é uma promessa de reprodução bit a bit em qualquer hardware.
- **Normalizar volume** é opcional e usa ReplayGain; altera o ganho percebido. Deixe desmarcado para conservar a dinâmica original.
- Um limitador contém picos que ultrapassem a faixa ao somar voz e efeitos. Se o medidor ficar sempre no máximo, reduza o volume dos efeitos para reservar margem para a voz.
- No editor, deixe **Ganho = 0 dB** para cada arquivo que precise de amplitude original.
- Filtros do destino continuam podendo alterar o áudio. Para transmitir música/efeitos sem cortes, revise supressão de ruído, cancelamento de eco, ganho automático e ativação por voz no Discord/jogo. Use fones para evitar retorno acústico.

Há testes que verificam preservação das amostras PCM, incluindo componentes graves e agudos, e a presença dos dois sinais na mistura. Um programa não recupera detalhes perdidos em um MP3 nem impede o processamento posterior do serviço de voz.

## Biblioteca, cortes, gravação e atalhos

A biblioteca não impõe limite numérico de sons; capacidade depende de armazenamento e recursos. SQLite mantém os registros em disco. A interface carrega páginas de 500 registros e oferece **Carregar mais**. Há busca sem distinção de maiúsculas/acentos, categorias, favoritos e ordenação.

Formatos comuns: MP3, WAV, FLAC, OGG/Vorbis, Opus, AAC/M4A e WMA. O suporte efetivo depende dos plugins presentes; consulte `--check`. Arquivos protegidos por DRM não são decodificados. Os arquivos são referenciados pelos caminhos originais e precisam continuar acessíveis.

Clique direito em um áudio para editar nome, categoria, atalho, ganho e intervalo. Na forma de onda, clique esquerdo marca início e direito marca fim. **Ouvir corte** utiliza o modo de saída selecionado. As edições preservam o arquivo original. **Biblioteca → Exportar áudio** permite WAV PCM 24 bits/48 kHz, FLAC 24 bits/48 kHz ou MP3 320 kbit/s. WAV/FLAC evitam outra compressão com perda.

**Arquivo → Gravar áudio** captura uma entrada física ou o som de uma saída para WAV 24 bits/48 kHz. Ao finalizar, adiciona a gravação à biblioteca. A duração depende do espaço em disco.

**Arquivo → Exportar lista** salva caminhos e metadados em JSON; os áudios não são incluídos. Para migrar entre computadores, copie os áudios e ajuste os caminhos na lista. **Remover da biblioteca** não apaga o arquivo; **Desfazer remoção** restaura a última remoção durante a sessão.

Atalhos por áudio: por exemplo, `F8` ou `<Control><Alt>1`. Na janela, **Enter** reproduz, **Espaço** pausa/continua e **Escape** para. No Wayland, atalhos globais dependem do portal GlobalShortcuts do desktop e da autorização apresentada por ele. No Xorg há alternativa por XGrabKey. O estado aparece no painel de áudio.

Se o desktop não oferecer esse portal, configure atalhos do próprio desktop para:

```sh
smith-soundboard --play 12
smith-soundboard --stop
smith-soundboard --pause
smith-soundboard --import /caminho/para/sons
```

O ID aparece na coluna **#**. Com a janela já aberta, esses comandos de controle não a trazem para a frente. Não é usado acesso a `/dev/input` nem execução do aplicativo como root.

## Diagnóstico e solução de problemas

```sh
smith-soundboard --check              # bibliotecas e formatos; falha se faltar algo essencial
smith-soundboard --check --json       # resultado estruturado
smith-soundboard --check-audio        # inclui conexão com servidor de áudio
smith-soundboard --diagnose           # informações da sessão e dispositivos
smith-soundboard --self-test          # testes sem hardware de áudio
smith-soundboard --self-test-routing  # teste real da cadeia virtual com um tom
```

| Sintoma | Verificação |
| --- | --- |
| Minha voz não aparece | Escolha Automático ou o microfone físico; desmarque os dois controles de silêncio; verifique o mute nas configurações de som do sistema |
| O destino recebe só a voz, sem efeitos | Selecione SMITH_Soundboard_Microphone como entrada no destino e escolha um modo que inclua microfone no SMITH |
| Microfone não aparece na lista | Atualize dispositivos, reconecte-o e confira se o sistema reconhece uma entrada; confira também o perfil do headset nas configurações de som |
| Mudo após reiniciar PipeWire/PulseAudio | Desative e ative o microfone virtual para reconstruir os módulos temporários |
| Música picota ou perde detalhes | Revise os filtros de voz do destino e reserve margem de volume para a mistura |
| `No module named gi` ou erro Cairo | Execute novamente o instalador de dependências, usando o Python do sistema; não misture um Python de venv/Conda com GI de outro Python |
| Plugin/codec ausente | Use `--check`; instale os plugins da distribuição para esse formato, ou exporte a origem como WAV/FLAC |
| Connection refused / Permission denied | Execute como usuário da sessão gráfica, sem sudo; confirme o servidor com `pactl info` |
| Atalho global indisponível | Confira o portal do desktop ou configure o comando CLI nas configurações de atalhos do sistema |
| Aplicativo não aparece no menu | Saia e entre na sessão, ou execute o caminho completo do launcher; confira `~/.local/share/applications` |

O teste de roteamento cria somente os seus dispositivos temporários e um tom digital; não captura o microfone físico nem altera a entrada padrão. Execute com o SMITH fechado para evitar colisão com o nome do microfone virtual.

O programa registra os módulos de sua sessão e descarrega somente aqueles que reconhece como próprios. Não modifica permanentemente a configuração de PipeWire/PulseAudio. O registro permite recuperar uma sessão anterior quando o microfone virtual é reativado após uma falha.

## Dependências e instalação manual

A lista utilizada por instalador e empacotador está em [packaging/dependencies.json](packaging/dependencies.json). São necessários Python 3.10+, PyGObject, PyCairo e integração Cairo/GI, GTK/GDK 3, GStreamer 1 e `pactl`. Os plugins essenciais incluem playback, audioconvert, audioresample, pulseaudio, replaygain, level, app e WAV. Codecs adicionais são separados.

Para visualizar os comandos exatos sem instalar:

```sh
sh install.sh --dry-run
sh install.sh --dry-run --family debian
sh install.sh --dry-run --family fedora
sh install.sh --dry-run --family suse
sh install.sh --dry-run --family arch
sh install.sh --dry-run --family alpine
```

`--family` é uma opção avançada para revisar planos ou derivados não reconhecidos; não torna pacotes de outra distribuição compatíveis. Internet e repositórios válidos são necessários para baixar dependências que ainda não estejam no cache.

## Atualização, rollback e remoção

DEB/RPM: instale a versão nova com o mesmo comando do gerenciador. Para remover:

```sh
sudo apt remove smith-soundboard       # Debian/Ubuntu
sudo dnf remove smith-soundboard       # Fedora
sudo zypper remove smith-soundboard    # openSUSE
```

Instalador por usuário: execute o novo `.run` para atualizar. O link `current` muda somente depois de copiar a nova versão; uma falha na integração restaura os arquivos e o link anteriores. As versões anteriores são mantidas.

```sh
sh smith-soundboard-1.1.0-install.run --rollback
sh smith-soundboard-1.1.0-install.run --uninstall
```

Para prefixo personalizado, repita `--prefix`. Também pode executar `python3 ~/.local/lib/smith-soundboard/current/packaging/installer.py --uninstall` enquanto estiver instalado. O desinstalador preserva arquivos de integração modificados e não remove dependências compartilhadas, áudios, gravações ou a biblioteca.

Evite manter simultaneamente uma instalação por usuário e outra do gerenciador: o atalho da conta pode ter prioridade sobre o atalho do sistema.

## Dados e backup

- Biblioteca/preferências: `$XDG_DATA_HOME/smith-soundboard/library.sqlite3`, normalmente `~/.local/share/smith-soundboard/library.sqlite3`.
- Gravações: subpasta `recordings` do mesmo diretório.
- Estado temporário do áudio: `$XDG_RUNTIME_DIR/smith-soundboard`, ou diretório de dados quando XDG_RUNTIME_DIR não está definido.
- Instalação por usuário: `~/.local/lib/smith-soundboard/releases`, com link `current` e manifesto de instalação.
- Pacotes do sistema: código em `/usr/share/smith-soundboard`, launcher em `/usr/bin/smith-soundboard` e documentação em `/usr/share/doc/smith-soundboard`.

Feche o aplicativo antes de copiar o diretório de dados para backup. Copie também os áudios externos referenciados pela biblioteca. Os pacotes de distribuição contêm somente código e documentação, sem banco de dados, gravações pessoais ou configurações da máquina de desenvolvimento.

## Construir e distribuir

```sh
python3 packaging/build_release.py
# Apenas DEB/RPM:
python3 packaging/build_packages.py
# Todos os testes:
sh run.sh --self-test
```

A saída fica em `dist/`: fonte, `.run`, `.deb`, dois `.rpm`, `release.json` e `SHA256SUMS`. `SOURCE_DATE_EPOCH` fixa o horário da construção; os arquivos são ordenados e os donos são normalizados para permitir builds reproduzíveis. O empacotador usa a biblioteca padrão do Python e valida os RPMs com a ferramenta `rpm` quando ela está presente.

```sh
cd dist
sha256sum -c SHA256SUMS
```

Os checksums detectam corrupção; autenticação do publicador requer assinatura ou um canal de distribuição confiável. Para os campos de mantenedor Debian, defina `SMITH_MAINTAINER='Nome <email-publico>'` ao gerar sua versão. Nenhum email ou repositório público fictício é incluído.

Consulte [packaging/RELEASING.md](packaging/RELEASING.md) para receitas Flatpak/AppImage, teste em desktop e publicação. A receita AppImage reúne Python, GTK, GI, GStreamer e `pactl`; não depende apenas de ter o Python instalado no destino. Ainda não há AppImage validado nesta entrega. A receita Flatpak usa GNOME Platform 50, que contém a pilha de execução; seu binário também precisa ser gerado fora do ambiente restrito.

## Licença, escopo e referências

Código próprio sob [MIT](LICENSE). O projeto não possui vínculo com a Leppsoft. [Histórico de alterações](CHANGELOG.md). Contribuições devem incluir distro/versão, desktop, Wayland/Xorg, resultado de `--check` e passos para reproduzir o problema.

Referências: [manual oficial do Soundpad](https://www.leppsoft.com/soundpad/en/help/manual/), módulos PipeWire [null sink](https://docs.pipewire.org/page_pulse_module_null_sink.html), [remap source](https://docs.pipewire.org/page_pulse_module_remap_source.html) e [loopback](https://docs.pipewire.org/page_pulse_module_loopback.html), [GStreamer ReplayGain](https://gstreamer.freedesktop.org/documentation/replaygain/rganalysis.html), [GlobalShortcuts](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html), [pacotes Debian GI/Cairo](https://packages.debian.org/trixie/python3-gi-cairo), [RPM v4](https://rpm-software-management.github.io/rpm/manual/format_v4.html) e [AppImageBuilder](https://appimage-builder.readthedocs.io/en/latest/reference/recipe.html).
