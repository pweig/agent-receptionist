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
import os; os.makedirs('services/receptionist/models/piper', exist_ok=True); \
from piper.download import find_voice, get_voices; \
get_voices('services/receptionist/models/piper'); \
find_voice('de_DE-thorsten-high', 'services/receptionist/models/piper'); \
find_voice('en_US-ryan-high', 'services/receptionist/models/piper')" || \
	echo "Piper model download failed — models will download on first run."
	@echo ""
	@echo "Setup complete. Copy .env.example to .env and fill in your API keys:"
	@echo "  cp .env.example .env"

# Run the agent (creates a Daily room automatically if DAILY_ROOM_URL is not set)
dev:
	$(RUN) -m services.receptionist.main

# Install only (no model download)
install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r services/receptionist/requirements.txt
