import ollama

from Config import get_config_value
from prompts import brain_system_prompt, knowledge_system_prompt
from Memory import (
    load_recent_conversation,
    record_conversation,
    search_memory,
)


MODEL = get_config_value(
    "ai",
    "ollama_model",
    default="llama3.1:8b",
    legacy_key="ollama_model",
)

CLIENT = ollama.Client(
    timeout=float(
        get_config_value(
            "ai",
            "request_timeout",
            default=30,
        )
    )
)

SYSTEM_PROMPT = brain_system_prompt()


def _initial_conversation():
    recent = load_recent_conversation(limit=8)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *recent,
    ]


conversation = _initial_conversation()


def _memory_context(text, fallback_memory=None):
    memories = search_memory(text, limit=6)
    if not memories and fallback_memory:
        memories = [
            str(item).strip()
            for item in fallback_memory[-6:]
            if str(item).strip()
        ]

    if not memories:
        return ""

    return (
        "\n\nIstotne, jawnie zapamiętane fakty o użytkowniku:\n"
        + "\n".join(f"- {item}" for item in memories)
    )


def _build_messages(text, memory=None, memory_manager=None, *, mode="chat", response_language=None):
    """Prepare messages for Brain chat. May append user turn to ``conversation``."""
    global conversation

    from core.response_language import language_instruction

    lang = (response_language or "pl").lower()
    lang_block = language_instruction(lang)
    knowledge_mode = str(mode or "chat").lower() in ("knowledge", "explain", "repair")

    record_conversation("user", text)
    extra = ""
    if not knowledge_mode:
        extra = _memory_context(text, memory)
    if memory_manager is not None:
        try:
            built = memory_manager.build_context(
                text,
                include_preferences=not knowledge_mode,
                knowledge_only=knowledge_mode,
            )
            block = built.get("text") or ""
            if block:
                label = (
                    "Kontekst rozmowy (temat/follow-up; bez preferencji aplikacji):\n"
                    if knowledge_mode
                    else "Kontekst pamięci Eliana (używaj tylko tych faktów):\n"
                )
                extra = (extra + "\n\n" if extra else "\n\n") + label + block
        except Exception:
            pass

    if knowledge_mode:
        messages = [
            {"role": "system", "content": knowledge_system_prompt() + "\n\n" + lang_block},
            {"role": "user", "content": text + extra},
        ]
        temperature = 0.15
    else:
        user_message = text + extra + "\n\n" + lang_block
        conversation.append({
            "role": "user",
            "content": user_message,
        })
        messages = conversation
        temperature = 0.35

    return {
        "messages": messages,
        "temperature": temperature,
        "knowledge_mode": knowledge_mode,
        "lang": lang,
        "user_appended": not knowledge_mode,
    }


def _trim_conversation():
    global conversation
    while len(conversation) > 13:
        conversation.pop(1)
        if len(conversation) > 13:
            conversation.pop(1)


def _commit_assistant(answer: str, *, knowledge_mode: bool, lang: str, user_appended: bool):
    """Write only the spoken/final assistant text into history."""
    global conversation
    from core.response_language import coerce_to_session_language

    answer = coerce_to_session_language(str(answer or "").strip(), lang)
    if not answer:
        return ""

    if knowledge_mode:
        record_conversation("assistant", answer)
        return answer

    conversation.append({
        "role": "assistant",
        "content": answer,
    })
    record_conversation("assistant", answer)
    _trim_conversation()
    return answer


def _pop_user_if_appended(user_appended: bool):
    global conversation
    if user_appended and conversation and conversation[-1].get("role") == "user":
        conversation.pop()


