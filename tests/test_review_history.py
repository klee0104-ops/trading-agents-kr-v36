import json
from memory.review_history import review_history
from memory.store import MemoryStore, PredictionRecord


def test_audit_history_is_allowlisted_and_does_not_change_scores(tmp_path):
    memory = MemoryStore(str(tmp_path / 'memory_store.json'))
    memory.save(PredictionRecord('2026-01-02', '2026-01-01', 'up', '보통', verified=True, correct=True))
    before = (tmp_path / 'memory_store.json').read_bytes()
    (tmp_path / 'memory_store_superseded.json').write_text(json.dumps([
        {'target_date':'2026-01-02','direction':'down','secret':'must not leave file'}]), encoding='utf-8')
    lessons = tmp_path / 'lessons.json'
    lessons.write_text(json.dumps([
        {'target_date':'2026-01-01','observational':True,'correct':False,'lesson':'private detail'},
        {'target_date':'2026-01-02','observational':False,'correct':True}]), encoding='utf-8')
    audit = review_history(memory.path, lessons)
    assert audit['archived'] == [{'target_date':'2026-01-02','direction':'down'}]
    assert audit['observations'] == [{'target_date':'2026-01-01','correct':False}]
    assert memory.compute_stats()['accuracy'] == 1
    assert (tmp_path / 'memory_store.json').read_bytes() == before


def test_missing_audit_files_are_not_created(tmp_path):
    assert review_history(tmp_path/'memory.json', tmp_path/'lessons.json') == {
        'archived':[], 'observations':[], 'unavailable':False}
    assert not list(tmp_path.iterdir())


def test_corrupt_audit_file_is_reported(tmp_path):
    (tmp_path/'lessons.json').write_text('{', encoding='utf-8')
    assert review_history(None, tmp_path/'lessons.json')['unavailable'] is True
