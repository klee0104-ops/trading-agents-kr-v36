"""
Feature Engineering. V3.5 설계의 "Python Processing Layer" 중 계산 가능한 지표는
전부 코드로 산출한다는 원칙에 따라, LLM 호출 없이 RSI/MACD/Bollinger Band를
순수 pandas 연산으로 계산한다.

입력은 종가(Close) 시계열 하나면 충분해서, 지수든 개별 종목이든 동일하게 쓸 수 있다.
데이터가 지표 계산에 필요한 최소 길이보다 짧으면 해당 지표는 None으로 둔다
(예: RSI14는 최소 15개, MACD(12,26,9)는 최소 35개 종가 필요).
"""
from __future__ import annotations
import pandas as pd


def compute_indicators(close: "pd.Series") -> dict:
    result = {"rsi14": None, "macd_hist": None, "bb_percent_b": None}

    if len(close) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))
        val = rsi.iloc[-1]
        if pd.notna(val):
            result["rsi14"] = round(float(val), 2)
        elif pd.notna(avg_loss.iloc[-1]) and avg_loss.iloc[-1] == 0 and pd.notna(avg_gain.iloc[-1]):
            # 하락이 전혀 없었던 구간(avg_loss=0) -> RSI 100
            result["rsi14"] = 100.0

    if len(close) >= 35:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal
        val = hist.iloc[-1]
        if pd.notna(val):
            result["macd_hist"] = round(float(val), 4)

    if len(close) >= 20:
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20
        band_range = (upper - lower).iloc[-1]
        if pd.notna(band_range) and band_range != 0:
            percent_b = (close.iloc[-1] - lower.iloc[-1]) / band_range
            result["bb_percent_b"] = round(float(percent_b), 3)

    return result
