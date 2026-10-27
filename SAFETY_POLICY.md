# Safety policy

AI never bypasses deterministic safety.

## Side-effect path

semantic intent → tool permission → ActionFirewall → target resolution → action → observation → verification

## Hard rules

- TALK/ANSWER/SIMULATE/WAIT: no mutation tools
- Hypothetical / quoted / garbled STT: no mutation
- FastPath cannot steal questions or jokes
- HA: before → action → after → verify. HTTP accept is not SUCCESS
- ComputerAgent: wrong window is a failed act, not a retry-with-hope
- No blind retry when a side effect may already have happened
- Destructive/send/purchase/account: existing `core/risk.py` confirmation
- Browser Stage D: captcha is the user’s; no cookie/credential dump
- Camera/presence: explicit opt-in, default DISABLED
- Memory: no passwords, tokens, keys

Metrics that must stay at zero on fixtures: fake success, fake perception, social/creative tool calls, dictation target leak, call-center closer, context hijack.
