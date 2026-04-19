.PHONY: setup dev install lint

PYTHON := python3
VENV   := .venv
PIP    := $(VENV)/bin/pip
RUN    := $(VENV)/bin/python

# Create venv and install dependencies
setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r services/receptionist/requirements.txt
	@echo ""
	@echo "Dependencies installed. Downloading Piper TTS models..."
	$(RUN) -c "\
from pathlib import Path; \
from piper.download_voices import download_voice; \
d = Path('services/receptionist/models/piper'); d.mkdir(parents=True, exist_ok=True); \
download_voice('de_DE-thorsten-high', d); \
download_voice('en_US-ryan-high', d)" || \
	echo "Piper model download failed — models will download automatically on first run."
	@echo ""
	@echo "Setup complete. Copy .env.example to .env and fill in your API keys:"
	@echo "  cp .env.example .env"

# Run the agent — opens a browser-based WebRTC session, no external account needed
dev:
	@echo ""
	@echo "  Starting agent on http://localhost:7860"
	@echo "  Open http://localhost:7860 in your browser and click Start Call."
	@echo ""
	$(RUN) -m services.receptionist.main

# Install only (no model download)
install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r services/receptionist/requirements.txt
