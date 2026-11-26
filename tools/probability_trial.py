"""Five non-recording Trader judgments from one saved context; paid calls only with --live."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path


def trial(llm, saved: dict, repetitions: int = 5) -> dict:
    from agents.trader import run_trader
    from agents.moderator import build_moderation_text

    if repetitions != 5:
        raise ValueError('Exactly five judgments required')
    outcome = saved.get('outcome') or {}
    analyst = outcome.get('analyst_report') or {}
    transcript = outcome.get('debate_transcript') or []
    risk = outcome.get('risk_assessment') or {}
    if not analyst or not transcript or not risk:
        raise ValueError('Saved analyst/debate/risk context is incomplete')
    watch = [r.get('name') for r in (outcome.get('news_universe') or []) if not r.get('core')]
    probabilities = []
    for _ in range(repetitions):
        result = run_trader(llm, analyst, transcript, risk,
            verification=outcome.get('verification'), use_web_search=False,
            rule_context=json.dumps(outcome.get('market_analysis') or {}, ensure_ascii=False)[:4000],
            moderation_text=build_moderation_text(outcome.get('moderation') or {}),
            watch_names=[name for name in watch if isinstance(name, str)])
        p = (result.get('kospi_prediction') or {}).get('up_probability')
        if type(p) not in (int, float) or not 0 <= p <= 100:
            raise ValueError('Trader probability missing or invalid')
        probabilities.append(p)
    return {'context_date': outcome.get('target_date'), 'judgments': repetitions,
            'probabilities': probabilities,
            'exact_fifty_ratio_pct': 100 * sum(p == 50 for p in probabilities) / repetitions,
            'goal_pct': 10, 'goal_met': sum(p == 50 for p in probabilities) / repetitions <= .10,
            'scope': 'same saved context, no forecast or account record changed; not predictive performance'}


def main() -> int:
    from config import Config
    from memory.outcome_store import load_last_outcome

    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Authorize exactly five paid Trader calls')
    args = parser.parse_args()
    if not args.live:
        print('Add --live for five paid, non-recording Trader calls')
        return 0
    saved = load_last_outcome(Config.LAST_OUTCOME_PATH)
    if not saved or saved.get('mode') != 'real':
        raise ValueError('A saved real analysis is required')
    from cli import _real_llm
    result = trial(_real_llm(), saved)
    result['at'] = datetime.now().astimezone().isoformat(timespec='seconds')
    result['model_provider'] = Config.LLM_PROVIDER
    folder = Path(Config.LAST_OUTCOME_PATH).parent / 'logs'
    folder.mkdir(exist_ok=True)
    path = folder / ('probability_trial_' + result['at'][:10].replace('-', '') + '.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Probability trial: ' + str(result['probabilities']) +
          ' · exact 50 ratio ' + str(result['exact_fifty_ratio_pct']) + '%')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
