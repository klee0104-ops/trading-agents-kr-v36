"""
비밀값 스캐너 + 안전한 배포용 zip 생성.

- scan_for_secrets(): 프로젝트 파일에서 API 키로 보이는 문자열을 찾는다.
  (.env는 원래 키를 담는 파일이라 "경고"로만 따로 표시하고, 그 외 파일에서
   발견되면 "위험"으로 분류한다.)
- build_release_zip(): .env / venv / DB / 예측 기록 / 캐시 / .claude 등을 뺀
  공유용 zip을 만들고, 만든 zip을 다시 스캔해서 키가 없을 때만 통과시킨다.
"""
from __future__ import annotations

import fnmatch
import zipfile
from dataclasses import dataclass
from pathlib import Path

from security.secret_store import PROJECT_ROOT, SECRET_PATTERNS, mask

# 배포/공유 zip에서 항상 제외할 것들
EXCLUDE_DIRS = {"venv", ".venv", "__pycache__", ".pytest_cache", ".git", ".claude",
                ".idea", ".vscode", "release", "node_modules", "site", ".review-backups"}
EXCLUDE_FILES = [".env", ".env.*", "*.db", "*.sqlite", "memory_store.json", "memory_store_superseded.json", "demo_memory.json", "last_outcome.json",
                 "실행로그.txt", "진단결과.txt", "paper_account.json", "paper_portfolio.json", "paper_portfolio.json.lock", "paper_portfolio.pending", "research_reports.json", "research_reports.json.lock", "research_reports.pending", "long_research.json", "long_research.json.lock", "long_research.pending", "lessons.json",
                 "backtest_results*.json", "*_log.txt", "아침요약.txt", "*.bak", "*.pdf", "flow_debug.html",
                 "*.pyc", "*.log", "*.zip", "*.pem", "*.key"]
ALLOW_FILES = [".env.example"]

TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".bat", ".cmd", ".ps1", ".sh", ".toml",
                 ".cfg", ".ini", ".yaml", ".yml", ".js", ".html", ".css", ".example", ""}


@dataclass
class Finding:
    path: str
    line: int
    masked: str
    severity: str  # "위험" | "경고"


def _excluded(rel: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts[:-1]):
        return True
    name = rel.name
    if any(fnmatch.fnmatch(name, p) for p in ALLOW_FILES):
        return False
    return any(fnmatch.fnmatch(name, p) for p in EXCLUDE_FILES)


def _scan_text(text: str, rel: str, severity: str) -> list[Finding]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat in SECRET_PATTERNS:
            for m in pat.finditer(line):
                out.append(Finding(rel, i, mask(m.group(0)), severity))
    return out


def scan_for_secrets(root: Path = PROJECT_ROOT, include_dotenv: bool = True) -> list[Finding]:
    import os
    findings: list[Finding] = []
    skip = {"venv", ".venv", "__pycache__", ".git", "node_modules", ".pytest_cache"}
    paths = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in skip]  # venv 등은 아예 들어가지 않는다 (속도)
        paths += [Path(dp) / f for f in fns]
    for p in paths:
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        is_dotenv = rel.name == ".env" or (rel.name.startswith(".env.") and rel.name != ".env.example")
        if is_dotenv and not include_dotenv:
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES and not is_dotenv and rel.name != ".env.example":
            continue
        if p.stat().st_size > 5_000_000:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        findings += _scan_text(text, str(rel), "경고" if is_dotenv else "위험")
    return findings


def build_release_zip(out_path: Path, root: Path = PROJECT_ROOT) -> dict:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    added, skipped = [], []
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        import os
        all_files = []
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in EXCLUDE_DIRS]
            all_files += [Path(dp) / f for f in fns]
        for p in sorted(all_files):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if p.resolve() == out_path.resolve() or _excluded(rel):
                skipped.append(str(rel))
                continue
            zf.write(p, arcname=str(Path(root.name) / rel))
            added.append(str(rel))

    # 만든 zip을 다시 열어서 키가 새지 않았는지 검사 (이중 확인)
    leaks: list[Finding] = []
    with zipfile.ZipFile(out_path) as zf:
        for n in zf.namelist():
            if n.endswith("/.env") or "/.env." in n and not n.endswith(".env.example"):
                leaks.append(Finding(n, 0, "(.env 파일 포함됨)", "위험"))
                continue
            try:
                text = zf.read(n).decode("utf-8", errors="ignore")
            except Exception:
                continue
            leaks += _scan_text(text, n, "위험")
    if leaks:
        out_path.unlink(missing_ok=True)
    return {"zip": str(out_path), "files": len(added), "skipped": len(skipped),
            "leaks": [f.__dict__ for f in leaks], "ok": not leaks}
