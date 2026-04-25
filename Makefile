.PHONY: setup dev dev-sip install lint gen-fallback convert-captures metrics purge-old-logs

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

# Run the agent in SIP mode — AudioSocket listener on :8089 for Asterisk to connect.
# Requires services/telephony/ Asterisk container to be up and registered with
# the FritzBox. See docs/phase1-m1-fritzbox-setup.md.
dev-sip:
	@echo ""
	@echo "  Starting agent in SIP mode — AudioSocket listener on :8089"
	@echo "  Asterisk (services/telephony/) must be running and a call routed"
	@echo "  to the receptionist number on the FritzBox."
	@echo ""
	TRANSPORT=sip $(RUN) -m services.receptionist.main

# Generate the SIP crash-fallback audio clip.
# Piper synth + soxr resample to 8 kHz SLIN16 (Asterisk AudioSocket format).
# Run once after `make setup`.
gen-fallback:
	$(RUN) scripts/gen_fallback.py

# Convert raw SLIN8 captures to WAV for playback and offline Whisper evaluation.
# Requires ffmpeg in PATH. Reads logs/captures/*.raw
convert-captures:
	@echo "Converting captures in logs/captures/ ..."
	@for f in logs/captures/*.raw; do \
		out=$${f%.raw}.wav; \
		ffmpeg -y -f s16le -ar 8000 -ac 1 -i "$$f" "$$out" && echo "  $$out"; \
	done
	@echo "Done."

# Print a 1-page call quality summary from logs/events.jsonl
metrics:
	@$(RUN) scripts/metrics_report.py

# Delete events.jsonl lines older than LOG_RETENTION_DAYS (default 30) and
# raw captures older than CAPTURE_RETENTION_DAYS (default 7).
purge-old-logs:
	$(RUN) scripts/purge_logs.py

# Install only (no model download)
install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r services/receptionist/requirements.txt
