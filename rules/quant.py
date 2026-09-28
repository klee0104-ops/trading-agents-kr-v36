"""
(V3.14) 정량 지표 모듈 — 금융권에서 표준으로 쓰는 공개 공식만 코드로 계산한다. LLM 미사용.

공식 출처
- RSI(14): Wilder(1978) New Concepts in Technical Trading Systems
- MACD(12,26,9): Appel(1979)
- 볼린저 밴드 %B(20, 2σ): Bollinger(2001)
- 이동평균 배열(20/60/120): 국내 실무 관행(1개월/3개월/6개월)
- 실현 변동성(20일, 연율화 √252): 표준 정의
- ATR(14): Wilder(1978)
- 12-1 모멘텀: Jegadeesh & Titman(1993) — 최근 1개월 제외 12개월 수익률
- 단기 반전(1주 수익률): Lo & MacKinlay(1990), Jegadeesh(1990)
- 52주 고점 대비 거리: George & Hwang(2004)
- 외국인 순매수 z-score(5일): 표준 z-점수

용도
1) 분석가·토론·트레이더 프롬프트에 "사실"로 주입 (rules.engine.build_context_text 뒤에 붙임)
2) QA 근거 검증: 토론 근거의 정성 주장("과매수", "골든크로스", "외국인 매수 전환" 등)을 지표와 대조
3) 포지션 크기: 실현 변동성 → execution.position 의 변동성 타게팅 입력
"""
from __future__ import annotations
import math
import re
from typing import Optional, Sequence

RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
BB_HIGH = 1.0   # %B > 1 : 상단 밴드 위
BB_LOW = 0.0    # %B < 0 : 하단 밴드 아래
VOL_HIGH_PCT = 25.0   # 연율 실현변동성(%) 이상이면 "변동성 높음"
VOL_LOW_PCT = 12.0
FLOW_Z_STRONG = 1.0


def _ema(values: Sequence[float], span: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (span + 1.0)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(out[-1] + k * (float(v) - out[-1]))
    return out


def _sma(values: Sequence[float], n: int) -> Optional[float]:
    if len(values) < n or n <= 0:
        return None
    return sum(float(v) for v in values[-n:]) / n


def _std(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    m = sum(values) / n
    return math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))


def rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    """Wilder 평활 RSI."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = float(closes[i]) - float(closes[i - 1])
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100.0 - 100.0 / (1.0 + rs), 2)


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
    if len(closes) < slow + signal:
        return None
    ef, es = _ema(closes, fast), _ema(closes, slow)
    line = [a - b for a, b in zip(ef, es)]
    sig = _ema(line[slow - 1:], signal)
    hist = line[-1] - sig[-1]
    prev_hist = line[-2] - sig[-2] if len(sig) >= 2 else hist
    cross = None
    if prev_hist <= 0 < hist:
        cross = "golden"
    elif prev_hist >= 0 > hist:
        cross = "dead"
    return {"macd": round(line[-1], 3), "signal": round(sig[-1], 3), "hist": round(hist, 3),
            "hist_prev": round(prev_hist, 3), "cross": cross}


def bollinger_pct_b(closes: Sequence[float], n: int = 20, k: float = 2.0) -> Optional[float]:
    if len(closes) < n:
        return None
    window = [float(v) for v in closes[-n:]]
    m = sum(window) / n
    sd = _std(window)
    if sd == 0:
        return 0.5
    return round((float(closes[-1]) - (m - k * sd)) / (2 * k * sd), 3)


def realized_vol_pct(closes: Sequence[float], n: int = 20) -> Optional[float]:
    """일간 로그수익률 표준편차 × √252, % 단위."""
    if len(closes) < n + 1:
        return None
    rets = [math.log(float(closes[i]) / float(closes[i - 1])) for i in range(len(closes) - n, len(closes))]
    return round(_std(rets) * math.sqrt(252) * 100, 2)


def atr_pct(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> Optional[float]:
    if min(len(highs), len(lows), len(closes)) < n + 1:
        return None
    trs = []
    for i in range(len(closes) - n, len(closes)):
        h, l, pc = float(highs[i]), float(lows[i]), float(closes[i - 1])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return round(sum(trs) / n / float(closes[-1]) * 100, 3)


def pct_change(closes: Sequence[float], lookback: int, skip: int = 0) -> Optional[float]:
    """(t-skip) 대비 (t-skip-lookback) 수익률 %. skip=21 → 최근 1개월 제외."""
    end = len(closes) - 1 - skip
    start = end - lookback
    if start < 0 or end < 0:
        return None
    return round((float(closes[end]) / float(closes[start]) - 1) * 100, 2)


def ma_alignment(closes: Sequence[float]) -> Optional[dict]:
    m20, m60, m120 = _sma(closes, 20), _sma(closes, 60), _sma(closes, 120)
    if m20 is None or m60 is None:
        return None
    last = float(closes[-1])
    if m120 is not None:
        if m20 > m60 > m120:
            state = "정배열"
        elif m20 < m60 < m120:
            state = "역배열"
        else:
            state = "혼조"
    else:
        state = "정배열(단기)" if m20 > m60 else "역배열(단기)"
    return {"ma20": round(m20, 2), "ma60": round(m60, 2), "ma120": round(m120, 2) if m120 else None,
            "state": state, "above_ma20": last > m20, "above_ma60": last > m60}


def zscore(values: Sequence[float], window: int = 20, min_n: int = 6) -> Optional[float]:
    """마지막 값의 z-score(직전 window-1개 기준). 외국인 순매수 등에 사용. 표본 min_n 미만이면 None."""
    vals = [float(v) for v in values if v is not None]
    if len(vals) < min_n:
        return None
    hist = vals[-window:] if len(vals) >= window else vals
    sd = _std(hist[:-1]) if len(hist) > 2 else 0.0
    if sd == 0:
        return 0.0
    m = sum(hist[:-1]) / (len(hist) - 1)
    return round((hist[-1] - m) / sd, 2)


def compute_indicators(closes: Sequence[float], highs: Optional[Sequence[float]] = None,
                       lows: Optional[Sequence[float]] = None,
                       flow_history: Optional[Sequence[float]] = None) -> dict:
    """KOSPI 종가 시계열(오래된 → 최신)에서 지표 묶음을 계산한다. 데이터가 모자라는 지표는 None."""
    closes = [float(c) for c in closes if c is not None]
    out: dict = {"n_sessions": len(closes), "last_close": round(closes[-1], 2) if closes else None}
    if not closes:
        return out
    out["rsi14"] = rsi(closes)
    out["macd"] = macd(closes)
    out["bb_pct_b"] = bollinger_pct_b(closes)
    out["ma"] = ma_alignment(closes)
    out["realized_vol_20"] = realized_vol_pct(closes)
    out["atr14_pct"] = atr_pct(highs, lows, closes) if highs is not None and lows is not None else None
    out["mom_12_1"] = pct_change(closes, lookback=231, skip=21)   # 12개월(252) - 최근 1개월(21)
    out["mom_3m"] = pct_change(closes, lookback=63)
    out["rev_1w"] = pct_change(closes, lookback=5)
    hi52 = max(closes[-252:])
    out["dist_52w_high_pct"] = round((closes[-1] / hi52 - 1) * 100, 2)
    out["flow_z5"] = None
    if flow_history:
        vals = [float(v) for v in flow_history if v is not None]
        if len(vals) >= 6:
            sums5 = [sum(vals[i - 4:i + 1]) for i in range(4, len(vals))]
            out["flow_z5"] = zscore(sums5)
    out["labels"] = describe(out)
    return out


def describe(ind: dict) -> dict:
    """지표 → 정성 라벨. QA 대조와 프롬프트 문장에 같은 기준을 쓴다."""
    lab: dict = {}
    r = ind.get("rsi14")
    if r is not None:
        lab["rsi"] = "과매수" if r >= RSI_OVERBOUGHT else "과매도" if r <= RSI_OVERSOLD else "중립"
    m = ind.get("macd")
    if m:
        lab["macd"] = ("골든크로스" if m.get("cross") == "golden" else "데드크로스" if m.get("cross") == "dead"
                       else "상승 모멘텀" if m["hist"] > 0 else "하락 모멘텀")
    b = ind.get("bb_pct_b")
    if b is not None:
        lab["bb"] = "상단 이탈" if b > BB_HIGH else "하단 이탈" if b < BB_LOW else "밴드 내"
    ma = ind.get("ma")
    if ma:
        lab["ma"] = ma["state"]
    v = ind.get("realized_vol_20")
    if v is not None:
        lab["vol"] = "높음" if v >= VOL_HIGH_PCT else "낮음" if v <= VOL_LOW_PCT else "보통"
    z = ind.get("flow_z5")
    if z is not None:
        lab["flow"] = "강한 순매수" if z >= FLOW_Z_STRONG else "강한 순매도" if z <= -FLOW_Z_STRONG else "평소 수준"
    mom = ind.get("mom_12_1")
    if mom is not None:
        lab["momentum"] = "양(+)" if mom > 0 else "음(-)"
    return lab


def quant_text(ind: dict) -> str:
    """프롬프트 주입용. 수치와 라벨을 함께 줘서 LLM이 재계산하지 않게 한다."""
    if not ind or not ind.get("last_close"):
        return ""
    lab = ind.get("labels") or describe(ind)
    lines = ["\n[정량 지표 — 코드로 계산(공개 공식), LLM 재계산 불필요. 근거로 인용할 때 아래 수치·라벨을 그대로 쓰세요]",
             f"KOSPI 종가 {ind['last_close']} (거래일 {ind.get('n_sessions')}개 기준)"]
    if ind.get("rsi14") is not None:
        lines.append(f"RSI(14, Wilder) = {ind['rsi14']} → {lab.get('rsi')} (과매수≥70, 과매도≤30)")
    m = ind.get("macd")
    if m:
        lines.append(f"MACD(12,26,9) 히스토그램 = {m['hist']} (전일 {m['hist_prev']}) → {lab.get('macd')}")
    if ind.get("bb_pct_b") is not None:
        lines.append(f"볼린저 %B(20,2σ) = {ind['bb_pct_b']} → {lab.get('bb')}")
    ma = ind.get("ma")
    if ma:
        lines.append(f"이평 20/60/120 = {ma['ma20']}/{ma['ma60']}/{ma.get('ma120')} → {ma['state']}, "
                     f"종가는 20일선 {'위' if ma['above_ma20'] else '아래'}")
    if ind.get("realized_vol_20") is not None:
        lines.append(f"실현 변동성(20일, 연율) = {ind['realized_vol_20']}% → {lab.get('vol')}"
                     + (f", ATR(14) = 종가의 {ind['atr14_pct']}%" if ind.get("atr14_pct") is not None else ""))
    if ind.get("mom_12_1") is not None:
        lines.append(f"12-1 모멘텀(Jegadeesh-Titman) = {ind['mom_12_1']}%, 3개월 = {ind.get('mom_3m')}%, "
                     f"1주 = {ind.get('rev_1w')}% (단기 반전 참고), 52주 고점 대비 {ind.get('dist_52w_high_pct')}%")
    elif ind.get("mom_3m") is not None:
        lines.append(f"3개월 모멘텀 = {ind['mom_3m']}%, 1주 = {ind.get('rev_1w')}%, 52주 고점 대비 {ind.get('dist_52w_high_pct')}%")
    if ind.get("flow_z5") is not None:
        lines.append(f"외국인 5일 누적 순매수 z-score = {ind['flow_z5']} → {lab.get('flow')}")
    return "\n".join(lines)


# ---- QA: 정성 주장 ↔ 지표 대조 -------------------------------------------------
# (패턴, 지표 라벨 키, 주장이 성립하는 라벨 집합)
_CLAIM_RULES = [
    (re.compile(r"과매수"), "rsi", {"과매수"}),
    (re.compile(r"과매도"), "rsi", {"과매도"}),
    (re.compile(r"골든\s*크로스"), "macd", {"골든크로스", "상승 모멘텀"}),
    (re.compile(r"데드\s*크로스"), "macd", {"데드크로스", "하락 모멘텀"}),
    (re.compile(r"정배열"), "ma", {"정배열", "정배열(단기)"}),
    (re.compile(r"역배열"), "ma", {"역배열", "역배열(단기)"}),
    (re.compile(r"변동성(이|은)?\s*(높|확대|급등)"), "vol", {"높음"}),
    (re.compile(r"변동성(이|은)?\s*(낮|축소|안정)"), "vol", {"낮음", "보통"}),
    (re.compile(r"외국인.*(순매수|매수\s*전환|매수세)"), "flow", {"강한 순매수", "평소 수준"}),
    (re.compile(r"외국인.*(순매도|매도\s*전환|매도세|이탈)"), "flow", {"강한 순매도", "평소 수준"}),
    (re.compile(r"(밴드|볼린저).*(상단|돌파)"), "bb", {"상단 이탈"}),
    (re.compile(r"(밴드|볼린저).*하단"), "bb", {"하단 이탈"}),
]


def check_claims(texts: Sequence[str], ind: dict) -> list[dict]:
    """근거 문장들에서 지표 관련 정성 주장을 찾아 라벨과 대조한다.
    반환: [{"text","claim","indicator","actual","verdict"}] verdict ∈ supported|contradicted.
    지표가 없거나 관련 주장이 없는 문장은 결과에 넣지 않는다(unchecked)."""
    lab = (ind or {}).get("labels") or describe(ind or {})
    out = []
    for t in texts or []:
        s = str(t)
        for pat, key, ok in _CLAIM_RULES:
            m = pat.search(s)
            if not m or key not in lab:
                continue
            actual = lab[key]
            out.append({"text": s, "claim": m.group(0), "indicator": key, "actual": actual,
                        "verdict": "supported" if actual in ok else "contradicted"})
            break  # 문장당 한 번
    return out


def contradiction_summary(checks: list[dict]) -> dict:
    c = sum(1 for x in checks if x["verdict"] == "contradicted")
    s = sum(1 for x in checks if x["verdict"] == "supported")
    return {"checked": len(checks), "supported": s, "contradicted": c,
            "contradicted_texts": [x["text"] for x in checks if x["verdict"] == "contradicted"]}
