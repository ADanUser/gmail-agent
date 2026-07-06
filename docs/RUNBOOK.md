# RUNBOOK — Capstone Gmail Support Agent

Инструкция для запуска, обновления и диагностики проекта.

## 1. Что это

AI-агент службы поддержки с доступом к Gmail. Читает входящие письма,
проверяет условия из задачи, и через tool calling (`send_email`) готовит
или отправляет ответ. Защищён многоуровневыми guardrails (см. раздел 6).

Стек: Python 3.11, OpenAI SDK (клиент, база — Groq API), Gmail API (OAuth),
Docker, GitHub Actions (CI).

Проект оформлен как устанавливаемый Python-пакет (`pyproject.toml`),
исходный код лежит в `src/gmail_agent/`.

## 2. Требования

- Python 3.11+
- Docker (или Colima как альтернатива Docker Desktop на macOS)
- Google Cloud Console проект с включённым Gmail API (OAuth consent screen
  в режиме Testing)
- API-ключ Groq (https://console.groq.com)

## 3. Первоначальная настройка (один раз)

### 3.1 Виртуальное окружение

Создать и активировать изолированное окружение — обязательно, чтобы
версии зависимостей проекта не конфликтовали с другими Python-проектами
на этой машине:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Активировать нужно в каждой новой сессии терминала перед работой с
проектом (в начале строки терминала появится `(.venv)`).

### 3.2 Установка пакета

```bash
pip install -e .
```

Флаг `-e` (editable) — изменения в коде сразу видны без переустановки.
Зависимости и версии зафиксированы в `pyproject.toml`.

### 3.3 Секреты

Создать файл `.env` в корне проекта:

```
GROQ_API_KEY=твой_ключ_groq
```

`.env` НЕ должен попадать в Git или Docker-образ — уже добавлен в
`.gitignore` и `.dockerignore`.

### 3.4 Gmail OAuth

1. Убедиться, что `credentials.json` лежит в корне проекта (скачивается
   из Google Cloud Console → APIs & Services → Credentials).
2. Запустить локально (не в Docker):

   ```bash
   python -m gmail_agent.auth
   ```

3. Откроется браузер — войти в Google-аккаунт, подтвердить доступ.
4. После успешной авторизации в корне появится `token.pickle` — токен
   доступа, привязанный к аккаунту. Тоже не должен попадать в Git/образ.

Если `token.pickle` истёк или повреждён — удалить и повторить шаг 3.4:

```bash
rm token.pickle
python -m gmail_agent.auth
```

## 4. Запуск локально (без Docker)

```bash
source .venv/bin/activate   # если ещё не активировано
python -m gmail_agent.agent
```

## 5. Запуск через Docker

### 5.1 Сборка образа

```bash
docker build -t capstone-gmail-agent .
```

Пересобирать нужно при любом изменении кода или `pyproject.toml` —
запущенный контейнер не подхватывает изменения на лету.

### 5.2 Запуск контейнера

Секреты и токен передаются снаружи, а не встраиваются в образ:

```bash
docker run -it --env-file .env \
  -v "$(pwd)/token.pickle:/app/token.pickle" \
  capstone-gmail-agent
```

Флаги:
- `-it` — обязателен, если агент запрашивает подтверждение через `input()`
  (dry-run режим). Без него получите `EOFError: EOF when reading a line`.
- `--env-file .env` — прокидывает переменные окружения из файла на диске.
- `-v ...` — пробрасывает `token.pickle` с хоста внутрь контейнера
  (volume mount), не копируя его в образ.

Если путь к проекту содержит пробелы (например, папка `AI Dev`) — всегда
оборачивать `$(pwd)/...` в кавычки, иначе `docker: invalid reference format`.

## 6. Guardrails — что заблокирует агента и почему

Агент проходит через несколько независимых проверок перед реальной
отправкой письма (независимых от того, что "решила" модель):

1. **Guardrail 1 (agent.py)** — блокирует `send_email`, если перед этим
   не было вызова `get_email_by_id`. Защита от ответа на несуществующее
   или неоткрытое письмо.
2. **Guardrail 2 (guardrails.py → validate_send_email)**:
   - Rate limit — не более 10 писем в минуту.
   - Адрес `to` должен совпадать с реальным отправителем открытого письма.
   - Домен получателя не должен быть в блок-листе.
   - Тело письма проверяется на паттерны prompt injection.
3. **Approval (dry-run)** — при `dry_run=True` перед реальной отправкой
   запрашивается ручное подтверждение "да/нет" в терминале.

Все блокировки и действия логируются через `log_action()` в
`audit_log.jsonl` со статусами `success` / `blocked` / `cancelled` / `error`.

## 7. CI/CD

При каждом push или pull request в `main` автоматически запускается
GitHub Actions workflow (`.github/workflows/ci.yml`):

1. Установка пакета: `pip install -e .`
2. Прогон `security/security_tests.py` (5 атак + rate limiter)
3. Прогон `eval/eval_runner.py` (5 golden-сценариев, LLM-as-judge)

`GROQ_API_KEY` передаётся в CI через GitHub Secrets
(Settings → Secrets and variables → Actions), не хранится в коде
воркфлоу.

## 8. Структура проекта

```
capstone_gmail_agent/
├── pyproject.toml          # конфигурация пакета и зависимости
├── Dockerfile
├── src/gmail_agent/        # основной пакет
│   ├── agent.py
│   ├── guardrails.py
│   ├── audit.py
│   ├── auth.py
│   └── gmail_tools.py
├── eval/                   # eval harness + traces
├── security/               # security tests + threat model
├── docs/                   # RUNBOOK.md, SECURITY.md
├── reports/                # before/after отчёты
└── .github/workflows/      # CI pipeline
```

Все внутренние импорты используют полный путь пакета, например:
`from gmail_agent.guardrails import validate_send_email`.

## 9. Known issues

- **LLM может вызвать tool даже когда условие задачи не выполнено.**
  Пример: агент искал письмо от hh.ru, не нашёл, но попытался вызвать
  `send_email` с фиктивным адресом. Промпт-хардненинг снижает частоту,
  но не устраняет полностью — поэтому guardrails на уровне кода
  обязательны, а не опциональны.
- Свободный тариф OpenRouter имеет строгие лимиты запросов — проект
  переключён на Groq (`llama-3.3-70b-versatile`) как более стабильный
  провайдер. Код клиента под OpenRouter оставлен закомментированным в
  `agent.py` как fallback-референс.

## 10. Диагностика

| Симптом | Причина | Решение |
|---|---|---|
| `EOFError: EOF when reading a line` | Контейнер запущен без `-it` | Добавить `-it` к `docker run` |
| `docker: invalid reference format` | Путь с пробелами без кавычек | Обернуть `$(pwd)/...` в кавычки |
| `TypeError: Client.__init__() got an unexpected keyword argument 'proxies'` | Несовместимая версия `httpx` | Версия зафиксирована в `pyproject.toml` (`httpx==0.27.2`) — пересобрать через `pip install -e .` |
| `docker: open .env: no such file or directory` | `.env` отсутствует в корне проекта | Создать `.env` в корне `capstone_gmail_agent/` |
| Gmail API отказывает в доступе | `token.pickle` истёк/отсутствует | `rm token.pickle && python -m gmail_agent.auth` |
| `ModuleNotFoundError: No module named 'gmail_agent'` (или `guardrails`, `tracer`, `auth`) | Пакет не установлен, или модуль импортирован по старому пути | Выполнить `pip install -e .` из корня; проверить, что импорты используют `from gmail_agent.xxx import ...`, а не голое имя модуля |
| Конфликт версий при `pip install -e .` (например с `httpx`) | Установка в общее (не изолированное) окружение Python, где уже стоят другие проекты с другими версиями зависимостей | Использовать `.venv` (см. раздел 3.1) — устанавливать пакет только в изолированное окружение |

## 11. Откат (rollback)

Docker-образы тегированы как `capstone-gmail-agent:latest`. Для отката к
рабочей версии — использовать `docker images` для просмотра истории
собранных образов и `docker run` с конкретным `IMAGE ID` вместо тега
`latest`, либо откатить код через `git checkout <previous_commit>` и
пересобрать образ. GitHub Actions history (вкладка Actions) позволяет
увидеть, на каком коммите CI последний раз проходил зелёным.