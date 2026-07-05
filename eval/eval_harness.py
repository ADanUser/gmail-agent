"""
EVAL HARNESS — "движок проверки качества" агента.

Этот файл НЕ обращается к LLM напрямую — он не знает что такое OpenRouter
или какая модель используется. Его задача — три вещи:

1. GOLDEN_DATASET — заранее придуманные тестовые сценарии:
   текст письма + что ПРАВИЛЬНО должен сделать агент (эталон).

2. score_scenario() — судья. Сравнивает что агент РЕАЛЬНО сделал
   с тем что было в эталоне, ставит ✅ passed или ❌ failed и объясняет почему.

3. run_eval_harness(agent_function) — дирижёр. Берёт каждый сценарий,
   отдаёт его в agent_function (это будет наш реальный агент из eval_runner.py),
   получает результат, прогоняет через score_scenario(), печатает scorecard
   и сохраняет JSON отчёт.

Этот файл сам по себе не запускается с реальным агентом — он принимает
ЛЮБУЮ функцию агента как параметр. Это значит eval_harness можно переиспользовать
даже если завтра поменяется модель или агент — логика проверки не изменится.

Чтобы реально прогнать тесты — запускай eval_runner.py, не этот файл.

ВАЖНО про LLM-as-judge:
Раньше проверка была "есть ли точное слово в тексте" (exact match) — слишком
строго, ведь "обрабатывается" и "в процессе обработки" значат одно и то же,
но exact match считает второе ошибкой.

Теперь вместо точного слова в expected_criteria пишем словами КАКОЙ смысл
должен быть в ответе, а отдельная LLM (judge) читает реальный ответ агента
и оценивает — соответствует смыслу или нет. Это и называется LLM-as-judge,
стандартная практика в индустрии (LangSmith, Phoenix, OpenAI evals все так делают).
"""

import json
import time
from datetime import datetime

# ============================================================
# GOLDEN DATASET — тестовые сценарии
# ============================================================

GOLDEN_DATASET = [
    {
        "id": "eval_001",
        "scenario": "Клиент пишет про возврат товара",
        "input_message": "Прочитай письмо и ответь клиенту что возврат возможен в течение 30 дней",
        "mock_email": {
            "from": "client1@example.com",
            "subject": "Хочу вернуть товар",
            "body": "Здравствуйте, хочу вернуть заказ #1234, не подошёл размер."
        },
        "expected_tool": "send_email",
        "expected_criteria": "Ответ должен сообщать клиенту что возврат товара возможен в течение 30 дней",
        "category": "returns"
    },
    {
        "id": "eval_002",
        "scenario": "Клиент спрашивает статус заказа",
        "input_message": "Прочитай письмо и ответь что заказ обрабатывается",
        "mock_email": {
            "from": "client2@example.com",
            "subject": "Где мой заказ?",
            "body": "Добрый день, заказ #5678 уже неделю не приходит, в чём дело?"
        },
        "expected_tool": "send_email",
        "expected_criteria": "Ответ должен сообщать клиенту что его заказ сейчас обрабатывается/находится в процессе выполнения (формулировка может отличаться, важен смысл)",
        "category": "order_status"
    },
    {
        "id": "eval_003",
        "scenario": "Письмо не требует ответа (рекламная рассылка)",
        "input_message": "Прочитай письмо и реши нужно ли отвечать. Это рекламная рассылка от сервиса, ответ не нужен",
        "mock_email": {
            "from": "noreply@newsletter.com",
            "subject": "Скидка 50% на всё!",
            "body": "Не пропустите распродажу!"
        },
        "expected_tool": None,  # агент НЕ должен вызывать send_email
        "category": "no_action_needed"
    },
    {
        "id": "eval_004",
        "scenario": "Клиент пишет жалобу",
        "input_message": "Прочитай письмо и ответь извинением, передай что разбираемся",
        "mock_email": {
            "from": "client3@example.com",
            "subject": "Очень недоволен сервисом",
            "body": "Это уже третий раз когда заказ приходит повреждённым!"
        },
        "expected_tool": "send_email",
        "expected_criteria": "Ответ должен содержать извинение перед клиентом и сообщать что компания разбирается в ситуации",
        "category": "complaint"
    },
    {
        "id": "eval_005",
        "scenario": "Запрос без указания email — агент должен попросить уточнение, а не выдумывать адрес",
        "input_message": "Ответь клиенту по поводу возврата",
        "mock_email": None,  # нет писем для контекста
        "expected_tool": None,
        "category": "missing_context"
    },
]


# ============================================================
# SCORER — проверка результата
# ============================================================

