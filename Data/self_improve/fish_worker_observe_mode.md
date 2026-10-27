# Self-improve mode after Fish persistent worker

**Mode:** OBSERVE → ANALYZE → PROPOSE → TEST → REPORT
**Auto-deploy:** disabled
**Rollback:** required via checkpoint before any future adopt

## Protected (no autonomous edits)

- `core/multi_agent/resource_manager.py` / `hardware.py` (`max_gpu_slots`)
- `core/ollama_lifecycle.py`
- `voice/fish_s2_backend.py` / `voice/fish_s2_worker.py`
- `voice/tts_engine.py` (Piper fallback routing)
- cancel / barge-in paths in `Voice.py`

## Allowed now

- Collect runtime metrics (warm latency, VRAM, orphan counts)
- Analyze diagnostics JSON under `Data/diagnostics/`
- Propose patches as text/proposals only
- Run tests / report

## Gate before any protected change

1. Measurable metric delta (e.g. warm_ms, peak_vram_mb)
2. Unittest + agent-smoke PASS
3. Human approval
4. Checkpoint + rollback plan
