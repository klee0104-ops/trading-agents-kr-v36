"""(V3.22) 1회용 데이터 정리. 실행 전 .review-backups/learning-fix-* 에 원본 보관됨. LLM·네트워크 미사용.
1) 포지션 없이 채점돼 수익률 0.0 으로 저장된 옛 기록 → 방향 100% 규칙으로 재계산
2) 2026-09-29 08:59(개장 전) 실행이 옛 컷오프(08:50) 때문에 9/30 대상으로 저장된 것을 9/29 대상으로 바로잡고,
   9/28 09:32(개장 후) 실행의 9/29 대상 기록은 memory_store_superseded.json 으로 옮김(통계 제외, 관찰 복기용 보관)
3) 옮긴 9/28 기록에 복기용 요약(outcome_digest)을 9/28 공유 페이지 백업에서 복원
되돌리기: 백업 폴더의 memory_store.json / last_outcome.json 을 덮어쓰고 memory_store_superseded.json 삭제."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory.store import MemoryStore

m = MemoryStore("memory_store.json")
print("legacy returns recomputed:", m.recompute_legacy_returns())
data = json.load(open("memory_store.json", encoding="utf-8"))
old, new = data.get("2026-09-29"), data.get("2026-09-30")
if old and new and new.get("run_date") == "2026-09-29" and old.get("run_date") == "2026-09-28" and not old.get("verified"):
    try:
        h = open(".review-backups/empty-account-20260928-181445/site/index.html", encoding="utf-8").read()
        d = json.loads(re.search(r'<script type="application/json" id="ta-data">(.*?)</script>', h, re.S).group(1).replace("\\u003c", "<"))
        pred = d.get("prediction") or {}
        old["outcome_digest"] = {
            "rule_summary": "(9/28 09:32 실행, 미국 9/25 종가 기준) " + " / ".join(pred.get("reasoning") or [])[:500],
            "quant_text": "", "analysts": {},
            "debate": [{"stance": t.get("stance"), "round": t.get("round"), "argument": str(t.get("argument", ""))[:220],
                        "evidence": (t.get("evidence") or [])[:3]} for t in d.get("debate") or []][:6],
            "qa_rejected": [], "moderation": "",
            "risk": f"{(d.get('risk') or {}).get('risk_level')} / {str((d.get('risk') or {}).get('summary', ''))[:250]}",
            "prediction": {"reasoning": [str(x)[:160] for x in (pred.get("reasoning") or [])][:4]},
        }
    except Exception as e:  # noqa: BLE001
        print("digest restore skipped:", e)
    old["superseded_by"] = "2026-09-29 08:59 실행(개장 전)"
    old["run_at"] = old.get("run_at") or "2026-09-28T09:32+09:00"
    sup_path = "memory_store_superseded.json"
    sup = json.load(open(sup_path, encoding="utf-8")) if os.path.exists(sup_path) else []
    sup.append(old)
    json.dump(sup, open(sup_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    new["target_date"] = "2026-09-29"; new["score_after_date"] = "2026-09-28"
    for s in new.get("stocks", []):
        s["predicted_date"] = "2026-09-29"
    for a in new.get("agent_predictions", []):
        a["predicted_date"] = "2026-09-29"
    del data["2026-09-30"]; data["2026-09-29"] = new
    json.dump(data, open("memory_store.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    lo = json.load(open("last_outcome.json", encoding="utf-8"))
    lo["outcome"]["target_date"] = "2026-09-29"
    json.dump(lo, open("last_outcome.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("re-keyed 2026-09-30 -> 2026-09-29, old 9/28 record superseded")
else:
    print("re-key: nothing to do")
