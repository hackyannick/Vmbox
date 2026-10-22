# VMBox - build, test and install helpers
DESTDIR ?=
PYTHON  ?= python3
DEMO_DIR ?= /tmp/vmbox-demo

.PHONY: all test lint demo install iso artwork clean

all: test

test:
	$(PYTHON) -m unittest discover -s tests -v

lint:
	$(PYTHON) -m pyflakes src/vmboxd src/bin/vmbox-ctl src/bin/vmbox-shell tools tests | grep -v "'context' imported but unused" || true
	@! $(PYTHON) -m pyflakes src/vmboxd src/bin/vmbox-ctl src/bin/vmbox-shell tools tests | grep -qv "'context' imported but unused"
	shellcheck -S warning src/bin/vmbox-installer src/bin/vmbox-session src/bin/vmbox-xstart build.sh live/auto/* live/config/hooks/normal/*
	node --check src/ui/app.js && node --check src/ui/bar.js && node --check src/ui/icons.js

# Explore the user interface without VirtualBox: http://127.0.0.1:8470
demo:
	cd src && $(PYTHON) -m vmboxd --demo --state-dir $(DEMO_DIR) --ui-dir ui --socket $(DEMO_DIR)/vmboxd.sock

artwork:
	$(PYTHON) tools/make-artwork.py artwork

# Installs VMBox into DESTDIR (used by build.sh to fill the live image).
install:
	install -d $(DESTDIR)/usr/lib/vmbox/vmboxd $(DESTDIR)/usr/share/vmbox/ui $(DESTDIR)/usr/share/vmbox/artwork
	install -m 0644 src/vmboxd/*.py $(DESTDIR)/usr/lib/vmbox/vmboxd/
	install -m 0644 src/ui/* $(DESTDIR)/usr/share/vmbox/ui/
	install -m 0644 artwork/*.png $(DESTDIR)/usr/share/vmbox/artwork/
	install -D -m 0644 src/share/openbox/rc.xml $(DESTDIR)/usr/share/vmbox/openbox/rc.xml
	install -D -m 0644 VERSION $(DESTDIR)/usr/share/vmbox/VERSION
	install -D -m 0755 src/bin/vmbox-ctl $(DESTDIR)/usr/bin/vmbox-ctl
	install -D -m 0755 src/bin/vmbox-shell $(DESTDIR)/usr/bin/vmbox-shell
	install -D -m 0755 src/bin/vmbox-session $(DESTDIR)/usr/lib/vmbox/vmbox-session
	install -D -m 0755 src/bin/vmbox-xstart $(DESTDIR)/usr/lib/vmbox/vmbox-xstart
	install -D -m 0755 src/bin/vmbox-installer $(DESTDIR)/usr/sbin/vmbox-installer
	install -d $(DESTDIR)/etc/systemd/system
	install -m 0644 src/systemd/*.service $(DESTDIR)/etc/systemd/system/
	cd src/etc && find . -type f | while read -r f; do install -D -m 0644 "$$f" "$(DESTDIR)/etc/$$f"; done
	install -D -m 0644 README.md $(DESTDIR)/usr/share/doc/vmbox/README.md

# Builds the ISO (Debian/Ubuntu host with live-build, needs root).
iso:
	sudo ./build.sh

clean:
	rm -rf out live/config/includes.chroot live/config/includes.binary
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
