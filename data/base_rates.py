"""(V3.37) 기준 비율: '오늘 같은 상황'이 과거에 몇 번 있었고 다음 날 코스피가 어떻게 마감했는지 코드로 센다.

- 자료: Yahoo Finance 일봉(^SOX, ^KS11), 2012년~. 6일마다 한 번 받아 base_rate_cache.json 에 캐시.
- 대상일 이전 자료만 사용(미래 정보 차단). 표본이 적으면(15일 미만) '표본 부족'으로 표시.
- 결과는 근거 자료(Q)로 분석가·토론·QA 에 전달된다. 예측을 대신하지 않는다.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
from statistics import mean, median

CACHE_TTL = timedelta(days=6)
START = date(2012, 1, 1)
MIN_SAMPLE = 15
FLAT_PCT = 0.3
SOX_BUCKETS = [(-99, -4, 'SOX −4% 이하 급락'), (-4, -2, 'SOX −4~−2% 하락'), (-2, -0.5, 'SOX −2~−0.5% 약세'),
               (-0.5, 0.5, 'SOX ±0.5% 이내 보합'), (0.5, 2, 'SOX +0.5~+2% 강세'), (2, 4, 'SOX +2~+4% 상승'),
               (4, 99, 'SOX +4% 이상 급등')]


def _download(start, end):
    import yfinance as yf
    sox = yf.download('^SOX', start=start, end=end, progress=False)
    ks = yf.download('^KS11', start=start, end=end, progress=False)
    def series(df, col):
        c = df[col]
        c = c.squeeze() if hasattr(c, 'squeeze') else c
        return {str(i.date()): float(v) for i, v in c.items() if v == v}
    return {'sox': series(sox, 'Close'), 'kospi_close': series(ks, 'Close'), 'kospi_open': series(ks, 'Open')}


def load_series(cache_path, today, downloader=_download):
    path = Path(cache_path)
    try:
        cached = json.loads(path.read_text(encoding='utf-8'))
        if datetime.fromisoformat(cached['fetched_at']).date() >= today - CACHE_TTL and cached.get('sox'):
            return cached
    except (OSError, ValueError, KeyError, TypeError):
        cached = None
    try:
        data = downloader(START, today + timedelta(days=1))
        if len(data.get('sox', {})) < 500 or len(data.get('kospi_close', {})) < 500:
            raise ValueError('too_short')
        data['fetched_at'] = today.isoformat()
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(json.dumps(data), encoding='utf-8')
        os.replace(tmp, path)
        return data
    except Exception:  # noqa: BLE001 — 실패하면 예전 캐시(있으면)라도 쓴다
        return cached


def pair_events(series, as_of):
    """KOSPI 거래일 t 마다: 직전 미국 세션의 SOX 등락, KOSPI 전일 등락, t 의 종가·시가 기준 등락. t < as_of 만."""
    sox_dates = sorted(series['sox'])
    sox_chg = {d: (series['sox'][d] / series['sox'][p] - 1) * 100 for p, d in zip(sox_dates, sox_dates[1:])}
    sd = sorted(sox_chg)
    kd = sorted(d for d in series['kospi_close'] if d < str(as_of))
    out, j = [], 0
    for prev2, prev, t in zip(kd, kd[1:], kd[2:]):
        while j + 1 < len(sd) and sd[j + 1] < t:
            j += 1
        if not sd or sd[j] >= t:
            continue
        c = series['kospi_close']
        o = series.get('kospi_open', {}).get(t)
        row = {'date': t, 'sox': sox_chg[sd[j]], 'kospi_prev': (c[prev] / c[prev2] - 1) * 100,
               'kospi': (c[t] / c[prev] - 1) * 100}
        if o and o > 0:
            row['gap'] = (o / c[prev] - 1) * 100
            row['open_to_close'] = (c[t] / o - 1) * 100
        out.append(row)
    return out


def _bucket(x):
    return next(label for lo, hi, label in SOX_BUCKETS if lo < x <= hi or (lo == -99 and x <= hi))


def _stats(rows):
    ks = [r['kospi'] for r in rows]
    gaps = [r['gap'] for r in rows if 'gap' in r]
    rec = [r for r in rows if 'gap' in r and r['gap'] <= -1.0]
    return {'n': len(ks), 'up': sum(k > 0 for k in ks), 'flat': sum(abs(k) < FLAT_PCT for k in ks),
            'mean': round(mean(ks), 2) if ks else None, 'median': round(median(ks), 2) if ks else None,
            'gap_mean': round(mean(gaps), 2) if gaps else None,
            'gap_down_recovered': sum(r['open_to_close'] > 0 for r in rec), 'gap_down_n': len(rec)}


def base_rate(series, as_of, sox_change, kospi_prev_change=None):
    """오늘 SOX 등락 구간(+전일 코스피 급락·급등이면 그 조건도)의 과거 다음 날 코스피 통계."""
    if not series or sox_change is None:
        return None
    rows = pair_events(series, as_of)
    if not rows:
        return None
    label = _bucket(sox_change)
    same = [r for r in rows if _bucket(r['sox']) == label]
    out = {'as_of': str(as_of), 'period': f"{rows[0]['date']}~{rows[-1]['date']}", 'sox_bucket': label,
           'sox_change': round(sox_change, 2), 'bucket': _stats(same), 'all': _stats(rows)}
    if kospi_prev_change is not None and abs(kospi_prev_change) >= 2:
        cond = '전일 코스피 −2% 이하 급락' if kospi_prev_change < 0 else '전일 코스피 +2% 이상 급등'
        both = [r for r in same if (r['kospi_prev'] <= -2 if kospi_prev_change < 0 else r['kospi_prev'] >= 2)]
        out['with_prev'] = {'condition': cond, **_stats(both)}
    return out


def registry_lines(br):
    """근거 자료 문장. 표본이 적으면 그렇게 적는다."""
    if not br:
        return []
    def line(title, s):
        if s['n'] < MIN_SAMPLE:
            return f"{title}: 과거 {s['n']}일 — 표본 부족(15일 미만), 참고만"
        rec = (f", 시가 −1% 이하 하락 출발 {s['gap_down_n']}일 중 장중 회복(종가>시가) {s['gap_down_recovered']}일"
               if s['gap_down_n'] else '')
        return (f"{title}: 과거 {s['n']}일 중 다음 날 코스피 상승 마감 {s['up']}일({100 * s['up'] / s['n']:.0f}%), "
                f"±0.3% 이내 보합 {s['flat']}일, 평균 {s['mean']:+.2f}% · 중앙값 {s['median']:+.2f}%{rec}")
    out = [line(f"기준 비율({br['period']}) {br['sox_bucket']}(오늘 SOX {br['sox_change']:+.2f}%) 다음 날", br['bucket']),
           line(f"기준 비율 전체 거래일", br['all'])]
    if br.get('with_prev'):
        w = br['with_prev']
        out.append(line(f"기준 비율 {br['sox_bucket']} + {w['condition']}", w))
    return out


def kospi_prev_change(series, as_of):
    if not series:
        return None
    kd = sorted(d for d in series['kospi_close'] if d < str(as_of))
    if len(kd) < 2:
        return None
    c = series['kospi_close']
    return (c[kd[-1]] / c[kd[-2]] - 1) * 100


def special_tags(sox_change, kospi_prev, has_big_disclosure=False):
    """(V3.37) 특수 상황 태그 — 교훈 검색·기록에 함께 쓴다."""
    tags = []
    if sox_change is not None and sox_change <= -4:
        tags.append('특수:SOX급락')
    if sox_change is not None and sox_change >= 4:
        tags.append('특수:SOX급등')
    if kospi_prev is not None and kospi_prev <= -2:
        tags.append('특수:전일급락')
    if kospi_prev is not None and kospi_prev >= 2:
        tags.append('특수:전일급등')
    if has_big_disclosure:
        tags.append('특수:대형주공시')
    return tags
