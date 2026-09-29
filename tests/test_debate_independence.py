"""Offline adversarial regression tests; no provider clients or user records."""
from copy import deepcopy
import pytest
from graph.debate_loop import run_debate_loop
from agents.qa_verifier import verify_debate_evidence, verify_final_result


def test_each_round_uses_same_prior_history(monkeypatch):
    seen = []
    def speak(role, history):
        seen.append((role, deepcopy(history)))
        # Even a misbehaving role must not alter the other role's input.
        history.append({"argument": "mutation"})
        return {"stance": role, "argument": role, "evidence": [role], "confidence": .6}
    monkeypatch.setattr('graph.debate_loop.run_bull', lambda l, a, h: speak('bull', h))
    monkeypatch.setattr('graph.debate_loop.run_bear', lambda l, a, h: speak('bear', h))
    transcript = run_debate_loop(None, {}, max_rounds=2)
    assert seen[0][1] == seen[1][1] == []
    assert seen[2][1] == seen[3][1] == transcript[:2]
    assert len(transcript) == 4


def test_equal_confidence_does_not_stop_new_evidence(monkeypatch):
    def speak(role, history):
        return {"stance": role, "argument": role, "evidence": [str(len(history))], "confidence": .6}
    monkeypatch.setattr('graph.debate_loop.run_bull', lambda l, a, h: speak('bull', h))
    monkeypatch.setattr('graph.debate_loop.run_bear', lambda l, a, h: speak('bear', h))
    assert len(run_debate_loop(None, {}, max_rounds=3)) == 6


@pytest.mark.parametrize('confidence', [None, 'bad', float('nan'), float('inf'), 2])
def test_malformed_confidence_never_counts_as_stable(monkeypatch, confidence):
    def speak(role):
        return {"stance": role, "argument": role, "evidence": ['a'], "confidence": confidence}
    monkeypatch.setattr('graph.debate_loop.run_bull', lambda *a: speak('bull'))
    monkeypatch.setattr('graph.debate_loop.run_bear', lambda *a: speak('bear'))
    assert len(run_debate_loop(None, {}, max_rounds=3)) == 6


class Reply:
    def __init__(self, result):
        self.result = result
        self.prompts = []
    def complete_json(self, system, user, **kwargs):
        self.prompts.append(user)
        return deepcopy(self.result)


@pytest.mark.parametrize('quant', [None, {} , {'labels': {}}])
@pytest.mark.parametrize('items', [[], None, [{'evidence': 'a', 'verified': 'true'}],
    [{'evidence': 'invented', 'verified': True}],
    [{'evidence': 'a', 'verified': True}, {'evidence': 'a', 'verified': True}]])
def test_missing_or_ambiguous_qa_never_passes(items, quant):
    llm = Reply({'results': items})
    out = verify_debate_evidence(llm, [{'stance': 'bull', 'evidence': ['a', 'b']}], {}, quant=quant)
    assert out[0]['verified_count'] == 0
    assert out[0]['unverified'] == ['a', 'b']
    assert len(llm.prompts) == 1


def test_partial_qa_only_credits_explicit_true():
    llm = Reply({'results': [{'evidence': 'a', 'verified': True}]})
    out = verify_debate_evidence(llm, [{'stance': 'bull', 'evidence': ['a', 'b', 'b']}], {}, quant={'labels': {}})
    assert out[0]['verified_count'] == 1
    assert out[0]['total_count'] == 3
    assert out[0]['unverified'] == ['b']


@pytest.mark.parametrize('result', [{}, {'consistent': 'true', 'issues': []},
    {'consistent': True, 'issues': 'bad'}, {'consistent': True, 'issues': ['conflict']}])
def test_invalid_final_qa_is_not_success(result):
    assert verify_final_result(Reply(result), {}, [], {})['consistent'] is False


def test_final_qa_receives_raw_sources_before_conclusions():
    from rules.citations import build_registry
    registry = build_registry(None, None, ['[2026-09-29][삼성전자][test] 원자료'], 'rss')
    llm = Reply({'consistent': True, 'issues': []})
    verify_final_result(llm, {}, [], {}, source_registry=registry,
                        debate_qa=[{'unverified': ['REJECTED_FACT']}])
    prompt = llm.prompts[0]
    assert '원자료' in prompt and '[N1]' in prompt and 'REJECTED_FACT' in prompt
    assert prompt.index('[원자료:') < prompt.index('[최종 결론]')
    assert len(llm.prompts) == 1