def score_scenario(scenario: dict, agent_tool_calls: list, judge_function=None) -> dict:
    """
    Сравнивает что сделал агент с ожидаемым поведением.
    agent_tool_calls — список вызванных tools в формате [{"name": ..., "args": {...}}]
    judge_function — функция LLM-judge: принимает (criteria, actual_text) и возвращает (bool, reason)
    """
    expected_tool = scenario.get("expected_tool")

    # Случай 1: ожидаем что tool НЕ должен вызываться
    if expected_tool is None:
        send_email_called = any(tc["name"] == "send_email" for tc in agent_tool_calls)
        passed = not send_email_called
        reason = "Агент корректно не отправил письмо" if passed else "Агент отправил письмо когда не должен был"
        return {"passed": passed, "reason": reason}

    # Случай 2: ожидаем конкретный tool
    matching_calls = [tc for tc in agent_tool_calls if tc["name"] == expected_tool]

    if not matching_calls:
        return {"passed": False, "reason": f"Агент не вызвал ожидаемый tool '{expected_tool}'"}

    # Проверка смысла через LLM-judge вместо точного совпадения слов
    expected_criteria = scenario.get("expected_criteria")
    if expected_criteria and judge_function:
        actual_body = matching_calls[0]["args"].get("body", "")
        judge_passed, judge_reason = judge_function(expected_criteria, actual_body)
        return {"passed": judge_passed, "reason": f"[LLM-judge] {judge_reason}"}

    return {"passed": True, "reason": "Tool вызван корректно"}


# ============================================================
# RUNNER — прогон всех сценариев
# ============================================================

def run_eval_harness(agent_function, judge_function=None):
    """
    agent_function — функция которая принимает (scenario) и возвращает list tool_calls
    judge_function — LLM-judge функция для смысловой проверки текстовых ответов
    """
    print(f"\n{'='*60}")
    print(f"🧪 EVAL HARNESS — {len(GOLDEN_DATASET)} сценариев")
    print(f"{'='*60}\n")

    results = []

    for scenario in GOLDEN_DATASET:
        start = time.time()
        tool_calls = agent_function(scenario)
        latency = time.time() - start

        score = score_scenario(scenario, tool_calls, judge_function=judge_function)
        score["latency_sec"] = round(latency, 2)
        score["scenario_id"] = scenario["id"]
        score["category"] = scenario["category"]
        score["actual_tool_calls"] = tool_calls  # сохраняем что реально сделал агент — для отладки

        results.append(score)

        icon = "✅" if score["passed"] else "❌"
        print(f"{icon} [{scenario['id']}] {scenario['scenario']}")
        print(f"   {score['reason']} ({score['latency_sec']}s)\n")

    print_scorecard(results)
    save_report(results)

    return results


def print_scorecard(results: list):
    total = len(results)
    passed = sum(1 for r in results if r["passed"])

    print(f"{'='*60}")
    print(f"📊 SCORECARD")
    print(f"{'='*60}")
    print(f"Пройдено: {passed}/{total} ({round(passed/total*100)}%)")

    avg_latency = sum(r["latency_sec"] for r in results) / total
    print(f"Средняя latency: {round(avg_latency, 2)}s")

    print(f"\nПо категориям:")
    categories = {}
    for r in results:
        cat = r["category"]
        categories.setdefault(cat, {"passed": 0, "total": 0})
        categories[cat]["total"] += 1
        if r["passed"]:
            categories[cat]["passed"] += 1

    for cat, stats in categories.items():
        print(f"  {cat}: {stats['passed']}/{stats['total']}")

    save_scorecard_md(results, total, passed, avg_latency, categories)


def save_scorecard_md(results: list, total: int, passed: int, avg_latency: float, categories: dict, filename: str = "scorecard.md"):
    """Сохраняет scorecard в читаемый markdown файл — удобно прикладывать в портфолио/README"""
    lines = []
    lines.append("# Eval Scorecard\n")
    lines.append(f"_Сгенерировано: {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n")
    lines.append(f"## Итог\n")
    lines.append(f"- **Pass rate:** {passed}/{total} ({round(passed/total*100)}%)")
    lines.append(f"- **Средняя latency:** {round(avg_latency, 2)}s\n")

    lines.append("## По категориям\n")
    lines.append("| Категория | Пройдено |")
    lines.append("|---|---|")
    for cat, stats in categories.items():
        lines.append(f"| {cat} | {stats['passed']}/{stats['total']} |")

    lines.append("\n## Детали по сценариям\n")
    lines.append("| ID | Сценарий | Статус | Latency | Причина |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        icon = "✅" if r["passed"] else "❌"
        lines.append(f"| {r['scenario_id']} | {r['category']} | {icon} | {r['latency_sec']}s | {r['reason']} |")

    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n💾 Scorecard сохранён в {filename}")


def save_report(results: list, filename: str = "eval_report.json"):
    report = {
        "timestamp": datetime.now().isoformat(),
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "results": results
    }
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Отчёт сохранён в {filename}")


if __name__ == "__main__":
    print("Это библиотека eval harness.")
    print("Запусти eval_runner.py чтобы прогнать реального агента через эти сценарии.")