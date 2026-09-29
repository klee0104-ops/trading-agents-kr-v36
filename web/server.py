"""
로컬 대시보드 API 서버.

에이전트 파이프라인 실행 결과 / 과거 적중률 / 변동성 이벤트 아카이브를
JSON API로 노출하고, 정적 프론트엔드(web/static)를 서빙한다.

보안 설계 (localhost 단일 사용자 전제지만, "실제 분석 실행"이 유료 API 키를
쓰기 때문에 다른 웹사이트가 몰래 호출하지 못하게 막는다 — SECURITY.md 참고):
1. 127.0.0.1에만 바인딩 (같은 와이파이의 다른 기기에서 접근 불가)
2. Host 헤더 허용목록 — DNS 리바인딩 공격 차단
3. 상태를 바꾸는 POST 요청은 (a) Origin이 이 서버일 것 (b) 서버 시작 시 새로 만든
   세션 토큰(X-TA-Token 헤더)이 일치할 것 — 둘 다 만족해야 한다 (CSRF 차단)
4. 실제 분석(run real)은 동시에 1건만 — 토큰 과금 폭주 방지
5. 에러 메시지·응답은 전부 마스킹(redact) 후 반환, 키 값은 어떤 응답에도 포함하지 않음
6. 보안 헤더 (CSP, X-Frame-Options, nosniff, no-store)
"""
from __future__ import annotations

import hmac
import re
import secrets
import threading
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, abort, Response

from config import Config
from agents.base import MockLLMClient
from memory.store import MemoryStore
from memory.event_archive import collect_and_store, query_events, query_news_for_event, list_stock_names
from graph.pipeline import TradingAgentsPipeline
from cli import _DemoDataProvider, _real_llm
from security.secret_store import redact
from memory.outcome_store import save_last_outcome, load_last_outcome

_STATIC_DIR = Path(__file__).parent / "static"

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

# 서버 프로세스가 뜰 때마다 새로 만드는 1회용 토큰. 디스크에 저장하지 않는다.
SESSION_TOKEN = secrets.token_urlsafe(32)
_RUN_LOCK = threading.Lock()

_MAX_COLLECT_DAYS = 400
_STOCK_NAME_RE = re.compile(r"^[0-9A-Za-z가-힣&\.\-\s]{1,40}$")


def _allowed_hosts() -> set[str]:
    port = Config.WEB_PORT
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def _allowed_origins() -> set[str]:
    return {f"http://{h}" for h in _allowed_hosts()}


@app.before_request
def _security_gate():
    # (2) DNS 리바인딩 방지: 공격자 도메인이 127.0.0.1로 풀리더라도 Host 헤더는
    # 공격자 도메인이므로 여기서 걸린다.
    if request.host not in _allowed_hosts():
        abort(403)

    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # (0) 보기 전용 모드: 상태를 바꾸거나 API를 호출하는 요청은 토큰과 무관하게 전부 거부
        if Config.WEB_READONLY:
            return jsonify({"error": "보기 전용 모드입니다. 분석 실행과 수집은 비활성화되어 있습니다."}), 403
        # (3a) 브라우저는 교차 출처 요청에 Origin을 반드시 붙인다
        origin = request.headers.get("Origin")
        if origin is not None and origin not in _allowed_origins():
            abort(403)
        # (3b) 세션 토큰
        token = request.headers.get("X-TA-Token", "")
        if not hmac.compare_digest(token, SESSION_TOKEN):
            return jsonify({"error": "보안 토큰이 없거나 일치하지 않습니다. 페이지를 새로고침하세요."}), 403
        if not request.is_json:
            return jsonify({"error": "Content-Type: application/json 요청만 허용됩니다."}), 415
        if not isinstance(request.get_json(silent=True), dict):
            return jsonify({"error": "JSON 객체가 필요합니다."}), 400


@app.after_request
def _security_headers(resp: Response):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _safe_error(e: Exception, status: int = 500):
    """예외 메시지를 마스킹해서 반환한다 (키/요청 헤더가 섞여 있을 가능성 대비)."""
    return jsonify({"error": redact(f"{type(e).__name__}: {e}")[:500]}), status


@app.errorhandler(Exception)
def _handle_error(error):
    from werkzeug.exceptions import HTTPException
    if isinstance(error, HTTPException):
        return jsonify({"error": error.name}), error.code
    # Unhandled exceptions may contain paths, provider bodies, or credentials.
    return jsonify({"error": "내부 오류가 발생했습니다."}), 500


