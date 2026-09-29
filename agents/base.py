"""
LLM 클라이언트 추상화.

모든 에이전트는 이 인터페이스(LLMClient)에만 의존한다. 그래서:
- 실제 운영 시: AnthropicLLMClient 또는 GeminiLLMClient (진짜 API 호출, 웹 검색 도구 사용)
  (API 키는 SecretStr로 받고, SDK 예외 메시지는 마스킹해서 LLMCallError로 다시 던진다)
- 테스트/개발 시: MockLLMClient (네트워크 없이 정해진 답을 돌려줌)
를 그냥 교체만 하면 전체 파이프라인이 동일하게 동작한다.
"""
from __future__ import annotations
import json
import re
from abc import ABC, abstractmethod
from typing import Optional, Union

from security.secret_store import SecretStr, redact, register_runtime_secret


class LLMCallError(RuntimeError):
    """SDK 예외를 감싼 에러. 메시지는 항상 마스킹되어 있고, 원본 예외(요청 헤더 등
    민감정보를 담을 수 있음)는 체인에서 끊어 traceback에 노출되지 않게 한다."""


def _secret_value(api_key: Union[str, SecretStr, None]) -> str:
    if isinstance(api_key, SecretStr):
        return api_key.reveal()
    return (api_key or "").strip()


