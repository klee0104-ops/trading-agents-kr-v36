"""
(V3.21) 가상 계좌(모의투자) — 실제 돈 없이 프로그램 판단대로 굴렸을 때의 계좌를 기록한다.

규칙
- 시작 자금 PAPER_INITIAL_CASH(기본 1,000만 원). 매매 대상은 KODEX 200(롱), KODEX 인버스(쇼트) 두 ETF만.
- 매일 아침 분석의 포지션(-1~+1)이 "오늘 목표 비중". 그날 ETF 에 전량 리밸런싱(살 건 사고 팔 건 팔고), **종가**로 평가.
  체결가(V3.22): 분석이 08:40 이전에 끝났으면 장전 시간외 종가매매(08:30~08:40, 전일 종가로 체결)를 가정해 **전일 종가**,
  그 이후면 정규장 **시가**. 전일 종가 체결이면 밤사이 미국 흐름이 반영된 시가 갭까지 수익에 들어가고, 시가 체결이면 장중 움직임만 들어간다.
  다음 날 아침 다시 목표 비중으로 조정. 포지션 0이면 전부 현금.
- 비용: 매수·매도 각 0.015% 수수료 + 0.05% 슬리피지 가정(ETF는 거래세 없음). 정수 주만 체결.
- 이 계좌는 채점(`verify`)이 그날 ETF 시가/종가를 받았을 때 한 번만 체결·평가한다(중복 방지).
- 저장: paper_account.json (현금, 보유, 체결 내역, 일별 평가). LLM 미사용.
"""
from __future__ import annotations
import json
import os
from typing import Optional

FEE_RATE = 0.00015      # 편도 수수료
SLIPPAGE = 0.0005       # 편도 슬리피지 가정
DEFAULT_PATH = "./paper_account.json"