def _has_real_api_key() -> bool:
    return bool(Config.api_key())


# ------------------------------------------------------------------ 정적 파일
@app.route("/")
def index():
    html = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
    # 세션 토큰은 같은 출처에서만 읽을 수 있는 HTML 안에 심는다 (다른 사이트는 SOP로 못 읽음)
    # 보기 전용 모드에서는 토큰을 아예 내려보내지 않는다 (페이지를 본 사람도 POST 불가)
    html = html.replace("__TA_SESSION_TOKEN__", "" if Config.WEB_READONLY else SESSION_TOKEN)
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/<path:filename>")
def static_files(filename):
    if filename not in {"app.js", "style.css"}:
        abort(404)
    return send_from_directory(_STATIC_DIR, filename)


# ------------------------------------------------------------------ API
@app.route("/api/health")
def health():
    key = Config.api_key()
    return jsonify({
        "status": "ok",
        "provider": Config.LLM_PROVIDER,
        "has_api_key": bool(key),
        # 어디서 로드됐는지만 알려준다 (값/마스킹값 모두 응답에 넣지 않음)
        "key_source": key.source,
        "readonly": Config.WEB_READONLY,
    })


@app.route("/api/history")
def history():
    memory = MemoryStore(Config.MEMORY_DB_PATH)
    return jsonify({
        "records": memory.all_records(),
        "stats": memory.compute_stats(),
    })


@app.route("/api/latest")
def latest():
    """마지막 실제 분석 결과 (재실행/과금 없이 조회)."""
    data = load_last_outcome(Config.LAST_OUTCOME_PATH)
    if not data:
        return jsonify({"error": "저장된 실제 분석 결과가 없습니다."}), 404
    if Config.WEB_READONLY:
        # 보기 전용(타인 열람용)에서는 뉴스 원문 제목을 빼고 건수만 남긴다.
        o = dict(data.get("outcome") or {})
        titles = o.pop("news_titles", None) or []
        o["news_count"] = len(titles)
        data = {**data, "outcome": o}
    return jsonify(data)


_KOSPI_CACHE: dict = {}


@app.route("/api/live")
def market_live():
    """(V3.26) 실시간에 가까운 국내 지수(네이버 증권 일별 시세의 오늘 행 = 마지막 체결가). 20초 캐시, LLM 미사용.
    보기 전용 모드에서도 허용(읽기만). 실패하면 502 — 화면은 기존 시장 현황판을 그대로 둔다."""
    import time as _t
    from data.sessions import kst_now, market_status
    hit = _MARKET_CACHE.get("live")
    if hit and _t.time() - hit[0] < 20:
        return jsonify(hit[1])
    try:
        from data.kr_daily import fetch_daily
        now = kst_now()
        out = {"server_time": now.strftime("%Y-%m-%dT%H:%M:%S"), "status": market_status(now), "rows": []}
        for code, name in (("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ")):
            try:
                rows = fetch_daily(code, "index", include_intraday=True)
            except Exception:  # noqa: BLE001 — 한 지수 실패는 건너뛴다
                continue
            if not rows:
                continue
            r = rows[-1]
            chg = round((r["close"] / r["prev_close"] - 1) * 100, 2) if r.get("prev_close") else None
            out["rows"].append({"name": name, "value": r["close"], "prev": r.get("prev_close"), "change_pct": chg,
                                "date": r["date"], "traded_at": r.get("traded_at") or r["date"],
                                "is_today": r["date"] == str(now.date())})
        if not out["rows"]:
            raise RuntimeError("국내 지수 시세를 가져오지 못했습니다")
    except Exception as e:  # noqa: BLE001
        return _safe_error(e, 502)
    _MARKET_CACHE["live"] = (_t.time(), out)
    return jsonify(out)


@app.route("/api/kospi")
def kospi_history():
    """(V3.18) 차트용 KOSPI 최근 종가 (yfinance, 10분 캐시, LLM 미사용). 보기 전용 모드에서도 허용."""
    import time as _t
    from datetime import date as _d
    try:
        days = max(20, min(250, int(request.args.get("days", 80))))
    except (ValueError, TypeError):
        return jsonify({"error": "days는 정수여야 합니다."}), 400
    key = (str(_d.today()), days)
    hit = _KOSPI_CACHE.get(key)
    if hit and _t.time() - hit[0] < 600:
        return jsonify(hit[1])
    try:
        from data.market_data import MarketDataProvider
        h = MarketDataProvider().fetch_kospi_history(_d.today(), sessions=days)
        out = {"dates": h["dates"], "closes": [round(c, 2) for c in h["closes"]],
               "highs": [round(c, 2) for c in h.get("highs", [])], "lows": [round(c, 2) for c in h.get("lows", [])]}
    except Exception as e:  # noqa: BLE001
        return _safe_error(e, 502)
    _KOSPI_CACHE[key] = (_t.time(), out)
    return jsonify(out)