class AiStreamSession:
    """Streaming Brain reply. Assistant history is committed only via ``commit_spoken``."""

    def __init__(
        self,
        *,
        messages,
        temperature: float,
        knowledge_mode: bool,
        lang: str,
        user_appended: bool,
        model=None,
        num_predict: int = 220,
    ):
        self._messages = messages
        self._temperature = temperature
        self.knowledge_mode = knowledge_mode
        self.lang = lang
        self.user_appended = user_appended
        self._model = model or MODEL
        self._num_predict = num_predict
        self.generated_text = ""
        self.spoken_text = ""
        self.failed = False
        self.cancelled = False
        self._committed = False
        self._started = False

    def iter_tokens(self):
        """Yield token deltas from the model. Does not write assistant history."""
        from core.model_client import get_model_client

        self._started = True
        try:
            from core.metrics import get_metrics

            get_metrics().record_model_call("brain")
        except Exception:
            pass

        client = get_model_client()
        try:
            stream = client.chat_stream(
                self._messages,
                temperature=self._temperature,
                model=self._model,
                options={"num_predict": self._num_predict},
            )
            for piece in stream:
                if self.cancelled:
                    return
                text = str(piece or "")
                if not text:
                    continue
                self.generated_text += text
                yield text
        except Exception as error:
            print("Błąd lokalnego modelu Ollama:", error)
            self.failed = True
            if not self.generated_text:
                fallback = "Lokalny model chwilowo nie odpowiada."
                self.generated_text = fallback
                yield fallback

        if not self.generated_text.strip() and not self.cancelled:
            fallback = "Nie mam teraz dobrej odpowiedzi."
            self.generated_text = fallback
            yield fallback

    def cancel_generation(self) -> None:
        """Cancel in-flight Ollama stream (best-effort)."""
        self.cancelled = True
        try:
            from core.model_client import get_model_client

            client = get_model_client()
            cancel = getattr(client, "cancel_active_stream", None)
            if callable(cancel):
                cancel()
        except Exception:
            pass

    def commit_spoken(self, spoken: str) -> str:
        """Persist only text that was actually spoken (or full reply if complete)."""
        if self._committed:
            return self.spoken_text
        self._committed = True
        spoken = str(spoken or "").strip()
        self.spoken_text = spoken

        if not spoken:
            if self.failed:
                _pop_user_if_appended(self.user_appended)
            return ""

        return _commit_assistant(
            spoken,
            knowledge_mode=self.knowledge_mode,
            lang=self.lang,
            user_appended=self.user_appended,
        )

    def abandon(self) -> None:
        """No speech produced — drop unfinished assistant; keep or pop user on fail."""
        if self._committed:
            return
        self._committed = True
        if self.failed or not self._started:
            _pop_user_if_appended(self.user_appended)


def begin_ai_stream(
    text,
    memory=None,
    memory_manager=None,
    *,
    mode="chat",
    response_language=None,
):
    """Start a streaming Brain turn. Caller must ``commit_spoken`` or ``abandon``."""
    text = str(text or "").strip()
    if not text:
        return None

    built = _build_messages(
        text,
        memory=memory,
        memory_manager=memory_manager,
        mode=mode,
        response_language=response_language,
    )
    return AiStreamSession(
        messages=built["messages"],
        temperature=built["temperature"],
        knowledge_mode=built["knowledge_mode"],
        lang=built["lang"],
        user_appended=built["user_appended"],
    )


def ask_ai(text, memory=None, memory_manager=None, *, mode="chat", response_language=None):
    """Blocking Brain chat (compatible path). Full answer is committed when returned."""
    text = text.strip()
    if not text:
        return ""

    built = _build_messages(
        text,
        memory=memory,
        memory_manager=memory_manager,
        mode=mode,
        response_language=response_language,
    )
    messages = built["messages"]
    temperature = built["temperature"]
    knowledge_mode = built["knowledge_mode"]
    lang = built["lang"]
    user_appended = built["user_appended"]

    try:
        from core.metrics import get_metrics

        get_metrics().record_model_call("brain")
        response = CLIENT.chat(
            model=MODEL,
            messages=messages,
            options={"temperature": temperature, "num_predict": 220},
            keep_alive="30m",
        )
        answer = response["message"]["content"].strip()
    except Exception as error:
        print("Błąd lokalnego modelu Ollama:", error)
        _pop_user_if_appended(user_appended)
        return "Lokalny model chwilowo nie odpowiada."

    if not answer:
        return "Nie mam teraz dobrej odpowiedzi."

    return _commit_assistant(
        answer,
        knowledge_mode=knowledge_mode,
        lang=lang,
        user_appended=user_appended,
    )


def reset_conversation():
    global conversation
    conversation = [{"role": "system", "content": SYSTEM_PROMPT}]
