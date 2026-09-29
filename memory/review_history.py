"""Read-only, allowlisted audit history. Never changes scores or account state."""
import json
from pathlib import Path


def review_history(memory_path, lessons_path):
    out = {"archived": [], "observations": [], "unavailable": False}
    specs = [
        ("archived", Path(memory_path).with_name("memory_store_superseded.json") if memory_path else None,
         ("target_date", "run_date", "run_at", "direction", "superseded_by")),
        ("observations", Path(lessons_path) if lessons_path else None,
         ("target_date", "reviewed_record", "direction", "actual_direction", "correct", "note")),
    ]
    for kind, path, fields in specs:
        if path is None or not path.exists():
            continue
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                raise ValueError("Expected a list")
            rows = [r for r in rows if isinstance(r, dict)
                    and (kind != "observations" or r.get("observational") is True)]
            out[kind] = [{k: r[k] for k in fields if k in r}
                         for r in sorted(rows, key=lambda r: str(r.get("target_date", "")), reverse=True)[:30]]
        except (OSError, ValueError, UnicodeError):
            out["unavailable"] = True
    return out
