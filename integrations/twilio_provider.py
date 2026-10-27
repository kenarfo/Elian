# ==========================
# ELIAN — Twilio SMS (opt-in, secrets only)
# ==========================
"""REST SMS send/list. No number is invented. Missing secrets = not_configured."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from Config import get_config_value, get_secret
from integrations.base import Availability, IntegrationProvider, IntegrationResult

RequestFn = Callable[..., Any]


def twilio_enabled() -> bool:
    block = get_config_value("integrations", "twilio", default={}) or {}
    if isinstance(block, dict):
        return bool(block.get("enabled", False))
    return False


def twilio_credentials() -> Dict[str, str]:
    sid = get_secret("twilio_account_sid", env_var="TWILIO_ACCOUNT_SID")
    token = get_secret("twilio_auth_token", env_var="TWILIO_AUTH_TOKEN")
    number = str(
        get_secret("twilio_from_number", env_var="TWILIO_FROM_NUMBER")
        or (get_config_value("integrations", "twilio", default={}) or {}).get("from_number")
        or ""
    ).strip()
    return {"sid": sid, "token": token, "from_number": number}


def twilio_configured() -> bool:
    if not twilio_enabled():
        return False
    creds = twilio_credentials()
    return bool(creds["sid"] and creds["token"] and creds["from_number"])


def twilio_status_spoken() -> str:
    if not twilio_enabled():
        return (
            "Nie mam jeszcze własnego numeru. "
            "Jak włączysz Twilio i dodasz SID plus numer, będę mógł SMS-ować."
        )
    if not twilio_configured():
        return (
            "Twilio jest włączone, ale brakuje SID, tokenu albo numeru. "
            "Nie wymyślam numeru."
        )
    number = twilio_credentials()["from_number"]
    return f"Mój numer Twilio to {number}."


class TwilioMessagingProvider(IntegrationProvider):
    name = "twilio"
    label = "SMS Twilio"

    def availability(self) -> Availability:
        if not twilio_enabled():
            return Availability.DISABLED
        if not twilio_configured():
            return Availability.NOT_CONFIGURED
        return Availability.AVAILABLE

    def is_configured(self) -> bool:
        return twilio_configured()

    def send(self, contact, content, attachment=None):
        to = str(contact or "").strip()
        body = str(content or "").strip()
        result = send_sms(to, body)
        if not result.success:
            return result.message
        return result.message


def send_sms(
    to: str,
    body: str,
    *,
    request_fn: Optional[RequestFn] = None,
) -> IntegrationResult:
    dest = str(to or "").strip()
    text = str(body or "").strip()
    if not dest or not text:
        return IntegrationResult(
            success=False,
            message="Podaj numer i treść SMS.",
            error_code="NOT_FOUND",
            availability=Availability.AVAILABLE,
        )
    if not twilio_configured():
        return IntegrationResult(
            success=False,
            message=twilio_status_spoken(),
            error_code="NOT_CONFIGURED",
            availability=Availability.NOT_CONFIGURED,
        )
    creds = twilio_credentials()
    try:
        poster = request_fn or _post
        payload = poster(
            f"https://api.twilio.com/2010-04-01/Accounts/{creds['sid']}/Messages.json",
            data={"From": creds["from_number"], "To": dest, "Body": text},
            auth=(creds["sid"], creds["token"]),
            timeout=8,
        )
    except Exception as error:
        return IntegrationResult(
            success=False,
            message="Nie udało się wysłać SMS.",
            error_code="TEMPORARY_FAILURE",
            data={"error": str(error)[:160]},
            availability=Availability.UNAVAILABLE,
        )
    sid = str((payload or {}).get("sid") or "")
    if not sid:
        return IntegrationResult(
            success=False,
            message="Twilio nie potwierdziło wysyłki.",
            error_code="TEMPORARY_FAILURE",
            availability=Availability.UNAVAILABLE,
        )
    return IntegrationResult(
        success=True,
        message="SMS wysłany.",
        data={"sid": sid, "to": dest},
        availability=Availability.AVAILABLE,
    )


def list_inbound_sms(
    *,
    limit: int = 5,
    request_fn: Optional[RequestFn] = None,
) -> List[Dict[str, str]]:
    if not twilio_configured():
        return []
    creds = twilio_credentials()
    try:
        getter = request_fn or _get
        payload = getter(
            f"https://api.twilio.com/2010-04-01/Accounts/{creds['sid']}/Messages.json",
            params={"To": creds["from_number"], "PageSize": max(1, min(10, int(limit or 5)))},
            auth=(creds["sid"], creds["token"]),
            timeout=8,
        )
    except Exception:
        return []
    rows = []
    for item in list((payload or {}).get("messages") or []):
        if str(item.get("direction") or "").lower() not in ("inbound", "incoming"):
            continue
        rows.append(
            {
                "id": str(item.get("sid") or f"sms_{uuid4().hex[:10]}"),
                "sender": str(item.get("from") or "").strip(),
                "body": str(item.get("body") or "").strip(),
            }
        )
    return rows


def _post(url, *, data, auth, timeout):
    import requests

    response = requests.post(url, data=data, auth=auth, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _get(url, *, params, auth, timeout):
    import requests

    response = requests.get(url, params=params, auth=auth, timeout=timeout)
    response.raise_for_status()
    return response.json()
