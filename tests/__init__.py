# Unit-test defaults — do not change product config.
# - Disable background self-dev so ElianRuntime tests don't mutate the repo.
# - Disable LLM→TTS streaming so ask_ai patches in CM tests remain effective.
import os

os.environ.setdefault("ELIAN_DISABLE_BACKGROUND_SELF_DEV", "1")
os.environ.setdefault("ELIAN_STREAM_LLM", "0")
# Keep unit tests fast/offline: Fish S2 CUDA path is covered by integration tests.
os.environ.setdefault("ELIAN_FISH_FORCE_UNAVAILABLE", "1")
