class MessagingProvider:
    name = "messaging"
    label = "wiadomości"

    def is_configured(self):
        return False

    def send(self, contact, content, attachment=None):
        raise NotImplementedError


class EmailProvider:
    name = "email"
    label = "e-mail"

    def is_configured(self):
        return False

    def send(self, contact, content, attachment=None):
        raise NotImplementedError


class FileShareProvider:
    name = "file_share"
    label = "udostępnianie plików"

    def is_configured(self):
        return False

    def send(self, contact, content, attachment=None):
        raise NotImplementedError


_PROVIDERS = {
    "messaging": MessagingProvider(),
    "whatsapp": MessagingProvider(),
    "discord": MessagingProvider(),
    "telegram": MessagingProvider(),
    "email": EmailProvider(),
    "gmail": EmailProvider(),
    "file": FileShareProvider(),
}


def get_provider(channel="messaging"):
    name = str(channel or "messaging").lower()
    if name in ("messaging", "sms", "twilio"):
        try:
            from integrations.twilio_provider import TwilioMessagingProvider, twilio_configured

            if twilio_configured():
                return TwilioMessagingProvider()
        except Exception:
            pass
    return _PROVIDERS.get(name, MessagingProvider())


def send_message(contact, content, channel="messaging", attachment=None):
    provider = get_provider(channel)
    if not provider.is_configured():
        return (
            "Nie mam jeszcze skonfigurowanej integracji do wysyłania "
            f"{provider.label}."
        )
    return provider.send(contact, content, attachment=attachment)
