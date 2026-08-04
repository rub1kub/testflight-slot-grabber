from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from .models import utc_now


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def save(self, data: Dict[str, Any], durable: bool = True) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(data)
        data["updated_at"] = utc_now()
        fd, temporary = tempfile.mkstemp(prefix="state-", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                if durable:
                    os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
