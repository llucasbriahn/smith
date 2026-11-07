.PHONY: all run test diagnose install packages release
all:
	@/usr/bin/python3 -m compileall -q soundboard
	@echo 'Pronto. Execute ./run.sh'
run:
	./run.sh
test:
	./run.sh --self-test
diagnose:
	./run.sh --diagnose
install:
	sh install.sh
packages:
	/usr/bin/python3 packaging/build_packages.py
release:
	/usr/bin/python3 packaging/build_release.py
