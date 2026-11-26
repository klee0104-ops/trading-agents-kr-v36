"""
시장 데이터 수집 레이어.

⚠️ 이 샌드박스 환경은 Yahoo Finance 접속이 네트워크 정책상 막혀있어 실제 호출은
안 되지만, 로컬(인터넷 되는 PC)에서는 그대로 동작한다.

핵심 설계 원칙 (TradingAgents의 "검증된 데이터 접근 계약" 아이디어를 간소화 적용):
1. look-ahead 방지: backtest 시 조회 시점 이후 데이터가 절대 섞이면 안 된다.
2. stale 데이터 거부: 너무 오래된 캐시나 응답은 사용하지 않는다.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional
import json
import math
import os
from pathlib import Path


US_TICKERS = {
    "S&P500": "^GSPC",
    "나스닥": "^IXIC",
    "다우존스": "^DJI",
    "SOX": "^SOX",
    "VIX": "^VIX",
}

KR_INDEX_TICKER = "^KS11"  # KOSPI

# (V3.7) 거시 지표 — KOSPI 외국인 수급과 직결되는 것들. 실패해도 파이프라인은 계속 돈다.
MACRO_TICKERS = {
    "원/달러":     "KRW=X",      # 상승 = 원화 약세 → 외국인 매도 압력
    "미국10년금리": "^TNX",       # 급등 = 위험자산 부담
    "WTI유가":     "CL=F",       # 급등 = 수입 물가/무역수지 부담
    "달러인덱스":   "DX-Y.NYB",   # 강달러 = 신흥국 자금 유출
}


@dataclass
class IndexQuote:
    name: str
    change_pct: float
    direction: str  # "up" | "down"
    as_of: str
    value: Optional[float] = None  # 원시 종가 (규칙엔진에서 VIX 절대 수준 등을 판단할 때 사용)
    rsi14: Optional[float] = None
    macd_hist: Optional[float] = None
    bb_percent_b: Optional[float] = None


@dataclass
class MarketSnapshot:
    as_of: str
    us_indices: list[IndexQuote] = field(default_factory=list)
    kospi_prev_close_direction: Optional[str] = None
    macro: list[IndexQuote] = field(default_factory=list)   # (V3.7) 환율·금리·유가·달러
    kr_flow: Optional[dict] = None  # (V3.7) 외국인 수급 {"date","foreign_net_krw_bn","direction","source"} 또는 None
    data_notes: list[str] = field(default_factory=list)      # 조회 실패 등 안내


class DataAccessError(Exception):
    pass


class MarketDataProvider:
    """
    실제 데이터 소스(yfinance)를 감싸는 클래스.
    backtest_mode=True 이면 as_of_date 이후 데이터는 절대 반환하지 않는다 (look-ahead 방지).
    """

    def __init__(self, backtest_mode: bool = False):
        self.backtest_mode = backtest_mode
        self._cache: dict = {}  # 백테스트 중 같은 지수를 매일 다시 받지 않도록 (ticker -> DataFrame)
        self.us_close_cache_path = Path(os.environ.get("US_INDEX_CLOSE_CACHE_PATH", "us_index_close_cache.json"))

    @staticmethod
    def _weekday_steps(earlier: date, later: date) -> int:
        """두 날짜 사이에 있는 평일 수. 휴장일은 알 수 없으므로 누락 때 보수적으로 중단한다."""
        from datetime import timedelta
        count = 0
        day = earlier + timedelta(days=1)
        while day <= later:
            count += day.weekday() < 5
            day += timedelta(days=1)
        return count

    def _read_us_closes(self) -> dict:
        if self.backtest_mode:
            return {}
        try:
            data = json.loads(self.us_close_cache_path.read_text(encoding="utf-8"))
            return data.get("sessions", {}) if data.get("schema") == 1 else {}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def _save_us_closes(self, sessions: dict, session_date: str, quotes: list[IndexQuote]) -> None:
        if self.backtest_mode:
            return
        saved = dict(sessions)
        saved[session_date] = {q.name: q.value for q in quotes}
        saved = {key: saved[key] for key in sorted(saved)[-7:]}
        path = self.us_close_cache_path
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps({"schema": 1, "sessions": saved}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def _download_cached(self, yf, ticker, start, end):
        """백테스트 모드에선 넓은 구간을 한 번만 받아두고 잘라 쓴다 (look-ahead는 호출부에서 차단)."""
        if not self.backtest_mode:
            return yf.download(ticker, start=start, end=end, progress=False)
        from datetime import timedelta
        c = self._cache.get(ticker)
        if c is None or c["start"] > start or c["end"] < end:
            s0 = min(start, c["start"]) if c else start - timedelta(days=30)
            e0 = max(end, c["end"]) if c else end + timedelta(days=45)
            df = yf.download(ticker, start=s0, end=e0, progress=False)
            c = {"start": s0, "end": e0, "df": df}
            self._cache[ticker] = c
        df = c["df"]
        return df[(df.index.date >= start) & (df.index.date < end)]

    def fetch_kospi_sessions(self, start: date, end: date) -> list[dict]:
        """start~end 사이 KOSPI 실제 거래일과 각 날의 전일 대비 등락률 (백테스트 대상일 목록)."""
        try:
            import yfinance as yf
        except ImportError as e:
            raise DataAccessError("yfinance가 필요합니다: pip install yfinance") from e
        from datetime import timedelta
        hist = yf.download(KR_INDEX_TICKER, start=start - timedelta(days=15), end=end + timedelta(days=1),
                           progress=False)
        if hist.empty:
            raise DataAccessError("KOSPI 데이터를 가져오지 못했습니다")
        close = hist["Close"].squeeze()
        out = []
        for i in range(1, len(close)):
            d = close.index[i].date()
            if start <= d <= end:
                chg = float((close.iloc[i] / close.iloc[i - 1] - 1) * 100)
                out.append({"date": d, "change_pct": round(chg, 4),
                            "direction": "up" if chg >= 0 else "down"})
        return out

    def fetch_us_snapshot(self, as_of_date: Optional[date] = None) -> MarketSnapshot:
        try:
            import yfinance as yf
        except ImportError as e:
            raise DataAccessError("yfinance가 필요합니다: pip install yfinance") from e

        from data.features import compute_indicators

        quotes: list[IndexQuote] = []
        end = as_of_date or date.today()

        # 6개월치를 받아오는 이유: RSI14/MACD(12,26,9)/Bollinger(20) 계산에 필요한
        # 최소 종가 개수(최대 35개 영업일)를 안전하게 확보하기 위함.
        # (병합 시 수정) period="6mo"는 "오늘 기준" 6개월이라, 6개월보다 오래된 날짜를
        # 백테스트하면 데이터가 비었다. 기준일(end) 기준으로 명시적 구간을 요청한다.
        # yfinance의 end는 배타적이라 하루를 더한다.
        from datetime import timedelta
        previous_sessions = self._read_us_closes()
        for name, ticker in US_TICKERS.items():
            hist = self._download_cached(yf, ticker, end - timedelta(days=190), end + timedelta(days=1))
            if hist.empty or len(hist) < 2:
                raise DataAccessError(f"{name}({ticker}) 데이터를 가져오지 못했습니다")

            if self.backtest_mode and as_of_date is not None:
                hist = hist[hist.index.date <= end]
                if len(hist) < 2:
                    raise DataAccessError(f"{name}: as_of_date 이전 데이터가 부족합니다 (look-ahead 방지)")

            close = hist["Close"].squeeze().dropna()
            if len(close) < 2:
                raise DataAccessError(f"{name}: 유효 종가가 부족합니다")
            current_date, yahoo_prev_date = close.index[-1].date(), close.index[-2].date()
            prev_value = float(close.iloc[-2])
            if self._weekday_steps(yahoo_prev_date, current_date) > 1:
                candidates = [(date.fromisoformat(day), values.get(name)) for day, values in previous_sessions.items()
                              if isinstance(values, dict) and yahoo_prev_date < date.fromisoformat(day) < current_date]
                candidates = [(day, value) for day, value in candidates
                              if self._weekday_steps(day, current_date) == 1
                              and isinstance(value, (int, float)) and math.isfinite(value) and value > 0]
                if not candidates:
                    raise DataAccessError(f"{name}: {yahoo_prev_date}~{current_date} 사이 미국장 종가 누락; 전일 등락률 계산 보류")
                prior_date, prev_value = max(candidates)
                # 보완한 종가를 기술 지표에도 반영한다.
                import pandas as pd
                close = close.copy()
                close.loc[pd.Timestamp(prior_date)] = prev_value
                close = close.sort_index()
            change_pct = float((float(close.iloc[-1]) / prev_value - 1) * 100)
            features = compute_indicators(close)
            quotes.append(IndexQuote(
                name=name,
                change_pct=round(change_pct, 2),
                direction="up" if change_pct >= 0 else "down",
                as_of=str(hist.index[-1].date()),
                value=round(float(close.iloc[-1]), 2),
                rsi14=features["rsi14"],
                macd_hist=features["macd_hist"],
                bb_percent_b=features["bb_percent_b"],
            ))

        # 같은 날짜의 다섯 지수가 모두 검증된 뒤에만 재사용 가능한 종가를 기록한다.
        if len({q.as_of for q in quotes}) == 1:
            self._save_us_closes(previous_sessions, quotes[0].as_of, quotes)

        # (V3.7) 거시 지표 — 개별 실패는 건너뛰고 기록만 남긴다
        macro: list[IndexQuote] = []
        notes: list[str] = []
        for name, ticker in MACRO_TICKERS.items():
            try:
                hist = self._download_cached(yf, ticker, end - timedelta(days=45), end + timedelta(days=1))
                if self.backtest_mode and as_of_date is not None:
                    hist = hist[hist.index.date <= end]
                if hist.empty or len(hist) < 2:
                    raise DataAccessError("데이터 부족")
                close = hist["Close"].squeeze().dropna()
                if len(close) < 2:
                    raise DataAccessError("데이터 부족")
                if self._weekday_steps(close.index[-2].date(), close.index[-1].date()) > 1:
                    raise DataAccessError("중간 거래일 누락 · 등락률 보류")
                chg = float((close.iloc[-1] / close.iloc[-2] - 1) * 100)
                macro.append(IndexQuote(name=name, change_pct=round(chg, 2),
                                        direction="up" if chg >= 0 else "down",
                                        as_of=str(close.index[-1].date()), value=round(float(close.iloc[-1]), 3)))
            except Exception as e:  # noqa: BLE001 — 거시 지표는 보조 정보라 실패해도 계속
                notes.append(f"{name} 조회 실패: {type(e).__name__}")

        kr_flow = None
        try:
            from data.kr_flow import fetch_foreign_net_buy
            kr_flow = fetch_foreign_net_buy(end)
        except Exception as e:  # noqa: BLE001
            notes.append(f"외국인 수급 조회 실패: {type(e).__name__}")

        return MarketSnapshot(as_of=str(end), us_indices=quotes, macro=macro, kr_flow=kr_flow, data_notes=notes)

    def fetch_kospi_actual_change(self, target_date: date) -> float:
        """검증/백테스트 단계에서 사용: target_date의 KOSPI 실제 등락률(%)을 조회한다."""
        try:
            import yfinance as yf
        except ImportError as e:
            raise DataAccessError("yfinance가 필요합니다: pip install yfinance") from e

        # (병합 시 수정) 예전엔 period="15d"(오늘 기준 15일)라서, 검증을 며칠만 늦게
        # 돌려도 target_date 데이터가 범위 밖으로 밀려나 영원히 검증되지 않았다.
        from datetime import timedelta
        hist = yf.download(KR_INDEX_TICKER, start=target_date - timedelta(days=15),
                           end=target_date + timedelta(days=1), progress=False)
        hist = hist[hist.index.date <= target_date]
        if len(hist) < 2:
            raise DataAccessError(f"KOSPI {target_date} 기준 데이터가 부족합니다")
        close = hist["Close"].squeeze()
        return float((close.iloc[-1] / close.iloc[-2] - 1) * 100)

    def fetch_kospi_session_after(self, after_date: date) -> dict:
        """after_date(분석 실행일) 이후 첫 KOSPI 거래일의 실제 등락. 공휴일을 자동으로 건너뛴다."""
        try:
            import yfinance as yf
        except ImportError as e:
            raise DataAccessError("yfinance가 필요합니다: pip install yfinance") from e
        from datetime import timedelta
        from data.sessions import first_session_after

        try:
            hist = yf.download(KR_INDEX_TICKER, start=after_date - timedelta(days=15),
                               end=after_date + timedelta(days=20), progress=False)
        except Exception:  # noqa: BLE001 — Yahoo 장애 시 검증 가능한 국내 종가로만 보완
            hist = None
        if hist is None or hist.empty:
            if self.backtest_mode:
                raise DataAccessError(f"KOSPI {after_date} 전후 데이터를 가져오지 못했습니다")
            return self._naver_kospi_session_after(after_date)
        close = hist["Close"].squeeze()
        sdates = [str(d.date()) for d in close.index]
        closes = [float(v) for v in close.tolist()]
        sup = self._supplement_kospi({"dates": sdates, "closes": closes}, after_date + timedelta(days=20), 400)
        dates = [date.fromisoformat(x) for x in sup["dates"]]
        closes = sup["closes"]
        i = first_session_after(dates, after_date)
        if i is None:
            raise DataAccessError(f"{after_date} 이후 KOSPI 거래일 데이터가 아직 없습니다")
        change = float((closes[i] / closes[i - 1] - 1) * 100)
        return {"actual_date": str(dates[i]), "change_pct": round(change, 4),
                "actual_direction": "up" if change >= 0 else "down"}

    def _naver_kospi_session_after(self, after_date: date) -> dict:
        """Yahoo 전체 장애 시 최근 국내 확정 종가만으로 채점한다. 백테스트에는 사용하지 않는다."""
        if self.backtest_mode:
            raise DataAccessError("백테스트에서는 현재 시세로 과거 거래일을 보완하지 않습니다")
        from data.kr_daily import fetch_daily
        from data.sessions import first_session_after, kst_now

        now = kst_now()
        rows = fetch_daily("KOSPI", "index", pages=4)
        rows = [r for r in rows if r.get("date", "") <= str(now.date())]
        if now.hour < 15 or (now.hour == 15 and now.minute < 40):
            rows = [r for r in rows if r["date"] < str(now.date())]
        dates = [date.fromisoformat(r["date"]) for r in rows]
        i = first_session_after(dates, after_date)
        if i is None:
            raise DataAccessError(f"{after_date} 이후 확정 KOSPI 종가가 없습니다")
        row, previous = rows[i], rows[i - 1]
        close, prev = row.get("close"), row.get("prev_close")
        prior_close = previous.get("close")
        if (any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                for v in (close, prev, prior_close)) or abs(prev - prior_close) > 0.05):
            raise DataAccessError("KOSPI 전일 종가 대조에 실패했습니다")
        change = (close / prev - 1) * 100
        return {"actual_date": row["date"], "change_pct": round(change, 4),
                "actual_direction": "up" if change >= 0 else "down", "source": "네이버 증권"}

    def fetch_kospi_history(self, as_of: date, sessions: int = 260) -> dict:
        """(V3.14) as_of 까지의 KOSPI 종가/고가/저가 시계열 (오래된→최신). 정량 지표 계산용.
        as_of 이후 데이터는 절대 포함하지 않는다(look-ahead 방지)."""
        try:
            import yfinance as yf
        except ImportError as e:
            raise DataAccessError("yfinance가 필요합니다: pip install yfinance") from e
        from datetime import timedelta
        start = as_of - timedelta(days=int(sessions * 1.6) + 10)
        hist = self._download_cached(yf, KR_INDEX_TICKER, start, as_of + timedelta(days=1))
        if hist is None or hist.empty:
            raise DataAccessError("KOSPI 과거 데이터를 가져오지 못했습니다")
        hist = hist[hist.index.date <= as_of].tail(sessions)
        def col(name):
            c = hist[name]
            c = c.squeeze() if hasattr(c, "squeeze") else c
            return [float(v) for v in c.tolist()]
        out = {"dates": [str(d.date()) for d in hist.index], "closes": col("Close"),
               "highs": col("High"), "lows": col("Low")}
        return self._supplement_kospi(out, as_of, sessions)

    def _supplement_kospi(self, out: dict, as_of: date, sessions: int) -> dict:
        """(V3.22) Yahoo 에 최근 거래일이 빠졌으면 네이버 일별 시세로 채운다. 백테스트 모드에선 하지 않는다."""
        if self.backtest_mode:
            return out
        try:
            from data.kr_daily import fetch_daily, merge_series
            from data.sessions import kst_now
            from datetime import timedelta as _td
            now = kst_now()
            closed = now.hour > 15 or (now.hour == 15 and now.minute >= 40)
            # 장 마감 전이면 오늘 행(Yahoo 장중 값 포함)은 뺀다 — 종가만 쓴다
            drop_after = None if closed else str(now.date() - _td(days=1))
            extra = fetch_daily("KOSPI", "index")
            d, c, h, l, added = merge_series(out["dates"], out["closes"], extra, as_of, out.get("highs"), out.get("lows"),
                                             drop_after=drop_after)
            if added or len(d) != len(out["dates"]):
                out = {"dates": d[-sessions:], "closes": c[-sessions:], "highs": (h or [])[-sessions:],
                       "lows": (l or [])[-sessions:], "supplemented": added}
        except Exception:  # noqa: BLE001 — 보충 실패는 무시(Yahoo 값 그대로)
            pass
        return out

    def fetch_etf_returns(self, session_date: date) -> dict:
        """(V3.14) 채점용: 롱(KODEX200)·쇼트(KODEX 인버스) ETF의 session_date 종가 기준 등락률(%).
        (V3.22) fetch_etf_ohlc 를 재사용해 Yahoo 누락 시 네이버로 보충. 못 받은 쪽 키는 없다 → 지수로 근사."""
        out = {}
        for key, row in (self.fetch_etf_ohlc(session_date) or {}).items():
            if row.get("prev_close"):
                out[key] = round((row["close"] / row["prev_close"] - 1) * 100, 4)
        return out

    def fetch_etf_ohlc(self, session_date: date) -> dict:
        """(V3.21) 가상 계좌 체결용: 롱·쇼트 ETF의 session_date 시가/종가/전일종가. {"long": {...}, "short": {...}}"""
        try:
            import yfinance as yf
        except ImportError as e:
            raise DataAccessError("yfinance가 필요합니다: pip install yfinance") from e
        from datetime import timedelta
        from execution.position import LONG_ETF, SHORT_ETF
        out = {}
        for key, tk in (("long", LONG_ETF), ("short", SHORT_ETF)):
            try:
                h = yf.download(tk, start=session_date - timedelta(days=15), end=session_date + timedelta(days=1),
                                progress=False)
                h = h[h.index.date <= session_date]
                if len(h) >= 2 and h.index[-1].date() == session_date:
                    out[key] = {"ticker": tk, "open": round(float(h["Open"].squeeze().iloc[-1]), 2),
                                "close": round(float(h["Close"].squeeze().iloc[-1]), 2),
                                "prev_close": round(float(h["Close"].squeeze().iloc[-2]), 2)}
            except Exception:  # noqa: BLE001
                pass
            if key not in out and not self.backtest_mode:  # (V3.22) Yahoo 누락 시 네이버 일별 시세
                try:
                    from data.kr_daily import fetch_daily
                    row = next((r for r in fetch_daily(tk.split(".")[0], "stock") if r["date"] == str(session_date)), None)
                    if row and row.get("open") and row.get("prev_close"):
                        out[key] = {"ticker": tk, "open": row["open"], "close": row["close"], "prev_close": row["prev_close"],
                                    "source": "naver"}
                except Exception:  # noqa: BLE001
                    pass
        return out

    def fetch_kospi_actual_direction(self, target_date: date) -> str:
        """검증 단계에서 사용: target_date의 KOSPI 실제 등락 방향을 조회한다."""
        change_pct = self.fetch_kospi_actual_change(target_date)
        return "up" if change_pct >= 0 else "down"

    def fetch_stock_actual(self, name_or_code: str, target_date: date,
                            after_date: date | None = None) -> dict:
        """개별 종목의 실제 등락 (after_date를 주면 그 이후 첫 거래일 기준, 공휴일 자동 처리)."""
        from data.price_history import fetch_actual_stock_direction
        return fetch_actual_stock_direction(name_or_code, target_date, after_date=after_date)
