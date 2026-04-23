import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_audit_event(log_path: str, event_type: str, payload: dict[str, Any]) -> None:
    file_path = Path(log_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "payload": payload,
    }
    with file_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
