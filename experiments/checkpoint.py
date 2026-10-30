from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

class CheckpointManager:
    """
    The 'Black Box' of MASTA experiments. 
    Responsible for tracking progress and ensuring we never repeat finished work.
    """
    def __init__(self, checkpoint_path: Path) -> None:
        self.path = checkpoint_path
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not isinstance(data, dict): return {"completed_protocols": [], "results": {}}
                    if "completed_protocols" not in data: data["completed_protocols"] = []
                    if "results" not in data: data["results"] = {}
                    return data
            except:
                return {"completed_protocols": [], "results": {}}
        return {"completed_protocols": [], "results": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def is_completed(self, protocol_name: str) -> bool:
        """Checks if a protocol within the CURRENT system has been finished."""
        return protocol_name in self._data.get("completed_protocols", [])

    def mark_completed(self, protocol_name: str, result: Any = None) -> None:
        """Marks a protocol as done and persists its result immediately."""
        if protocol_name not in self._data["completed_protocols"]:
            self._data["completed_protocols"].append(protocol_name)
        if result:
            # If result is a Pydantic model, convert to dict
            if hasattr(result, "model_dump"):
                self._data["results"][protocol_name] = result.model_dump(mode="json")
            else:
                self._data["results"][protocol_name] = result
        self.save()

    def get_result(self, protocol_name: str) -> Any | None:
        return self._data.get("results", {}).get(protocol_name)

    def get_all_results_list(self) -> List[Any]:
        """Returns the list of stored results in order of completion."""
        return list(self._data.get("results", {}).values())

    @property
    def completed_count(self) -> int:
        return len(self._data["completed_protocols"])
