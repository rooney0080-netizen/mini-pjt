"""shared/tracer.py - LLM·도구 호출을 JSONL로 기록하는 콜백 (교육 실습 local_tracer.py 기반)

mask=True 이면 기록 직전에 전화번호·이메일을 마스킹한다 (v2). v1 기준선은 mask=False.
"""
import json
import threading
import time
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler

from src.shared.pii import mask_obj
from src.shared.messages import get_text


class FileTracer(BaseCallbackHandler):
    def __init__(self, path, mask: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.mask = mask
        self._starts = {}
        self._lock = threading.Lock()

    def _write(self, record: dict):
        record["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if self.mask:
            record = mask_obj(record)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with self._lock:      # 병렬 도구 호출 콜백이 동시에 쓰면 줄이 섞이므로 직렬화
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)

    def on_chat_model_start(self, serialized, messages, *, run_id, **kw):
        self._starts[run_id] = time.time()
        last = [get_text(m)[:500] for batch in messages for m in batch][-2:]
        self._write({"event": "llm_start", "run_id": str(run_id), "input_tail": last})

    def on_llm_end(self, response, *, run_id, **kw):
        elapsed = time.time() - self._starts.pop(run_id, time.time())
        gen = response.generations[0][0]
        msg = getattr(gen, "message", None)
        calls = [{"name": c["name"], "args": c["args"]} for c in (getattr(msg, "tool_calls", None) or [])]
        self._write({"event": "llm_end", "run_id": str(run_id), "output": gen.text[:1000],
                     "tool_calls": calls, "latency_s": round(elapsed, 2),
                     "usage": getattr(msg, "usage_metadata", None) or {}})

    def on_tool_start(self, serialized, input_str, *, run_id, **kw):
        self._starts[run_id] = time.time()
        name = (serialized or {}).get("name") or kw.get("name")
        self._write({"event": "tool_start", "run_id": str(run_id), "tool": name, "input": str(input_str)[:1000]})

    def on_tool_end(self, output, *, run_id, **kw):
        elapsed = time.time() - self._starts.pop(run_id, time.time())
        self._write({"event": "tool_end", "run_id": str(run_id),
                     "output": str(getattr(output, "content", output))[:1500], "latency_s": round(elapsed, 2)})

    def on_tool_error(self, error, *, run_id, **kw):
        self._write({"event": "tool_error", "run_id": str(run_id), "error": str(error)[:500]})

    def on_llm_error(self, error, *, run_id, **kw):
        self._write({"event": "llm_error", "run_id": str(run_id), "error": str(error)[:500]})

    def log(self, event: str, **data):
        """그래프 노드에서 단계 이벤트를 직접 남길 때 사용"""
        self._write({"event": event, **data})