class PaperAccount:
    def __init__(self, path: Optional[str] = DEFAULT_PATH, initial_cash: float = 10_000_000.0):
        self.path = path
        self.initial_cash = float(initial_cash)
        self._mem = None
        if path and not os.path.exists(path):
            self._write(self._fresh())
        elif path is None:
            self._mem = self._fresh()

    def _fresh(self) -> dict:
        return {"initial_cash": self.initial_cash, "cash": self.initial_cash,
                "holdings": {}, "trades": [], "daily": []}

    def _read(self) -> dict:
        if self.path is None:
            return self._mem
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, d: dict) -> None:
        if self.path is None:
            self._mem = d
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def state(self) -> dict:
        return self._read()

    def already_settled(self, session_date: str) -> bool:
        return any(x.get("date") == session_date for x in self._read().get("daily", []))

    def settle_day(self, session_date: str, target_position: float, etf: dict, note: str = "",
                   entry: str = "open") -> Optional[dict]:
        """session_date 시가에 목표 비중으로 리밸런싱 → 종가 평가. etf = fetch_etf_ohlc() 결과.
        롱 비중 = max(pos,0), 쇼트 비중 = max(-pos,0) (인버스 ETF 매수)."""
        d = self._read()
        if self.already_settled(session_date):
            return None
        # (V3.25) 순서 보장: 이미 더 늦은 날을 체결했으면 과거 날짜는 끼워 넣지 않는다(보유·현금이 뒤섞임)
        done = [x.get("date") for x in d.get("daily", []) if x.get("date")]
        if done and str(session_date) < max(done):
            print(f"[가상계좌] {session_date} 는 이미 체결한 {max(done)} 보다 이전이라 건너뜁니다 (순서 보호).")
            return None
        long_, short_ = etf.get("long"), etf.get("short")
        if not long_ or not short_:
            return None
        import math
        for q in (long_, short_):  # (V3.25) 가격이 없거나 0 이하/NaN 이면 체결하지 않는다
            for k in ("open", "close"):
                v = q.get(k)
                if not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                    print(f"[가상계좌] {session_date} {q.get('ticker')} {k} 가격 이상({v}) → 체결 보류")
                    return None
        try:
            tp = float(target_position or 0.0)
        except (TypeError, ValueError):
            tp = 0.0
        pos = max(-1.0, min(1.0, tp if math.isfinite(tp) else 0.0))
        # (V3.22) 체결가: entry="prev_close" = 장전 시간외 종가매매(08:30~08:40, 전일 종가 체결) — 08:40 이전에 분석이 끝난 날
        #          entry="open" = 정규장 시가 — 그 이후에 분석이 끝난 날
        px_key = "prev_close" if entry == "prev_close" and long_.get("prev_close") and short_.get("prev_close") else "open"
        opens = {long_["ticker"]: long_[px_key], short_["ticker"]: short_[px_key]}
        closes = {long_["ticker"]: long_["close"], short_["ticker"]: short_["close"]}
        # 시가 기준 총자산
        equity_open = d["cash"] + sum(q * opens.get(t, 0.0) for t, q in d["holdings"].items())
        targets = {long_["ticker"]: equity_open * max(pos, 0.0), short_["ticker"]: equity_open * max(-pos, 0.0)}
        trades = []
        for tk, target_value in targets.items():
            px = opens[tk]
            cur_qty = int(d["holdings"].get(tk, 0))
            want_qty = int(target_value // px) if px > 0 else 0
            diff = want_qty - cur_qty
            if diff == 0:
                continue
            side = "buy" if diff > 0 else "sell"
            fill = px * (1 + SLIPPAGE) if side == "buy" else px * (1 - SLIPPAGE)
            gross = abs(diff) * fill
            fee = gross * FEE_RATE
            if side == "buy":
                if gross + fee > d["cash"]:  # 현금 부족이면 살 수 있는 만큼만
                    diff = int(d["cash"] // (fill * (1 + FEE_RATE)))
                    if diff <= 0:
                        continue
                    gross = diff * fill; fee = gross * FEE_RATE
                d["cash"] -= gross + fee
                d["holdings"][tk] = cur_qty + diff
            else:
                d["cash"] += gross - fee
                d["holdings"][tk] = cur_qty + diff  # diff 음수
            if d["holdings"].get(tk) == 0:
                d["holdings"].pop(tk, None)
            trades.append({"date": session_date, "ticker": tk, "side": side, "qty": abs(diff),
                           "price": round(fill, 2), "fee": round(fee, 0)})
        d["trades"] += trades
        equity_close = d["cash"] + sum(q * closes.get(t, 0.0) for t, q in d["holdings"].items())
        prev_equity = d["daily"][-1]["equity"] if d["daily"] else d["initial_cash"]
        day = {"date": session_date, "target_position": pos, "equity": round(equity_close, 0),
               "cash": round(d["cash"], 0), "holdings": dict(d["holdings"]),
               "day_return_pct": round((equity_close / prev_equity - 1) * 100, 3) if prev_equity else 0.0,
               "total_return_pct": round((equity_close / d["initial_cash"] - 1) * 100, 3),
               "trades": len(trades), "note": note, "entry": "장전 시간외(전일 종가)" if px_key == "prev_close" else "시가"}
        d["daily"].append(day)
        self._write(d)
        return day

    def summary(self) -> dict:
        d = self._read()
        daily = d.get("daily", [])
        if not daily:
            return {"days": 0, "initial_cash": d["initial_cash"], "equity": d["cash"],
                    "cash": d["cash"], "holdings": dict(d.get("holdings", {})),
                    "total_return_pct": 0.0, "pnl": 0.0, "trades": 0, "fees": 0.0,
                    "sharpe_annual": None, "max_drawdown_pct": None, "win_rate": None,
                    "equity_curve": [], "recent_trades": [], "recent_days": []}
        from execution.position import performance
        perf = performance([x["day_return_pct"] for x in daily], positions=[x["target_position"] for x in daily])
        peak = max(x["equity"] for x in daily)
        return {"days": len(daily), "initial_cash": d["initial_cash"], "equity": daily[-1]["equity"],
                "cash": daily[-1]["cash"], "holdings": daily[-1]["holdings"],
                "total_return_pct": daily[-1]["total_return_pct"], "pnl": round(daily[-1]["equity"] - d["initial_cash"], 0),
                "peak_equity": peak, "trades": len(d.get("trades", [])),
                "fees": round(sum(t.get("fee", 0) for t in d.get("trades", [])), 0),
                "sharpe_annual": perf.get("sharpe_annual"), "max_drawdown_pct": perf.get("max_drawdown_pct"),
                "win_rate": perf.get("win_rate"), "equity_curve": [{"date": x["date"], "equity": x["equity"]} for x in daily],
                "recent_trades": d.get("trades", [])[-20:][::-1], "recent_days": daily[-30:][::-1]}
