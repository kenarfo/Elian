# ==========================
# ELIAN AI ASSISTANT
# Version 0.7
# Skills/System.py
# ==========================

import datetime
import platform
import re

import psutil


def match(text):
    text = text.lower().strip()
    patterns = (
        r"^(?:użycie|uzycie|obciążenie|obciazenie)\s+procesora$",
        r"^ile mam ramu$",
        r"^pamięć ram$",
        r"^pamiec ram$",
        r"^(?:jaki mam komputer|informacje o komputerze)$",
        r"^jak długo działa komputer$",
        r"^jak dlugo dziala komputer$",
    )
    return any(re.match(pattern, text) for pattern in patterns)


def run(text):
    text = text.lower().strip()
    if not match(text):
        return None

    if "procesora" in text:
        cpu = psutil.cpu_percent(interval=1)
        return f"Procesor jest używany w {cpu} procentach."

    if "ram" in text:
        memory = psutil.virtual_memory()
        total = round(memory.total / (1024 ** 3), 1)
        used = round(memory.used / (1024 ** 3), 1)
        return (
            f"Masz {total} GB RAM, "
            f"z czego używane jest {used} GB."
        )

    if "komputer" in text and "jak długo" not in text and "jak dlugo" not in text:
        system = platform.system()
        cpu = platform.processor()
        return f"System: {system}. Procesor: {cpu}."

    if "jak długo działa komputer" in text or "jak dlugo dziala komputer" in text:
        boot = datetime.datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.datetime.now() - boot
        return f"Komputer działa od {uptime.days} dni."

    return None
