# Generate Qt Designer .ui into Python.
# Requires: PyQt6 installed

PYDIR := ./env
PYTHON_EXE      := $(PYDIR)/Scripts/python.exe
PYUIC_EXE       := $(PYDIR)/Scripts/pyuic6.exe
PYINSTALLER_EXE := $(PYDIR)/Scripts/pyinstaller.exe
EDIT            := $(PYDIR)/Lib/site-packages/qt6_applications/Qt/bin/designer.exe
PYRCC_EXE       := $(PYDIR)/Lib/site-packages/qt6_applications/Qt/bin/rcc.exe

UI_FILE = main_window_ui.ui
PY_FILE = main_window_ui.py

.PHONY: all ui edit clean

all: ui

ui: $(PY_FILE)

$(PY_FILE): $(UI_FILE)
	echo $(PYUIC_EXE) -o $(PY_FILE) $(UI_FILE)
	$(PYUIC_EXE) -o $(PY_FILE) $(UI_FILE)

edit:
	echo $(EDIT) $(UI_FILE)
	"$(EDIT)" "$(UI_FILE)"

clean:
	-del /q $(PY_FILE) 2>nul || exit 0