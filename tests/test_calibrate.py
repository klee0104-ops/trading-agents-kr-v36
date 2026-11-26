from rules.calibrate import fit_calibration, apply_calibration


def _recs(n_hi, up_hi):
    rows = []
    for i in range(n_hi):
        rows.append({"verified": True, "actual_direction": "up" if i < up_hi else "down",
                     "up_probability_raw": 70.0, "target_date": f"2026-08-{i+1:02d}"})
    return rows


def test_identity_below_min_records():
    m = fit_calibration(_recs(10, 5))
    r = apply_calibration(70, m)
    assert r["p"] == 70 and not r["applied"]


def test_shrinks_overconfident_bin():
    m = fit_calibration(_recs(25, 12))          # 70% 구간에서 실제 48%
    r = apply_calibration(70, m)
    assert r["applied"] and 55 <= r["p"] < 70   # (25*48 + 10*70)/35 = 54.3 → MAX_SHIFT 15 → 55
    assert r["p"] == 55.0
    r2 = apply_calibration(30, m)               # 표본 없는 구간 → 그대로
    assert r2["p"] == 30 and not r2["applied"]


def test_as_of_excludes_future():
    m = fit_calibration(_recs(25, 12), as_of="2026-08-05")
    assert m["total"] == 4 and not m["active"]


def test_early_calibration_is_active_but_shift_is_limited():
    for count in (12, 19):
        assert not fit_calibration(_recs(count, 0))["active"]
        m = fit_calibration(_recs(count, 0), experimental=True)
        result = apply_calibration(70, m)
        assert m["active"] and result["applied"]
        assert 70 - 15 * count / 20 <= result["p"] < 70