_MARKET_CACHE: dict = {}


@app.route("/api/market")
def market_board():
    """(V3.19) 첫 화면 시세판: KOSPI 오늘(장중이면 현재가) + 참고 미국 지수 + 거시. yfinance, 10분 캐시."""
    import time as _t
    from datetime import date as _d, timedelta as _td
    hit = _MARKET_CACHE.get("board")
    if hit and _t.time() - hit[0] < 600:
        return jsonify(hit[1])
    try:
        import yfinance as yf
        from data.market_data import US_TICKERS, MACRO_TICKERS, KR_INDEX_TICKER
        rows = []
        def quote(name, tk, group):
            h = yf.download(tk, start=_d.today() - _td(days=12), end=_d.today() + _td(days=1), progress=False)
            if h is None or len(h) < 2:
                return
            c = h["Close"].squeeze()
            last, prev = float(c.iloc[-1]), float(c.iloc[-2])
            rows.append({"name": name, "group": group, "value": round(last, 2), "prev": round(prev, 2),
                         "change_pct": round((last / prev - 1) * 100, 2), "date": str(c.index[-1].date()),
                         "is_today": c.index[-1].date() == _d.today()})
        quote("KOSPI", KR_INDEX_TICKER, "kr")
        try:  # (V3.22) Yahoo 의 KOSPI 가 늦으면(최근 거래일 누락) 네이버 일별(장중 포함)로 대체
            from data.kr_daily import fetch_daily
            nv = fetch_daily("KOSPI", "index", include_intraday=True)
            if nv:
                last_nv = nv[-1]
                kr = next((r for r in rows if r["group"] == "kr"), None)
                if kr is None or last_nv["date"] > kr["date"]:
                    rows = [r for r in rows if r["group"] != "kr"]
                    rows.insert(0, {"name": "KOSPI", "group": "kr", "value": last_nv["close"], "prev": last_nv["prev_close"],
                                    "change_pct": round((last_nv["close"] / last_nv["prev_close"] - 1) * 100, 2) if last_nv["prev_close"] else None,
                                    "date": last_nv["date"], "is_today": last_nv["date"] == str(_d.today())})
        except Exception:  # noqa: BLE001
            pass
        for n, tk in US_TICKERS.items():
            quote(n, tk, "us")
        for n, tk in MACRO_TICKERS.items():
            quote(n, tk, "macro")
        out = {"rows": rows, "fetched_at": _t.strftime("%Y-%m-%d %H:%M")}
    except Exception as e:  # noqa: BLE001
        return _safe_error(e, 502)
    _MARKET_CACHE["board"] = (_t.time(), out)
    return jsonify(out)


@app.route("/api/paper")
def paper_account():
    """(V3.21) 가상 계좌 요약."""
    from execution.paper import PaperAccount
    try:
        return jsonify(PaperAccount(Config.PAPER_ACCOUNT_PATH, Config.PAPER_INITIAL_CASH).summary())
    except Exception as e:  # noqa: BLE001
        return _safe_error(e)


@app.route("/api/backtest")
def backtest_results():
    """저장형 백테스트 결과. backtest_results*.json 전부의 요약 + 선택한(기본: 최신) 파일의 일별 결과."""
    import glob as _glob
    import json as _json
    import os as _os
    import re as _re
    from backtest.persist import summarize_persisted
    files = sorted(_glob.glob("./backtest_results*.json"), key=_os.path.getmtime, reverse=True)
    if not files:
        return jsonify({"error": "백테스트 결과가 없습니다."}), 404
    runs = []
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            data = _json.load(f)
        runs.append({"file": _os.path.basename(fp), "meta": data.get("meta", {}),
                     "summary": summarize_persisted(data), "_data": data})
    want = request.args.get("file", "")
    if not _re.fullmatch(r"backtest_results[\w\-]*\.json", want or "x"):
        want = ""
    cur = next((r for r in runs if r["file"] == want), runs[0])
    days = sorted(cur["_data"].get("days", {}).values(), key=lambda d: d["target_session"])
    return jsonify({
        "runs": [{k: r[k] for k in ("file", "meta", "summary")} for r in runs],
        "current": cur["file"], "meta": cur["meta"], "summary": cur["summary"],
        "days": [{k: d.get(k) for k in ("target_session", "predicted", "confidence", "actual",
                                         "actual_change_pct", "correct", "baselines", "rule_risk_hint",
                                         "rule_direction_hint", "up_probability", "outlook")} for d in days],
    })


