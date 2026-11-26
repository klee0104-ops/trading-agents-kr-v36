from agents.base import MockLLMClient
from tools.probability_trial import trial


def test_five_judgments_never_write_a_prediction_record(tmp_path):
    saved = {'mode': 'real', 'outcome': {
        'target_date': '2026-10-22',
        'analyst_report': {'summary': '합성 시장 자료'},
        'debate_transcript': [{'stance': 'bull', 'round': 1, 'argument': '합성 상승 주장'}],
        'risk_assessment': {'risk_level': '보통', 'summary': '합성 위험 검토'},
        'news_universe': []}}
    before = list(tmp_path.iterdir())
    result = trial(MockLLMClient(), saved)
    assert result['judgments'] == 5
    assert len(result['probabilities']) == 5
    assert list(tmp_path.iterdir()) == before
