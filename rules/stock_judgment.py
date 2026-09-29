"""Completeness gate for NEW stock judgments; never migrate historical forecasts.

This checks required structured evidence explanations, not factual truth.
Existing source QA and moderator review remain necessary.
"""
def validate_stocks(result):
    for key in ('core_stocks', 'watch_stocks'):
        for stock in result.get(key) or []:
            if not isinstance(stock, dict):
                continue
            valid = (
                stock.get('assessment_status') == 'assessed'
                and stock.get('directional_signal') in ('up', 'down', 'flat')
                and stock.get('direction_bias') in ('up', 'down', 'neutral')
                and stock.get('bias_confidence') in ('low', 'mid', 'high')
                and all(isinstance(stock.get(k), str) and 3 <= len(stock[k].strip()) <= 1200
                        for k in ('reason', 'bias_reason'))
            )
            if stock.get('directional_signal') in ('up', 'down'):
                valid = valid and stock.get('direction_bias') == stock['directional_signal']
            stock['assessment_status'] = 'assessed' if valid else 'held'
            stock['assessment_note'] = '필수 판단 항목 확인 · 사실 검증 보증 아님' if valid else '근거 부족 또는 필수 판단 누락'
            if not valid:
                stock['direction_bias'] = 'unknown'
                stock['bias_confidence'] = 'unknown'
    return result
