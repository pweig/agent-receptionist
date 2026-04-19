# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# First-time setup (creates venv, installs deps, downloads Piper TTS models ~125 MB)
make setup

# Run the agent (starts FastAPI on http://localhost:7860)
make dev
# → open http://localhost:7860, click "Start Call"

# Install deps only (skip model download)
make install
```

Environment: copy `.env.example` to `.env` and fill in `GROQ_API_KEY`. No other accounts needed.

Override Whisper model at runtime: `WHISPER_MODEL=tiny make dev` (tiny=75MB, small=460MB, large-v3-turbo=800MB).

## Architecture

The bot is a single Pipecat pipeline that handles one WebRTC call at a time. It lives entirely in `services/receptionist/`.

**Pipeline (in order):**
```
SmallWebRTCTransport.input()
→ VADProcessor (SileroVADAnalyzer)
→ WhisperSTTService (faster-whisper, EN/DE auto-detect)
→ LLMContextAggregatorPair.user()
→ OpenAILLMService (Groq llama-3.3-70b-versatile)
→ PiperTTSService (en_US-ryan-high; switches to de_DE-thorsten-high at runtime)
→ SmallWebRTCTransport.output()
→ LLMContextAggregatorPair.assistant()
```

`main.py` assembles this pipeline, wraps it in a FastAPI app with `/api/offer` (POST/PATCH) WebRTC signaling endpoints, and serves `static/index.html` as the browser client. Each browser connection spawns a new `run_bot()` coroutine via `asyncio.create_task`.

**State machine (`pipecat-flows`):**

`flows/nodes.py` contains all 9 `NodeConfig` factories. Each node defines the LLM's task instructions and the tools available for that state. State transitions happen exclusively through tool call return values — every handler returns `(result_dict, next_NodeConfig)` or `(result_dict, None)` to stay in the current node.

Flow:
```
greeting ──set_language──► hours_check ──get_office_hours──► collect_info / closing
collect_info ──search_patient / request_slots──► slot_proposal / handoff
slot_proposal ──confirm_slot / get_more_slots──► confirmation / handoff
confirmation ──book_appointment + send_confirmation──► closing
Any node ──transfer_to_human──► handoff ──complete_handoff──► closing
closing → end_conversation (post_action)
```

**Key design constraints:**
- Nodes with `functions: []` cannot advance the flow — every state requires at least one callable function for transition.
- Handlers live in `flows/nodes.py`, not `tools/schemas.py`. `schemas.py` only holds raw property dicts and `TTS_VOICES` to avoid circular imports.
- The TTS voice language switch uses `TTSUpdateSettingsFrame(settings={"voice": TTS_VOICES[lang]})` queued on `flow_manager.task`.
- `handoff.py` contains a pure-Python `evaluate_handoff()` function (regex triggers) — it is **not** wired into the pipeline automatically; currently the LLM routes to handoff via the `transfer_to_human` tool.

**Tools (mock PMS):**

`tools/pms_mock.py` — 5 async functions backed by in-memory dicts. Fictional patients include an intentional ambiguous pair (Thomas Müller / Tobias Müller) and a pediatric patient. Replace these functions with real PMS adapters (Dampsoft, Dentrix) without changing the tool schemas.

**LLM instructions:**

`prompt.py` exports two things:
- `PERSONA_SYSTEM_PROMPT` — static persona (injected as `role_messages` in every node)
- `STATE_TASK_MESSAGES` — per-state task instructions (injected as `task_messages`)

**Configuration files:**
- `config/settings.yaml` — model names, VAD stop_secs, STT thresholds, handoff thresholds
- `config/office_hours.yaml` — weekday schedule, DE+US public holidays, after-hours routing

## Key Pipecat 1.0.0 API Notes

- `LLMContext` is from `pipecat.processors.aggregators.llm_context` (not `openai_llm_context`)
- `LLMContextAggregatorPair` is from `pipecat.processors.aggregators.llm_response_universal`
- `OpenAILLMService` is from `pipecat.services.openai.llm`
- `WhisperSTTService` and `Model` are from `pipecat.services.whisper.stt`
- `SmallWebRTCTransport` requires the `pipecat-ai[webrtc]` extra (installs `aiortc`)
- VAD is a separate `VADProcessor` in the pipeline (not a `TransportParams` field — that's Daily-specific)
- `FlowManager` constructor takes only `task`, `llm`, `context_aggregator` — no `flow_config` param
- Handler signature: `async def handler(args: dict, flow_manager: FlowManager) -> tuple[dict, NodeConfig | None]`