def extract_json(text: str) -> dict:
    """
    응답 텍스트에서 첫 번째로 완결되는 JSON 객체만 정확히 추출한다.
    (참고: HTML 프론트엔드 아티팩트에서 쓴 것과 동일한 괄호-균형 매칭 방식.
     text.rfind('}') 같은 naive한 방식은 검색 결과 인용구 등에 중괄호가 섞이면
     엉뚱한 범위를 잘라내 파싱이 깨지는 문제가 있었다.)
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("응답에서 JSON 시작 지점을 찾지 못했습니다: " + text[:200])

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                return json.loads(candidate)

    raise ValueError("완결된 JSON 객체를 찾지 못했습니다 (응답이 잘렸을 수 있음)")


class LLMClient(ABC):
    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int = 1500,
                 use_web_search: bool = False) -> str:
        """system/user 프롬프트를 받아 모델의 텍스트 응답을 반환한다."""
        raise NotImplementedError

    def complete_json(self, system: str, user: str, max_tokens: int = 1500,
                       use_web_search: bool = False) -> dict:
        text = self.complete(system, user, max_tokens=max_tokens, use_web_search=use_web_search)
        try:
            return extract_json(text)
        except (ValueError, json.JSONDecodeError):
            # 응답이 잘렸거나 JSON이 깨진 경우 1회만 재시도 (토큰 여유를 늘리고 JSON만 요구)
            text = self.complete(system + "\n\n반드시 완결된 JSON 객체 하나만 출력하세요. 설명 문장은 쓰지 마세요.",
                                 user, max_tokens=int(max_tokens * 1.5), use_web_search=use_web_search)
            return extract_json(text)


class AnthropicLLMClient(LLMClient):
    """실제 Anthropic API를 호출하는 클라이언트. ANTHROPIC_API_KEY 필요."""

    def __init__(self, api_key: Union[str, SecretStr], model: str = "claude-sonnet-4-6"):
        key = _secret_value(api_key)
        if not key:
            raise ValueError("ANTHROPIC_API_KEY가 설정되지 않았습니다 (python cli.py set-key anthropic)")
        try:
            import anthropic
        except ImportError as e:
            raise ImportError(
                "anthropic 패키지가 필요합니다: pip install anthropic"
            ) from e
        register_runtime_secret(key)
        self._client = anthropic.Anthropic(api_key=key)
        self.model = model

    def __repr__(self) -> str:  # 클라이언트 객체를 출력해도 키가 보이지 않게
        return f"AnthropicLLMClient(model={self.model!r})"

    def complete(self, system: str, user: str, max_tokens: int = 1500,
                 use_web_search: bool = False) -> str:
        tools = [{"type": "web_search_20250305", "name": "web_search"}] if use_web_search else None
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if tools:
            kwargs["tools"] = tools

        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as e:
            raise LLMCallError(f"Anthropic API 호출 실패: {type(e).__name__}: {redact(e)}") from None
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "\n".join(parts)


class GeminiLLMClient(LLMClient):
    """실제 Google Gemini API를 호출하는 클라이언트. GEMINI_API_KEY 필요."""

    def __init__(self, api_key: Union[str, SecretStr], model: str = "gemini-flash-lite-latest",
                 web_search_enabled: bool = True):
        key = _secret_value(api_key)
        if not key:
            raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다 (python cli.py set-key gemini)")
        try:
            from google import genai
        except ImportError as e:
            raise ImportError(
                "google-genai 패키지가 필요합니다: pip install google-genai"
            ) from e
        register_runtime_secret(key)
        self._client = genai.Client(api_key=key)
        self.model = model
        # 계정에 Search grounding 쿼터가 없을 때 .env(GEMINI_WEB_SEARCH_ENABLED=false)로
        # 끌 수 있도록 하는 스위치. 쿼터가 열리면 다시 true로 바꾸면 된다.
        self.web_search_enabled = web_search_enabled

    def __repr__(self) -> str:
        return f"GeminiLLMClient(model={self.model!r})"

    def complete(self, system: str, user: str, max_tokens: int = 1500,
                 use_web_search: bool = False) -> str:
        from google.genai import types

        use_web_search = use_web_search and self.web_search_enabled
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            tools=[types.Tool(google_search=types.GoogleSearch())] if use_web_search else None,
            # 기본적으로 켜진 "thinking" 모드가 max_output_tokens 예산을 추론에
            # 다 써버려 실제 답변이 잘리는 문제가 있어 최소 수준으로 낮춘다.
            # (thinking_budget=0은 이 모델 계열에서 400 INVALID_ARGUMENT가 남)
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
        )
        # 무료 요금제 분당 요청 제한(RPM) 대비: 호출 사이 최소 간격 (GEMINI_MIN_INTERVAL_SEC)
        import os, time
        gap = float(os.environ.get("GEMINI_MIN_INTERVAL_SEC", "0") or 0)
        last = getattr(self, "_last_call_ts", 0.0)
        if gap > 0 and time.time() - last < gap:
            time.sleep(gap - (time.time() - last))
        self._last_call_ts = time.time()
        try:
            resp = self._call_with_retry(config, user)
        except Exception as e:
            raise LLMCallError(f"Gemini API 호출 실패: {type(e).__name__}: {redact(e)}") from None
        return resp.text or ""

    def _call_with_retry(self, config, user: str, max_retries: int = 6):
        """
        Gemini 무료 티어는 모델당 분당 요청 수(RPM)가 매우 낮아(예: 5 RPM),
        토론 루프처럼 짧은 시간에 여러 번 호출하면 바로 429가 난다.
        서버가 응답에 실어 보내는 retryDelay만큼 기다렸다가 재시도한다.
        """
        import time
        from google.genai import errors as genai_errors

        for attempt in range(max_retries):
            try:
                return self._client.models.generate_content(
                    model=self.model,
                    contents=user,
                    config=config,
                )
            except genai_errors.ClientError as e:
                if e.code != 429 or attempt == max_retries - 1:
                    raise
                time.sleep(self._retry_delay_seconds(e))

    @staticmethod
    def _retry_delay_seconds(e, default: float = 20.0) -> float:
        try:
            for d in e.details["error"]["details"]:
                if d.get("@type", "").endswith("RetryInfo"):
                    return float(d["retryDelay"].rstrip("s")) + 1
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
        return default


def build_llm_client(provider: str, anthropic_key: Union[str, SecretStr] = "",
                      anthropic_model: str = "claude-sonnet-4-6",
                      gemini_key: Union[str, SecretStr] = "",
                      gemini_model: str = "gemini-flash-lite-latest",
                      gemini_web_search_enabled: bool = True) -> LLMClient:
    """
    provider 문자열("anthropic" | "gemini")에 맞는 LLMClient를 만들어 반환한다.
    파이프라인/CLI는 이 함수 하나만 알면 되고, 각 프로바이더의 SDK 세부사항은 몰라도 된다.
    """
    provider = (provider or "anthropic").lower().strip()
    if provider == "gemini":
        return GeminiLLMClient(api_key=gemini_key, model=gemini_model,
                                web_search_enabled=gemini_web_search_enabled)
    if provider == "anthropic":
        return AnthropicLLMClient(api_key=anthropic_key, model=anthropic_model)
    raise ValueError(f"지원하지 않는 LLM_PROVIDER입니다: {provider} (anthropic 또는 gemini만 가능)")


class MockLLMClient(LLMClient):
    """
    네트워크 없이 파이프라인 로직(특히 토론 루프)을 검증하기 위한 목 클라이언트.
    role_responses에 "역할 키워드 -> 반환할 JSON 문자열" 을 등록해서 사용한다.
    실제 프로덕션에서는 쓰지 않는다.
    """

    def __init__(self):
        self.call_log: list[dict] = []
        self._counter = 0

    def complete(self, system: str, user: str, max_tokens: int = 1500,
                 use_web_search: bool = False) -> str:
        self._counter += 1
        self.call_log.append({
            "n": self._counter,
            "system_head": system[:60],
            "user_head": user[:60],
        })
        return self._route(system, user)

    @staticmethod
    def _cite(user: str, name, kind: str = "M") -> str:
        """(V3.24) 프롬프트의 근거 자료 목록에서 해당 항목의 ID를 찾아 ' [M3]' 형태로 붙인다. 없으면 빈 문자열."""
        import re as _re
        pat = rf"\[({kind}\d+)\] " + (_re.escape(name) + r"\b" if name else "")
        m = _re.search(pat, user)
        return f" [{m.group(1)}]" if m else ""

    def _bear_evidence(self, user: str) -> str:
        """자료에 VIX 가 있으면 VIX, 없으면(ID 목록이 있을 때) SOX 를 근거로 든다 — 없는 자료를 인용하지 않는 mock."""
        vix = self._cite(user, "VIX")
        if vix or not self._cite(user, "SOX"):
            return f"VIX 등락 +2.00%{vix}"
        return f"SOX 등락 +1.10%{self._cite(user, 'SOX')}"

    def _route(self, system: str, user: str) -> str:
        s = system.lower()
        # 주의: "bull"/"bear" 영단어로 체크하면 안 된다. 각 프롬프트가 상대방을
        # "(Bull)"/"(Bear)"로 언급하기 때문에 서로의 프롬프트에도 그 단어가 섞여있어
        # 항상 첫 번째 조건(bull)에 잘못 걸린다. 겹치지 않는 한글 마커를 쓴다.
        #
        # QA 검증팀 라우팅이 "검증" 라우팅보다 먼저 와야 한다 — QA 프롬프트에도
        # "검증"이 들어있기 때문. QA_DEBATE/QA_FINAL 둘 다 "품질보증"이 공통으로
        # 들어있어서, 더 구체적인 조건(근거 인용 여부)을 먼저 체크해야 한다.
        if "복기(사후 검토)" in s:
            return ('{"lesson": "실현변동성이 높고 SOX 힌트가 중립이면 확률을 55 아래로 유지한다", '
                    '"cause": "미국 지수 강세만 보고 국내 수급 악화를 무시", "misleading_evidence": ["SOX 강세"], '
                    '"ignored_signals": ["외국인 순매도"], "luck": false, "confidence_note": "과신"}')
        if "종목 중재자" in s:
            # 근거 없는 첫 관심 종목 하나를 보합으로 되돌리는 시나리오
            import re as _re
            names = _re.findall(r"- (\S+): 신호=", user)
            reviews = [{"name": n, "action": "flat" if i == 2 else "keep",
                        "reason": "mock: 종목 근거 부족" if i == 2 else "mock: 근거 확인"} for i, n in enumerate(names)]
            return json.dumps({"reviews": reviews}, ensure_ascii=False)
        if "중재자" in s:
            return json.dumps({
                "agreed_facts": ["SOX가 +1.1% 상승했다", "VIX가 +2.0% 상승했다"],
                "key_disputes": [{"topic": "반도체 강세의 지속성", "bull": "HBM 수요로 지속",
                                  "bear": "VIX 상승은 조정 신호"}],
                "noise": [],
                "debate_quality": "contested",
                "stronger_side": "bull",
                "summary": "mock: 반도체 강세 근거가 조금 더 구체적이나 변동성 우려도 확인됨."
            }, ensure_ascii=False)
        if "품질보증" in s and "인용된 근거" in s:
            # 하위 QA: "[검증할 근거 목록]" 섹션 이후의 불릿만 근거로 취급한다
            # (소스 자료 안의 다른 "- " 불릿까지 세면 verified_count가 부풀려짐).
            marker = "[검증할 근거 목록]"
            section = user.split(marker, 1)[1] if marker in user else user
            evidence_lines = [l.strip("- ").strip() for l in section.split("\n") if l.strip().startswith("-")]
            results = [{"evidence": e, "verified": True, "note": "mock: 소스에서 확인됨"} for e in evidence_lines]
            return json.dumps({"results": results}, ensure_ascii=False)
        if "품질보증" in s:
            # 상위 QA
            return json.dumps({"consistent": True, "issues": []}, ensure_ascii=False)
        if "검증" in s:
            return json.dumps({
                "verification_passed": True,
                "final_risk_level": "보통",
                "notes": "mock: 규칙엔진과 상충 검토 결과 리스크 매니저 판단 승인."
            }, ensure_ascii=False)
        if "비관론자" in s:
            return json.dumps({
                "stance": "bear",
                "argument": "VIX 상승과 밸류에이션 부담을 고려하면 단기 조정 가능성이 있습니다.",
                "evidence": [self._bear_evidence(user)],
                "confidence": 0.55
            }, ensure_ascii=False)
        if "낙관론자" in s:
            return json.dumps({
                "stance": "bull",
                "argument": "미국 반도체 지수 강세와 HBM 수요 확대가 국내 반도체주에 긍정적입니다.",
                "evidence": [f"SOX 등락 +1.10%{self._cite(user, 'SOX')}"],
                "confidence": 0.62
            }, ensure_ascii=False)
        if "risk" in s:
            return json.dumps({
                "leaning": "up",
                "risk_level": "보통",
                "summary": "상반된 의견이 팽팽하나, 반도체 업황 모멘텀이 근소하게 우세합니다.",
                "recommended_confidence": "보통"
            }, ensure_ascii=False)
        if "trader" in s or "최종" in s:
            import re as _re
            marker = "[오늘의 관심 종목"
            watch_names = []
            if marker in user:
                sect = user.split(marker, 1)[1]
                watch_names = [m for m in _re.findall(r"\n- (\S+) \(관심", sect)]
            if watch_names:
                return json.dumps({
                    "kospi_prediction": {"up_probability": 64,
                                         "reasoning": ["미국 반도체 지수 강세", "VIX는 소폭 상승했으나 임계 수준 아님"]},
                    "core_stocks": [
                        {"name": "삼성전자", "related_us_indicator": "SOX", "sensitivity": "high",
                         "directional_signal": "up", "reason": "SOX 강세 동조화"},
                        {"name": "SK하이닉스", "related_us_indicator": "SOX", "sensitivity": "high",
                         "directional_signal": "up", "reason": "HBM 수요 기대"}],
                    "watch_stocks": [{"name": n, "sector": "-", "related_us_indicator": "S&P500", "sensitivity": "mid",
                                      "directional_signal": "up", "reason": f"mock: {n} 상승 근거"} for n in watch_names],
                }, ensure_ascii=False)
            return json.dumps({
                "as_of": "2026-07-24 (mock)",
                "kospi_prediction": {
                    "up_probability": 64,
                    "reasoning": ["미국 반도체 지수 강세", "VIX는 소폭 상승했으나 임계 수준 아님"]
                },
                "core_stocks": [
                    {"name": "삼성전자", "related_us_indicator": "SOX", "sensitivity": "high",
                     "directional_signal": "up", "reason": "SOX 강세 동조화"},
                    {"name": "SK하이닉스", "related_us_indicator": "SOX", "sensitivity": "high",
                     "directional_signal": "up", "reason": "HBM 수요 기대"}
                ],
                "watch_stocks": [
                    {"name": "LG에너지솔루션", "sector": "2차전지", "related_us_indicator": "나스닥",
                     "sensitivity": "mid", "directional_signal": "down", "reason": "정책 불확실성"},
                    {"name": "네이버", "sector": "IT/플랫폼", "related_us_indicator": "나스닥",
                     "sensitivity": "mid", "directional_signal": "up", "reason": "빅테크 밸류에이션 동조"},
                    {"name": "카카오", "sector": "IT/플랫폼", "related_us_indicator": "나스닥",
                     "sensitivity": "mid", "directional_signal": "up", "reason": "플랫폼 심리 회복"},
                    {"name": "현대차", "sector": "자동차", "related_us_indicator": "S&P500",
                     "sensitivity": "low", "directional_signal": "up", "reason": "관세 우려 완화"},
                    {"name": "기아", "sector": "자동차", "related_us_indicator": "S&P500",
                     "sensitivity": "low", "directional_signal": "up", "reason": "미국 판매 호조"},
                    {"name": "삼성바이오로직스", "sector": "바이오", "related_us_indicator": "나스닥",
                     "sensitivity": "low", "directional_signal": "up", "reason": "바이오텍 투자심리 개선"},
                    {"name": "한미반도체", "sector": "반도체 소부장", "related_us_indicator": "SOX",
                     "sensitivity": "high", "directional_signal": "up", "reason": "HBM 장비 수주 기대"},
                    {"name": "POSCO홀딩스", "sector": "철강", "related_us_indicator": "S&P500",
                     "sensitivity": "low", "directional_signal": "down", "reason": "원자재 가격 약세"},
                    {"name": "한화에어로스페이스", "sector": "방산", "related_us_indicator": "S&P500",
                     "sensitivity": "low", "directional_signal": "up", "reason": "수출 계약 기대"},
                    {"name": "KB금융", "sector": "금융", "related_us_indicator": "S&P500",
                     "sensitivity": "low", "directional_signal": "up", "reason": "금리 환경 우호적"}
                ]
            }, ensure_ascii=False)
        if "거시·수급 분석가" in s or "macro & flow" in s:
            return json.dumps({"direction": "down", "confidence": "낮음",
                               "summary": "원화 소폭 강세지만 외국인 순매수 규모는 제한적.",
                               "notable_points": [f"원/달러 -0.35%{self._cite(user, '원/달러')}"]}, ensure_ascii=False)
        if "반도체 분석가" in s or "semiconductor analyst" in s:
            return json.dumps({"direction": "up", "confidence": "높음",
                               "summary": "SOX 강세로 반도체 대형주 우호적.",
                               "notable_points": [f"SOX +1.10%{self._cite(user, 'SOX')}"]}, ensure_ascii=False)
        if "뉴스·심리 분석가" in s or "news sentiment" in s:
            return json.dumps({"direction": "up", "confidence": "낮음",
                               "summary": "뉴스 제목은 대체로 중립~긍정.",
                               "notable_points": [f"HBM 수주 기대 기사{self._cite(user, None, 'N')}"]}, ensure_ascii=False)
        if "기술적 분석가" in s or "technical analyst" in s:
            return json.dumps({"direction": "up", "confidence": "보통",
                               "summary": "지수 상승 모멘텀, 과매수 아님.",
                               "notable_points": [f"S&P500 +0.40%{self._cite(user, 'S&P500')}"]}, ensure_ascii=False)
        if "analyst" in s or "분석가" in s:
            return json.dumps({
                "us_indices": [
                    {"name": "S&P500", "change_pct": 0.4, "direction": "up"},
                    {"name": "SOX", "change_pct": 1.1, "direction": "up"},
                    {"name": "VIX", "change_pct": 2.0, "direction": "up"}
                ],
                "direction": "up",
                "confidence": "보통",
                "summary": "반도체 강세 속 변동성 지표도 소폭 상승."
            }, ensure_ascii=False)
        return json.dumps({"note": "unrouted mock response"}, ensure_ascii=False)
