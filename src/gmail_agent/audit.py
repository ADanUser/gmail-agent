import json
from datetime import datetime
import os

AUDIT_LOG_FILE = "audit_log.jsonl"


def log_action(action: str, params: dict, result: dict, status: str = "success"):
    """
    Записывает одно действие агента в audit log.

    action: имя инструмента (read_emails, send_email, ...)
    params: с какими параметрами вызван
    result: что вернул инструмент (или ошибка)
    status: success / cancelled / error
    """
    entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "params": params,
        "result_summary": _summarize_result(result),
        "status": status
    }

    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _summarize_result(result) -> str:
    """Коротко суммирует результат чтобы лог не разрастался текстом писем"""
    if isinstance(result, dict):
        if "message_id" in result:
            return f"message_id={result['message_id']}"
        if "status" in result:
            return result["status"]
        return str(result)[:100]
    if isinstance(result, list):
        return f"{len(result)} items"
    return str(result)[:100]


def read_audit_log() -> list:
    """Читает весь audit log для просмотра истории"""
    if not os.path.exists(AUDIT_LOG_FILE):
        return []

    entries = []
    with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries


def print_audit_log():
    """Выводит audit log в читаемом виде"""
    entries = read_audit_log()

    if not entries:
        print("Audit log пуст")
        return

    print(f"\n{'='*60}")
    print(f"📋 AUDIT LOG ({len(entries)} записей)")
    print(f"{'='*60}")

    for e in entries:
        status_icon = "✅" if e["status"] == "success" else "❌" if e["status"] == "error" else "⏸️"
        print(f"{status_icon} [{e['timestamp']}] {e['action']}({e['params']}) → {e['result_summary']}")


if __name__ == "__main__":
    print_audit_log()