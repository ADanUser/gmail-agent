"""
GUARDRAILS — защита на уровне кода, независимо от поведения LLM.

Почему это нужно отдельно от system prompt:
- System prompt можно обойти через prompt injection (attack_001 показал это)
- Код не обманешь — если правило написано в коде, оно выполняется всегда

Принцип: ALLOWLIST — разрешаем только то что явно разрешено,
всё остальное блокируется по умолчанию.

Использование:
    from guardrails import validate_send_email

    result = validate_send_email(
        to="client@example.com",
        original_sender="client@example.com"
    )
    if not result["allowed"]:
        print(f"❌ Заблокировано: {result['reason']}")
"""

import re
import time
from collections import deque

# Домены которым никогда нельзя отправлять письма
BLOCKED_DOMAINS = [
    "evil.com",
    "hack.com",
    "spam.com",
    "temp-mail.org",
    "guerrillamail.com",
]

# Паттерны которые выглядят как injection в теле письма
INJECTION_PATTERNS = [
    r"игнорируй все предыдущие инструкции",
    r"ignore (all |previous |prior )?instructions",
    r"ты теперь (другой|новый) агент",
    r"you are now",
    r"forget (your |all )?instructions",
    r"забудь (все |свои )?инструкции",
    r"system prompt",
    r"системный промпт",
]


def validate_send_email(to: str, original_sender: str, body: str = "") -> dict:
    """
    Проверяет безопасность перед отправкой письма.

    to: адрес куда агент хочет отправить
    original_sender: адрес кто прислал исходное письмо
    body: тело письма которое хочет отправить агент

    Возвращает {"allowed": bool, "reason": str}
    """

    # Проверка 0: rate limit — не более 10 писем в минуту
    rate_check = email_rate_limiter.check("send_email")
    if not rate_check["allowed"]:
        return {"allowed": False, "reason": rate_check["reason"]}

    # Проверка 1: адрес должен совпадать с отправителем
    if to.lower().strip() != original_sender.lower().strip():
        return {
            "allowed": False,
            "reason": f"Адрес получателя '{to}' не совпадает с отправителем '{original_sender}'. Возможна подмена адреса."
        }

    # Проверка 2: домен не в блок-листе
    domain = to.split("@")[-1].lower() if "@" in to else ""
    if domain in BLOCKED_DOMAINS:
        return {
            "allowed": False,
            "reason": f"Домен '{domain}' находится в блок-листе."
        }

    # Проверка 3: тело письма не содержит injection паттернов
    body_lower = body.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, body_lower):
            return {
                "allowed": False,
                "reason": f"В теле письма обнаружен подозрительный паттерн: '{pattern}'"
            }

    # Проверка 4: тело ответа не содержит подозрительных URL
    suspicious_url_patterns = [
        r"http[s]?://(?![\w.-]*\.(ru|com|org|net|gmail\.com))[^\s]+",  # нестандартные домены
        r"evil-",
        r"phishing",
        r"steal-data",
        r"hack",
    ]
    body_lower = body.lower()
    for pattern in suspicious_url_patterns:
        if re.search(pattern, body_lower):
            return {
                "allowed": False,
                "reason": f"В теле ответа обнаружена подозрительная ссылка или слово: '{pattern}'"
            }

    return {"allowed": True, "reason": "Письмо прошло все проверки"}


def detect_injection_in_email(email_body: str) -> dict:
    """
    Проверяет входящее письмо на признаки prompt injection
    перед тем как передать его агенту.

    Возвращает {"safe": bool, "reason": str}
    """
    body_lower = email_body.lower()

    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, body_lower):
            return {
                "safe": False,
                "reason": f"Обнаружен признак prompt injection: '{pattern}'"
            }

    return {"safe": True, "reason": "Письмо выглядит безопасным"}


# ============================================================
# RATE LIMITER
# ============================================================

class RateLimiter:
    """
    Ограничивает количество действий агента за промежуток времени.

    Защищает от:
    - DDoS через агента (злоумышленник шлёт 1000 писем)
    - Runaway agent (агент сошёл с ума и зациклился)
    - Случайных багов с бесконечным циклом

    Принцип: sliding window — запоминаем время каждого действия,
    если за последние window_seconds было больше max_calls — блокируем.

    Использование:
        limiter = RateLimiter(max_calls=10, window_seconds=60)
        result = limiter.check("send_email")
        if not result["allowed"]:
            print(f"Заблокировано: {result['reason']}")
    """

    def __init__(self, max_calls: int = 10, window_seconds: int = 60):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._calls = deque()  # очередь временных меток вызовов

    def check(self, action_name: str = "action") -> dict:
        """
        Проверяет можно ли выполнить действие прямо сейчас.
        Если лимит превышен — возвращает allowed=False.
        """
        now = time.time()

        # Удаляем старые вызовы которые вышли за пределы окна
        while self._calls and self._calls[0] < now - self.window_seconds:
            self._calls.popleft()

        if len(self._calls) >= self.max_calls:
            remaining = round(self._calls[0] + self.window_seconds - now)
            return {
                "allowed": False,
                "reason": f"Rate limit превышен: {self.max_calls} вызовов за {self.window_seconds}с. Повтори через {remaining}с."
            }

        self._calls.append(now)
        return {
            "allowed": True,
            "reason": f"OK ({len(self._calls)}/{self.max_calls} за последние {self.window_seconds}с)"
        }


# Глобальный rate limiter для send_email — не более 10 писем в минуту
email_rate_limiter = RateLimiter(max_calls=10, window_seconds=60)