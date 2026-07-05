from openai import OpenAI
from dotenv import load_dotenv
from gmail_tools import read_emails, send_email, get_email_by_id
from audit import log_action
from guardrails import validate_send_email, email_rate_limiter
import os
import json

load_dotenv()

# client = OpenAI(
#     base_url="https://openrouter.ai/api/v1",
#     api_key=os.getenv("OPENROUTER_API_KEY"),
#     timeout=60,
#     max_retries=2
# )
# Fallback option, hit rate limits on free tier

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY"),
    timeout=60,
    max_retries=2
)

# Tools для агента
tools = [
    {
        "type": "function",
        "function": {
            "name": "read_emails",
            "description": "Читает последние письма из входящих Gmail",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "description": "Количество писем для чтения (по умолчанию 5)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_by_id",
            "description": "Получает одно письмо по ID для детального анализа",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_id": {
                        "type": "string",
                        "description": "ID письма"
                    }
                },
                "required": ["email_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Отправляет ответ клиенту по email. Используй только после подтверждения пользователя.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Email получателя"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Тема письма"
                    },
                    "body": {
                        "type": "string",
                        "description": "Текст письма"
                    }
                },
                "required": ["to", "subject", "body"]
            }
        }
    }
]


def run_agent(user_message: str, dry_run: bool = True):
    """
    dry_run=True  — агент показывает что хочет отправить, но не отправляет
    dry_run=False — агент реально отправляет письмо
    """
    print(f"\n{'='*60}")
    print(f"🤖 Support Agent")
    print(f"📝 Задача: {user_message}")
    print(f"{'='*60}")

    messages = [
        {
    "role": "system",
    "content": """Ты AI агент службы поддержки с доступом к Gmail.
    Отвечай только на русском языке.

    Алгоритм работы — строго по шагам:
    1. Если нужно прочитать письма — вызови read_emails
    2. Проверь условие из задачи (например, "письмо от X"), сверяя реальное поле "from" в каждом письме
    3. Если условие НЕ выполнено (нет письма от нужного отправителя) — НЕ вызывай send_email. 
    Дай финальный текстовый ответ: "Письмо от [условие] не найдено, действие не требуется."
    4. Если условие выполнено — сначала вызови get_email_by_id, чтобы получить точный адрес отправителя,
    затем ОБЯЗАТЕЛЬНО вызови tool send_email с этим реальным email адресом получателя
    5. НИКОГДА не вызывай send_email с адресом, который не является реальным email из письма (например, "hh.ru" без @ — это невалидный адрес)
    6. НИКОГДА не пиши текст письма как обычный ответ — только через вызов tool send_email, и только когда условие подтверждено

    Запрещено: писать "вот черновик письма" текстом. Запрещено: вызывать send_email без предварительного get_email_by_id."""
        },
        {"role": "user", "content": user_message}
    ]

    pending_send = None  # Сохраняем pending отправку для dry-run
    last_opened_email_sender = None  # Адрес отправителя последнего открытого письма

    for step in range(10):
        response = client.chat.completions.create(
            # model="openrouter/free",
            model="llama-3.3-70b-versatile",
            messages=messages,
            tools=tools,
            temperature=0.3,
            max_tokens=1024
        )

        message = response.choices[0].message

        # Если нет tool calls — агент дал финальный ответ
        if not message.tool_calls:
            print(f"\n🤖 Агент: {message.content}")
            break

        messages.append(message)

        for tool_call in message.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            print(f"\n   🔧 {name}({args})")

            # Обрабатываем каждый tool
            if name == "read_emails":
                result = read_emails(**args)
                print(f"   📧 Получено писем: {len(result)}")
                log_action(name, args, result, status="success")

            elif name == "get_email_by_id":
                result = get_email_by_id(**args)
                last_opened_email_sender = result.get('from', '')
                print(f"   📨 Письмо от: {result.get('from', '?')}")
                log_action(name, args, result, status="success")

            elif name == "send_email":
                # Guardrail 1: нельзя отправлять письмо, не открыв его через get_email_by_id
                if not last_opened_email_sender:
                    result = {"status": "blocked", "reason": "Попытка отправить письмо без предварительного открытия через get_email_by_id"}
                    print(f"   🚫 Guardrail: {result['reason']}")
                    log_action(name, args, result, status="blocked")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False)
                    })
                    continue

                # Guardrail 2: полная проверка из guardrails.py (rate limit, совпадение адреса, домены, injection)
                check = validate_send_email(
                    to=args.get("to", ""),
                    original_sender=last_opened_email_sender,
                    body=args.get("body", "")
                )
                if not check["allowed"]:
                    result = {"status": "blocked", "reason": check["reason"]}
                    print(f"   🚫 Guardrail: {check['reason']}")
                    log_action(name, args, result, status="blocked")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False)
                    })
                    continue

                if dry_run:
                    # Dry-run: показываем черновик, не отправляем
                    pending_send = args
                    print(f"\n   📤 [DRY-RUN] Агент хочет отправить письмо:")
                    print(f"   Кому: {args['to']}")
                    print(f"   Тема: {args['subject']}")
                    print(f"   Текст:\n{args['body']}")

                    # Спрашиваем подтверждение
                    confirm = input("\n   ✅ Отправить письмо? (да/нет): ").strip().lower()

                    if confirm in ['да', 'yes', 'y', 'д']:
                        result = send_email(**args)
                        print(f"   ✅ Письмо отправлено! ID: {result['message_id']}")
                        log_action(name, args, result, status="success")
                    else:
                        result = {"status": "cancelled", "reason": "Пользователь отменил отправку"}
                        print(f"   ❌ Отправка отменена")
                        log_action(name, args, result, status="cancelled")
                else:
                    result = send_email(**args)
                    print(f"   ✅ Письмо отправлено! ID: {result['message_id']}")
                    log_action(name, args, result, status="success")

            else:
                result = {"error": "неизвестный инструмент"}
                log_action(name, args, result, status="error")

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result, ensure_ascii=False)
            })


if __name__ == "__main__":
    run_agent("Прочитай последние 20 писем. Если есть письмо от hh.ru — ответь на него письмом с текстом 'Спасибо, получил информацию' на тот же email адрес", dry_run=True)