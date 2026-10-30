from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent

CAPABILITY_NAMES: tuple[str, ...] = ("files", "tamarin", "llm", "scheduler")


def load_capability_doc(name: str) -> str:
    path = _DIR / f"{name}.md"
    if not path.exists():
        raise KeyError(f"unknown capability doc: {name!r}")
    return path.read_text(encoding="utf-8").rstrip() + "\n"


def render_capabilities_section(names: list[str]) -> str:
    blocks = [load_capability_doc(n) for n in names]
    return "\n".join(blocks).rstrip() + "\n"
