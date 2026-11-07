# Construção e publicação

Os artefatos gerados localmente são candidatos para teste. Publicar a versão em um repositório ou loja requer validar a integração no desktop e registrar os ambientes exercitados. Este projeto não está publicado no Flathub ou num repositório de pacotes.

## DEB, RPM e instalador

```sh
sh run.sh --self-test
python3 packaging/build_release.py
cd dist
sha256sum -c SHA256SUMS
```

O build utiliza somente a biblioteca padrão Python. DEB segue o formato ar com `debian-binary`, `control.tar.gz` e `data.tar.gz`. RPM segue v4 com região imutável, payload cpio, hashes de arquivos e payload; `rpm --checksig`, consulta de dependências e extração por `rpm2cpio` fazem parte da verificação quando as ferramentas estão disponíveis. Não há scripts de instalação executando downloads. Dependências opcionais não são tratadas como presentes quando faltam.

O RPM tem variantes Fedora/openSUSE; não converta um para o outro com ferramentas genéricas. `SMITH_MAINTAINER` configura o campo Maintainer do DEB. Defina nome e contato público reais antes de enviar a um repositório que exija esses campos. O projeto usa `org.smith.Soundboard` como ID local; valide o namespace que você controla antes de submeter a uma loja que exija propriedade de domínio.

### Verificações adicionais por mantenedores

Em Debian/Ubuntu com `dpkg-dev` e `lintian`:

```sh
dpkg-deb --info dist/smith-soundboard_1.1.0_all.deb
dpkg-deb --contents dist/smith-soundboard_1.1.0_all.deb
lintian dist/smith-soundboard_1.1.0_all.deb
```

Em sistemas RPM:

```sh
rpm --checksig dist/smith-soundboard-1.1.0-1.fedora.noarch.rpm
rpm -qpR dist/smith-soundboard-1.1.0-1.fedora.noarch.rpm
rpm -qpl dist/smith-soundboard-1.1.0-1.fedora.noarch.rpm
```

Assinaturas devem ser feitas com uma chave do mantenedor, fora do código:

```sh
rpmsign --addsign dist/*.rpm
gpg --armor --detach-sign dist/SHA256SUMS
```

Se assinar os RPMs, seus bytes mudam: recalcule SHA256SUMS/release.json antes de assinar os checksums. Guarde a chave privada fora do repositório. Os binários desta entrega não foram assinados por uma identidade pública.

## Flatpak — receita fornecida, binário pendente

Em um desktop com Flatpak e acesso ao Flathub:

```sh
flatpak install --user flathub org.gnome.Platform//50
python3 packaging/build_flatpak.py
flatpak install --user ./dist/smith-soundboard-1.1.0-x86_64.flatpak
flatpak run org.smith.Soundboard --check
flatpak run org.smith.Soundboard --self-test-routing
```

A receita monta apenas código e dados em cima do GNOME Platform 50 já instalado; não compila bibliotecas e usa a própria plataforma como ambiente de montagem. O runtime contém Python, GTK 3, GI/Cairo, GStreamer e `pactl`. `--arch=aarch64` requer a plataforma correspondente instalada e um host compatível para o exportador.

Permissões: Wayland/X11 para a janela, PulseAudio para entrada/saída e controle da mistura, IPC para X11 e diretório pessoal para referenciar/exportar arquivos escolhidos. Bibliotecas e gravações ficam no diretório de dados do aplicativo Flatpak. Arquivos fora do diretório pessoal precisam de permissão adicional escolhida pelo operador. Não se solicita acesso completo ao barramento D-Bus.

No ambiente de desenvolvimento, o exportador tentou validar o ícone por `bwrap`; a criação de socket NETLINK_ROUTE foi bloqueada pelo isolamento. A construção foi interrompida, preservando as verificações do exportador. Por isso nenhum `.flatpak` está sendo entregue como pronto.

## AppImage — receita fornecida, binário pendente

Use Ubuntu 22.04 x86_64 como máquina/contêiner de construção com rede, apt e uma versão funcional de `appimage-builder` conforme a [documentação oficial](https://appimage-builder.readthedocs.io/en/latest/intro/install.html). As bibliotecas não são copiadas do computador de Kai: a receita baixa pacotes oficiais Ubuntu para a AppDir.

```sh
# Na raiz de um checkout limpo:
appimage-builder --recipe packaging/AppImageBuilder.yml --skip-test
```

O resultado inclui Python, GI/Cairo, GTK, GStreamer/plugins e `pactl`, com variáveis de execução para encontrar as bibliotecas internas. A receita usa Jammy para manter uma base mais antiga que o computador de desenvolvimento. Gere o binário em ambiente limpo: `build/SMITH.AppDir` existente causa interrupção para não misturar builds.

Depois de gerar, execute no mínimo:

```sh
chmod +x ./SMITH*.AppImage
./SMITH*.AppImage --check
./SMITH*.AppImage --self-test
./SMITH*.AppImage --self-test-routing
```

Confira GUI, arquivo em caminho com espaços, MP3/FLAC/Opus, gravação e troca de microfone em pelo menos Debian/Ubuntu, Fedora e openSUSE. Se FUSE não estiver disponível, use o modo de extração e execução suportado pelo runtime AppImage gerado. Não publique como universal antes desses testes. Nenhum AppImage foi construído aqui: as ferramentas não estão instaladas e o ambiente não consegue resolver os hosts necessários para baixá-las. A receita ainda precisa ser exercitada e eventualmente ajustada conforme a versão do builder.

## Sessão gráfica e áudio

Os testes de unidade não substituem o uso real. Em uma máquina de teste, execute:

```sh
python3 packaging/smoke_gui.py
sh run.sh --self-test-routing
```

O primeiro abre a janela real, valida os padrões de qualidade/voz e grava uma captura em `build/gui-smoke.png`. Pode ser usado com `xvfb-run -a dbus-run-session python3 packaging/smoke_gui.py` quando Xvfb e D-Bus estiverem disponíveis. O segundo verifica a transmissão digital pelo servidor de áudio; não depende de microfone físico.

Teste físico: fale enquanto toca um efeito, grave o microfone virtual no destino e confira ambos. Tire e reconecte o microfone; atualize a lista; troque para outra entrada; pare e pause os efeitos. Confirme que o dispositivo físico nunca é silenciado pelo SMITH e que desligar o aplicativo devolve as rotas alteradas. Teste também normalização desligada e arquivos com graves/agudos para conferir o perfil original.

Registre distro/versão, arquitetura, desktop, Wayland/Xorg, servidor de áudio e resultado. Atualize VALIDATION.md com evidências reais. Não marque um ambiente como aprovado só porque sua família está no detector do instalador.

## Conteúdo para publicar

Distribua os cinco artefatos listados em release.json junto de SHA256SUMS, README.md, CHANGELOG.md, LICENSE, VALIDATION.md e logs de testes. Nenhum áudio de terceiros ou dado pessoal acompanha os pacotes. Um repositório público deve oferecer o código correspondente e um canal de issues. A licença de runtimes/bibliotecas incluídos em AppImage deve acompanhar esse formato quando ele for construído; os pacotes nativos dependem das bibliotecas fornecidas pela distribuição.
