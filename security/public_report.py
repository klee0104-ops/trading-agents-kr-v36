"""Fail-closed publication checks. Never print matched secret values."""
from __future__ import annotations
import base64
import hashlib
import html
from html.parser import HTMLParser
import json
import re
from urllib.parse import unquote
from security.secret_store import SECRET_PATTERNS

EXTRA_PATTERNS = [re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}'),
                  re.compile(r'github_pat_[A-Za-z0-9_]{30,}'),
                  re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
                  re.compile(r'Bearer\s+[A-Za-z0-9_.-]{20,}', re.I),
                  # (V3.33) 개인정보: 개인 메일 주소(언론사 기자 메일은 제외), PC 사용자 경로, 계좌·주민번호·휴대폰 형태
                  re.compile(r'[A-Za-z0-9._%+-]+@(?:gmail|naver|daum|hanmail|kakao|icloud|outlook|hotmail|nate|yahoo)\.(?:com|net|co\.kr)', re.I),
                  re.compile(r'[A-Za-z]:\\{1,2}Users\\{1,2}|/Users/[A-Za-z]|/home/[a-z][a-z0-9_-]*/', re.I),
                  re.compile(r'\b\d{8}-\d{2}\b|\b\d{6}-[1-4]\d{6}\b|\b01[016789]-\d{3,4}-\d{4}\b')]
SENSITIVE = re.compile(r'api[_-]?key|app[_-]?key|secret|password|credential|authorization|access[_-]?token|refresh[_-]?token|session[_-]?token|account[_-]?(?:no|num|number)|(?:^|_)(?:acnt|cano)(?:_|$)|계좌번호|kiwoom', re.I)

class PublicationBlocked(ValueError):
    pass

def configured_secrets():
    from security.secret_store import KEY_NAMES, get_api_key, _read_dotenv, _PROCESS_ENV_SNAPSHOT, _PLACEHOLDER_RE
    values = [get_api_key(provider).reveal() for provider in KEY_NAMES]
    # Include shadowed dotenv/environment credentials, not just the active provider.
    for source in (_read_dotenv(), _PROCESS_ENV_SNAPSHOT):
        values.extend(str(value) for key, value in source.items() if SENSITIVE.search(key) and value)
    return {v for v in values if v and not _PLACEHOLDER_RE.search(v)}

def check_text(text, secrets=()):
    for decoded in (text, html.unescape(text), unquote(text)):
        if any(p.search(decoded) for p in [*SECRET_PATTERNS, *EXTRA_PATTERNS]):
            raise PublicationBlocked('Secret pattern detected; publication stopped')
        if any(value and value in decoded for value in secrets):
            raise PublicationBlocked('Configured credential detected; publication stopped')

def check_data(value, secrets):
    if isinstance(value, dict):
        for key, item in value.items():
            if SENSITIVE.search(key):
                raise PublicationBlocked('Credential field detected; publication stopped')
            check_text(key, secrets)
            check_data(item, secrets)
    elif isinstance(value, list):
        for item in value: check_data(item, secrets)
    elif isinstance(value, str):
        check_text(value, secrets)
        # The legacy renderer has numeric/attribute interpolation paths. Do not
        # allow markup payloads into those paths even with the restrictive CSP.
        if re.search(r'<\s*/?\s*[A-Za-z!]|(?:javascript|vbscript)\s*:', value, re.I):
            raise PublicationBlocked('Markup payload in report data')

class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts = []
        self.current = None
        self.external = False
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in {'iframe', 'object', 'embed', 'base', 'form'}:
            self.external = True
        if any(k.lower().startswith('on') for k in a): self.external = True
        if tag == 'script':
            if 'src' in a: self.external = True
            self.current = [a, '']
        if tag == 'link' and a.get('rel') in {'stylesheet', 'preconnect', 'prefetch'}:
            self.external = True
    def handle_data(self, data):
        if self.current is not None: self.current[1] += data
    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            self.scripts.append(self.current)
            self.current = None

def validate_report(page, secrets=()):
    from web.export_site import render_fragment
    if len(page.encode('utf-8')) > 900_000:
        raise PublicationBlocked('Report exceeds publication size limit')
    check_text(page, secrets)
    parsed = Scripts(); parsed.feed(page)
    if parsed.external or parsed.current is not None:
        raise PublicationBlocked('Unexpected active or external content')
    payloads = [s for a, s in parsed.scripts if a.get('id') == 'ta-data' and a.get('type') == 'application/json']
    if len(payloads) != 1:
        raise PublicationBlocked('Exactly one report data block is required')
    try:
        data = json.loads(payloads[0])
    except (ValueError, TypeError):
        raise PublicationBlocked('Invalid report data') from None
    if not isinstance(data, dict): raise PublicationBlocked('Invalid report object')
    check_data(data, secrets)
    expected = Scripts(); expected.feed(render_fragment(data))
    if parsed.scripts != expected.scripts:
        raise PublicationBlocked('Scripts do not match the approved report renderer')
    for attrs, script in parsed.scripts:
        if attrs.get('type') == 'application/json': continue
        if re.search(r'\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|eval)\b|sendBeacon|/api/|X-TA-Token', script):
            raise PublicationBlocked('Network or execution API found in shared renderer')
    return parsed

def harden_report(page, secrets=()):
    parsed = validate_report(page, secrets)
    hashes = ["'sha256-" + base64.b64encode(hashlib.sha256(script.encode('utf-8')).digest()).decode() + "'"
              for attrs, script in parsed.scripts if attrs.get('type') != 'application/json']
    policy = "default-src 'none'; script-src " + ' '.join(hashes) + "; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
    head = '<meta http-equiv="Content-Security-Policy" content="' + html.escape(policy, quote=True) + '">\n'
    head += '<meta name="referrer" content="no-referrer">\n<meta name="robots" content="noindex,nofollow">\n'
    head += '<meta http-equiv="refresh" content="300">\n'
    if '<head>' not in page: raise PublicationBlocked('Missing HTML head')
    return page.replace('<head>', '<head>\n' + head, 1)
