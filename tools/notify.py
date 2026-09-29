"""아침 자동 분석 결과 알림.

last_outcome.json 을 읽어 한 줄 요약을 만들고
  1) 아침요약.txt 로 저장
  2) Windows 알림(토스트) 표시 (실패 시 메시지 박스)
  3) site/index.html (읽기 전용 결과 페이지) 를 기본 브라우저로 열기
LLM 호출 없음. 키 접근 없음.

사용: python tools/notify.py            -> 성공 알림
      python tools/notify.py --failed   -> 실패 알림 (로그 경로 안내)
"""
from __future__ import annotations
import json, os, subprocess, sys, webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTCOME = ROOT / os.environ.get("LAST_OUTCOME_PATH", "last_outcome.json")
SITE = ROOT / "site" / "index.html"
SUMMARY_TXT = ROOT / "아침요약.txt"
TITLE = "TradingAgents-KR 아침 분석"

_DIR = {"up": "상승", "down": "하락", "flat": "보합"}


def build_summary() -> tuple[str, str]:
    """(짧은 알림용, 긴 파일용) 요약 문자열."""
    d = json.loads(OUTCOME.read_text(encoding="utf-8"))
    o = d.get("outcome", d)
    saved = d.get("saved_at", "")[:16].replace("T", " ")
    fr = o.get("final_result", {})
    kp = fr.get("kospi_prediction", {})
    direction = _DIR.get(kp.get("direction"), str(kp.get("direction")))
    prob = kp.get("up_probability")
    outlook = kp.get("outlook_label") or kp.get("outlook") or ""
    conf = kp.get("confidence", "")
    target = o.get("target_date", "")
    risk = (o.get("risk_assessment") or {}).get("risk_level", "")
    ver = o.get("verification") or {}
    passed = "통과" if ver.get("verification_passed") else "주의"

    head = f"KOSPI {target} {direction}"
    if prob is not None:
        head += f" (상승확률 {float(prob):.0f}%)"
    if outlook:
        head += f" · {outlook}"
    short = f"{head}\n신뢰도 {conf} · 리스크 {risk} · 검증 {passed}"

    lines = [f"[{TITLE}] {saved}", head,
             f"신뢰도: {conf}   리스크: {risk}   검증: {passed}", ""]
    for r in kp.get("reasoning", [])[:3]:
        lines.append(f" - {r}")
    stocks = fr.get("watch_stocks") or []
    if stocks:
        lines += ["", "관심 종목:"]
        for s in stocks:
            sig = _DIR.get(s.get("directional_signal"), s.get("directional_signal"))
            tag = " (중재자 조정)" if s.get("moderated") else ""
            lines.append(f" - {s.get('name')}: {sig}{tag}")
    up = sum(1 for s in stocks if s.get("directional_signal") == "up")
    dn = sum(1 for s in stocks if s.get("directional_signal") == "down")
    if stocks:
        short += f"\n관심종목 {len(stocks)}개: 상승 {up} / 하락 {dn} / 보합 {len(stocks)-up-dn}"
    return short, "\n".join(lines)


def toast(title: str, body: str) -> bool:
    """Windows 10/11 토스트 알림. 추가 모듈 불필요."""
    if os.name != "nt":
        print(f"[알림] {title}\n{body}")
        return True
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    ps = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = @"
<toast duration="long"><visual><binding template="ToastGeneric">
<text>{esc(title)}</text><text>{esc(body)}</text>
</binding></visual></toast>
"@
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$appId = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
    (New-Object Windows.UI.Notifications.ToastNotification $doc))
"""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True
    except Exception:
        pass
    # 폴백: 일반 메시지 박스 (토스트가 막힌 환경)
    ps2 = ("Add-Type -AssemblyName System.Windows.Forms; "
           f"[System.Windows.Forms.MessageBox]::Show(@'\n{body}\n'@, '{title}') | Out-Null")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps2])
        return True
    except Exception:
        return False


def main() -> int:
    failed = "--failed" in sys.argv
    if failed:
        log = next((a for a in sys.argv[1:] if not a.startswith("--")), "logs 폴더")
        toast(TITLE + " 실패", f"오늘 분석이 실패했습니다. 로그: {log}\n(API 일일 한도(429)면 잠시 후 실제분석_로그실행.bat 로 다시 실행)")
        return 1
    try:
        short, long_ = build_summary()
    except Exception as e:  # 결과 파일이 없거나 깨짐
        toast(TITLE, f"결과 파일을 읽지 못했습니다: {e}")
        return 1
    SUMMARY_TXT.write_text(long_ + "\n", encoding="utf-8")
    print(long_)
    toast(TITLE, short)
    if SITE.exists() and "--no-open" not in sys.argv:
        webbrowser.open(SITE.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