@app.route("/api/events")
def events():
    stock = request.args.get("stock") or None
    rows = query_events(stock_name=stock, db_path=Config.EVENT_ARCHIVE_DB_PATH)
    return jsonify(rows)


@app.route("/api/events/stocks")
def event_stocks():
    return jsonify(list_stock_names(db_path=Config.EVENT_ARCHIVE_DB_PATH))


@app.route("/api/events/<int:event_id>/news")
def event_news(event_id):
    rows = query_news_for_event(event_id, db_path=Config.EVENT_ARCHIVE_DB_PATH)
    return jsonify(rows)


@app.route("/api/collect", methods=["POST"])
def collect():
    body = request.get_json(silent=True) or {}
    try:
        name = str(body.get("stock_name", "")).strip()
        if not _STOCK_NAME_RE.match(name):
            return jsonify({"error": "종목명 형식이 올바르지 않습니다."}), 400
        start = date.fromisoformat(str(body["start"]))
        end = date.fromisoformat(str(body["end"]))
        if not (start <= end <= date.today() + timedelta(days=1)):
            return jsonify({"error": "기간이 올바르지 않습니다."}), 400
        if (end - start).days > _MAX_COLLECT_DAYS:
            return jsonify({"error": f"수집 기간은 최대 {_MAX_COLLECT_DAYS}일입니다."}), 400
        threshold = float(body.get("threshold", 3.0))
        if not (0.5 <= threshold <= 30):
            return jsonify({"error": "임계값은 0.5~30% 사이여야 합니다."}), 400
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "요청 값이 올바르지 않습니다."}), 400

    try:
        result = collect_and_store(name, start, end, threshold_pct=threshold,
                                   db_path=Config.EVENT_ARCHIVE_DB_PATH)
        return jsonify(result)
    except Exception as e:
        return _safe_error(e, 400)


@app.route("/api/run", methods=["POST"])
def run_pipeline():
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "demo")
    if mode not in ("demo", "real"):
        return jsonify({"error": "mode는 demo 또는 real 이어야 합니다."}), 400

    if mode == "demo":
        try:
            memory = MemoryStore("./demo_memory.json")
            pipeline = TradingAgentsPipeline(MockLLMClient(), memory, debate_max_rounds=3,
                                              data_provider=_DemoDataProvider())
            return jsonify(pipeline.run())
        except Exception as e:
            return _safe_error(e)

    if not _has_real_api_key():
        return jsonify({"error": f"{Config.required_key_name()}가 설정되지 않았습니다. "
                                 f"터미널에서 `python cli.py set-key {Config.LLM_PROVIDER}`로 등록하세요."}), 400
    # (4) 실제 분석은 동시에 하나만 (버튼 연타/중복 호출로 인한 과금 폭주 방지)
    if not _RUN_LOCK.acquire(blocking=False):
        return jsonify({"error": "이미 실제 분석이 실행 중입니다. 끝난 뒤 다시 시도하세요."}), 429
    try:
        memory = MemoryStore(Config.MEMORY_DB_PATH)
        pipeline = TradingAgentsPipeline(_real_llm(), memory, debate_max_rounds=Config.DEBATE_MAX_ROUNDS)
        outcome = pipeline.run()
        save_last_outcome(outcome, Config.LAST_OUTCOME_PATH)
        return jsonify(outcome)
    except Exception as e:
        return _safe_error(e)
    finally:
        _RUN_LOCK.release()


if __name__ == "__main__":
    print(f"TradingAgents-KR 대시보드: http://127.0.0.1:{Config.WEB_PORT}"
          + ("  [보기 전용 모드: 분석 실행/수집 비활성화]" if Config.WEB_READONLY else ""))
    # (1) 반드시 127.0.0.1 — 0.0.0.0으로 바꾸면 같은 네트워크의 누구나 API 키로 분석을 돌릴 수 있다
    app.run(host=Config.WEB_HOST, port=Config.WEB_PORT, debug=False)
