"""
TRACER — записывает полный путь одного прогона агента в единый JSON trace.

Зачем это отдельно от audit.py (Block 8):
- audit.py логирует только TOOL CALLS (что вызвано, с какими параметрами)
- tracer.py логирует ВЕСЬ путь: каждый вызов LLM (с токенами) + каждый tool call,
  всё это объединено одним trace_id в правильном порядке (span'ы)

Из роадмапа (Block 9): "Tracing: model calls, tool calls, retrieval spans,
custom events, latency/cost" — это и есть тот самый полный trace.

Использование:
    tracer = Tracer(trace_name="eval_001")
    tracer.start_span("llm_call")
    ... делаешь вызов LLM ...
    tracer.end_span(metadata={"tokens": ..., "model": ...})

    tracer.start_span("tool_call", name="send_email")
    ... вызываешь tool ...
    tracer.end_span(metadata={"result": ...})

    tracer.finish()  # сохраняет JSON файл
"""

import json
import time
import uuid
from datetime import datetime
import os

TRACES_DIR = "traces"


class Tracer:
    def __init__(self, trace_name: str = "run"):
        self.trace_id = str(uuid.uuid4())[:8]
        self.trace_name = trace_name
        self.started_at = datetime.now().isoformat()
        self.spans = []
        self._current_span = None
        self._span_start_time = None

    def start_span(self, span_type: str, name: str = ""):
        """
        span_type: 'llm_call' или 'tool_call' или 'retrieval'
        name: для tool_call — имя инструмента (например 'send_email')
        """
        self._current_span = {
            "span_type": span_type,
            "name": name,
            "started_at": datetime.now().isoformat()
        }
        self._span_start_time = time.time()

    def end_span(self, metadata: dict = None):
        """Завершает текущий span, считает latency, добавляет metadata (токены, cost, результат)"""
        if self._current_span is None:
            return

        latency = time.time() - self._span_start_time
        self._current_span["latency_sec"] = round(latency, 3)
        self._current_span["metadata"] = metadata or {}

        self.spans.append(self._current_span)
        self._current_span = None
        self._span_start_time = None

    def total_latency(self) -> float:
        return round(sum(s["latency_sec"] for s in self.spans), 3)

    def total_tokens(self) -> int:
        total = 0
        for s in self.spans:
            usage = s.get("metadata", {}).get("tokens")
            if isinstance(usage, dict):
                total += usage.get("total_tokens", 0)
        return total

    def finish(self):
        """Сохраняет trace в файл traces/{trace_id}.json"""
        os.makedirs(TRACES_DIR, exist_ok=True)

        trace = {
            "trace_id": self.trace_id,
            "trace_name": self.trace_name,
            "started_at": self.started_at,
            "finished_at": datetime.now().isoformat(),
            "total_latency_sec": self.total_latency(),
            "total_tokens": self.total_tokens(),
            "steps": len(self.spans),
            "spans": self.spans
        }

        filepath = os.path.join(TRACES_DIR, f"{self.trace_id}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(trace, f, ensure_ascii=False, indent=2)

        return trace


def print_trace_summary(trace: dict):
    """Печатает краткую сводку trace в консоль"""
    print(f"\n   🔍 TRACE [{trace['trace_id']}] {trace['trace_name']}")
    print(f"      Шагов: {trace['steps']} | Общая latency: {trace['total_latency_sec']}s | Токенов: {trace['total_tokens']}")
    for s in trace["spans"]:
        icon = "🧠" if s["span_type"] == "llm_call" else "🔧"
        label = s["name"] if s["name"] else s["span_type"]
        print(f"      {icon} {label} — {s['latency_sec']}s")