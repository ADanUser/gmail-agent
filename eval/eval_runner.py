"""
EVAL RUNNER — "исполнитель", который реально звонит LLM.

Это файл который ты ЗАПУСКАЕШЬ (python eval_runner.py).

Что он делает:
1. Берёт каждый сценарий из eval_harness.py (через GOLDEN_DATASET)
2. Кладёт текст mock-письма прямо в промпт модели — НЕ читает реальный Gmail.
   Это специально: eval должен быть ПОВТОРЯЕМЫМ.
3. Отправляет промпт в Groq
4. Записывает какие tools (send_email) модель решила вызвать и с какими параметрами
5. НЕ отправляет письмо по-настоящему — просто подтверждает модели
   fake_result = {"status": "simulated_success"}, чтобы диалог мог продолжиться
6. Каждый LLM call и каждый tool call записывается через Tracer (tracer.py) —
   получается полный trace одного прогона: шаги, latency, токены.

В самом низу: run_eval_harness(run_agent_for_eval) — здесь "движок" из
eval_harness.py получает нашу функцию-агента и начинает гонять через неё
все сценарии один за другим, выводя scorecard.
"""

from openai import OpenAI
from dotenv import load_dotenv
import os
import json
from eval_harness import run_eval_harness
from tracer import Tracer, print_trace_summary

load_dotenv()

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY"),
    timeout=60,
    max_retries=2
)

MODEL = "llama-3.3-70b-versatile"

# Тот же send_email tool что в agent.py, но без get_email_by_id/read_emails —
# письмо уже дано в контексте, агенту не нужно его искать.
tools = [
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Отправляет ответ клиенту по email.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Email получателя"},
                    "subject": {"type": "string", "description": "Тема письма"},
                    "body": {"type": "string", "description": "Текст письма"}
                },
                "required": ["to", "subject", "body"]
            }
        }
    }
]

SYSTEM_PROMPT = """Ты AI агент службы поддержки.
Отвечай только на русском языке.

Тебе дано одно письмо клиента в контексте (или его может не быть).
Алгоритм:
1. Если есть письмо и нужно ответить — ОБЯЗАТЕЛЬНО вызови tool send_email
2. Если письмо не требует ответа (реклама/рассылка) — НЕ вызывай send_email, просто объясни почему
3. Если нет письма или не хватает данных (например email клиента) — НЕ вызывай send_email, спроси уточнение текстом

Никогда не выдумывай email получателя если он неизвестен."""


def run_agent_for_eval(scenario: dict) -> list:
    """
    Прогоняет один eval сценарий через реального агента.
    Возвращает список вызванных tool calls для проверки scorer'ом.
    Параллельно пишет полный trace через Tracer.
    """
    tracer = Tracer(trace_name=scenario["id"])

    mock_email = scenario.get("mock_email")

    if mock_email:
        email_context = f"""Письмо клиента:
От: {mock_email['from']}
Тема: {mock_email['subject']}
Текст: {mock_email['body']}"""
    else:
        email_context = "Письма нет в контексте."

    user_message = f"{email_context}\n\nЗадача: {scenario['input_message']}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message}
    ]

    tool_calls_made = []

    for step in range(5):
        tracer.start_span("llm_call")
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            temperature=0.3,
            max_tokens=512
        )
        usage = response.usage
        tracer.end_span(metadata={
            "model": MODEL,
            "tokens": {
                "prompt_tokens": usage.prompt_tokens if usage else None,
                "completion_tokens": usage.completion_tokens if usage else None,
                "total_tokens": usage.total_tokens if usage else None
            }
        })

        message = response.choices[0].message

        if not message.tool_calls:
            break

        messages.append(message)

        for tc in message.tool_calls:
            args = json.loads(tc.function.arguments)
            tool_calls_made.append({"name": tc.function.name, "args": args})

            tracer.start_span("tool_call", name=tc.function.name)
            # НЕ отправляем реально — просто подтверждаем модели что "выполнено"
            fake_result = {"status": "simulated_success"}
            tracer.end_span(metadata={"args": args, "result": fake_result})

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(fake_result, ensure_ascii=False)
            })

    trace = tracer.finish()
    print_trace_summary(trace)

    return tool_calls_made


def llm_judge(criteria: str, actual_text: str) -> tuple:
    """
    LLM-as-judge: отдельный вызов модели который оценивает СМЫСЛ ответа,
    а не точное совпадение слов.

    Возвращает (passed: bool, reason: str)
    """
    judge_prompt = f"""Ты — строгий проверяющий качества ответов службы поддержки.

Критерий который должен быть выполнен:
{criteria}

Реальный текст ответа агента:
\"\"\"
{actual_text}
\"\"\"

Оцени: соответствует ли текст критерию по СМЫСЛУ (точная формулировка не важна).
Ответь СТРОГО в формате JSON без markdown:
{{"passed": true или false, "reason": "краткое объяснение почему"}}"""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": judge_prompt}],
        temperature=0,
        max_tokens=200
    )

    raw = response.choices[0].message.content or ""
    raw = raw.strip().replace("```json", "").replace("```", "").strip()

    try:
        verdict = json.loads(raw)
        return verdict.get("passed", False), verdict.get("reason", "")
    except (json.JSONDecodeError, AttributeError):
        return False, f"Judge вернул невалидный JSON: {raw[:100]}"


if __name__ == "__main__":
    run_eval_harness(run_agent_for_eval, judge_function=llm_judge)