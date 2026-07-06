# Gmail Support Agent — Capstone AI Agent Project

AI-агент службы поддержки с доступом к Gmail. Читает входящие письма,
понимает контекст запроса, готовит и отправляет ответы через tool calling —
с многоуровневой защитой от типичных ошибок и атак agentic-систем.

## Зачем это нужно

Служба поддержки тратит время на рутинные ответы: статус заказа,
условия возврата, стандартные жалобы. Агент берёт на себя первичную
обработку — читает письмо, определяет, нужен ли ответ, готовит текст
и либо отправляет его, либо предлагает человеку на подтверждение.

## Возможности

- 📧 Чтение и анализ входящих писем через Gmail API (OAuth)
- 🤖 Tool calling — агент сам решает, какое действие предпринять
  (`read_emails`, `get_email_by_id`, `send_email`)
- 🛡️ Многоуровневые guardrails — независимые от решения модели проверки
  перед любой отправкой письма
- ✅ Dry-run / approval режим — ничего не отправляется без подтверждения
- 📊 Eval harness — 5 golden-сценариев с LLM-as-judge оценкой качества
- 🔒 Security tests — 5 типов атак (prompt injection, подмена адреса,
  социальная инженерия, спам, фишинг)
- 🔍 Full tracing — каждый LLM call и tool call записывается с latency
  и токенами
- 🐳 Docker + CI/CD — воспроизводимая сборка, автоматические проверки
  на каждый push

## Архитектура

```
Письмо → read_emails → get_email_by_id → LLM (Groq, llama-3.3-70b)
                                              │
                                    решает: нужен ли send_email?
                                              │
                              ┌───────────────┴───────────────┐
                         Guardrail 1                    Guardrail 2
                    (письмо было открыто?)      (адрес совпадает с отправителем?
                                                  домен не в блок-листе?
                                                  нет prompt injection?
                                                  rate limit не превышен?)
                                              │
                                        Approval (dry-run)
                                              │
                                         Gmail API → отправка
```

Всё логируется в `audit_log.jsonl` (кто/что/когда) и `eval/traces/`
(полный путь одного прогона: LLM calls, tool calls, latency, токены).

## Быстрый старт

```bash
git clone https://github.com/ADanUser/gmail-agent.git
cd gmail-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Настройка секретов, Gmail OAuth и запуск через Docker — подробно в
[`docs/RUNBOOK.md`](docs/RUNBOOK.md).

```bash
# Локальный запуск
python -m gmail_agent.agent

# Через Docker
docker build -t gmail-agent .
docker run -it --env-file .env -v "$(pwd)/token.pickle:/app/token.pickle" gmail-agent
```

## Качество и безопасность

| Проверка | Результат |
|---|---|
| Eval harness (5 сценариев) | ✅ 5/5 (100%), ~1s средняя latency |
| Security tests (5 атак) | ✅ 5/5 устояли на уровне code guardrails |
| CI/CD | ✅ Автоматический прогон при каждом push ([Actions](../../actions)) |

Полные отчёты: [`eval/scorecard.md`](eval/scorecard.md),
[`security/threat_model.md`](security/threat_model.md),
[`reports/before_after_report.md`](reports/before_after_report.md).

## Известное ограничение (найдено и закрыто в процессе разработки)

LLM может вызвать `send_email` даже когда условие задачи не выполнено —
например, не найдя нужное письмо, модель пыталась отправить ответ на
несуществующий адрес вместо того, чтобы сообщить об отсутствии условия.
Промпт-хардненинг снижает частоту, но не устраняет полностью. Закрыто
двумя независимыми guardrail'ами на уровне кода — подробности в
[`security/threat_model.md`](security/threat_model.md) (T-009) и
[`reports/before_after_report.md`](reports/before_after_report.md).

Это иллюстрирует ключевой принцип проекта: **критичные действия
проверяются кодом, а не только доверием к модели.**

## Стек

Python 3.11 · OpenAI SDK (клиент) → Groq API (`llama-3.3-70b-versatile`)
· Gmail API (OAuth 2.0) · Docker · GitHub Actions

## Структура проекта

```
├── src/gmail_agent/     # основной пакет: agent, guardrails, gmail_tools, auth, audit
├── eval/                # eval harness, golden dataset, traces
├── security/            # security tests, threat model
├── docs/                # RUNBOOK.md, SECURITY.md
├── reports/             # before/after отчёты
└── .github/workflows/   # CI pipeline
```

## Ограничения текущей версии

- Gmail OAuth в режиме Testing — токен истекает каждые 7 дней
- Только текстовые письма — вложения не обрабатываются
- Rate limit 10 писем/минуту — настраивается в `guardrails.py`
- Требуется ручное подтверждение перед реальной отправкой (dry-run по умолчанию)