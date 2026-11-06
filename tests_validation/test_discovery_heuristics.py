"""Phase 3: one heuristics engine for both advisors (A1-A3, M1-M3)."""

import csv
import math

import pytest

from quaxdim import DiscoveryBudget, discover_advanced
from quaxdim.advanced_discovery import ApproxCardinality
from quaxdim.heuristics import (
    HeuristicThresholds,
    dependency_evidence,
    good_turing_unseen_mass,
    hll_suggests_unique,
    min_detectable_duplicate_share,
    name_tokens,
    rule_of_three_upper,
    semantic_roles,
    start_end_pairs,
)

# --- A3: token-based roles ---------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("account_balance", ["unknown"]),  # was "count" via substring
        ("country", ["geographical"]),  # was also "count"
        ("tamaño_empresa", ["unknown"]),  # was "temporal" via "año"
        ("candidate_id", ["identifier"]),  # was also "temporal" via "date"
        ("año_corte", ["temporal"]),
        ("CODIGO_DANE", ["identifier"]),
        ("fechaInicioContrato", ["temporal"]),
        ("numero_vacantes", ["count"]),
    ],
)
def test_a3_roles_use_tokens_not_substrings(name, expected):
    assert semantic_roles(name) == expected


def test_name_tokens_split_camel_case_separators_and_accents():
    assert name_tokens("fechaInicio_Contrato") == (
        "fecha",
        "inicio",
        "contrato",
    )
    assert name_tokens("Número de Vacantes") == ("numero", "de", "vacantes")
    assert name_tokens("HTTPStatus") == ("http", "status")


def test_a3_start_end_pairs_require_the_same_event_root():
    names = [
        "fecha_inicio_contrato",
        "fecha_fin_contrato",
        "fecha_fin_vigencia",
        "fecha_financiacion",  # "fin" substring must not match
        "fecha_calendario",
        "start_date",
        "end_date",
    ]
    assert sorted(start_end_pairs(names)) == [
        ("fecha_inicio_contrato", "fecha_fin_contrato"),
        ("start_date", "end_date"),
    ]


def test_a3_advanced_report_carries_token_roles(tmp_path):
    path = tmp_path / "roles.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["account_balance", "country", "candidate_id"])
        writer.writerows([(str(i), "CO", f"{i:04d}") for i in range(30)])
    report = discover_advanced(path)
    roles = {
        c["name"]: c["semantic_roles"]
        for c in report.assets["dataset"]["columns"]
    }
    assert roles == {
        "account_balance": ["unknown"],
        "country": ["geographical"],
        "candidate_id": ["identifier"],
    }


# --- A1: blanks no longer hide unique keys ----------------------------------


def _key_recommendations(report, field):
    return [
        r
        for r in report.recommendations
        if r.evaluator == "builtin.composite_uniqueness"
        and r.fields == (field,)
    ]


def test_a1_blanks_do_not_suppress_unique_key_recommendation(tmp_path):
    path = tmp_path / "keys.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["CODE_ID", "STATUS"])
        writer.writerows([(f"{i:05d}", "A") for i in range(1000)])
        writer.writerows([("", "A")] * 5)
    report = discover_advanced(path)
    recommendations = _key_recommendations(report, "CODE_ID")
    assert recommendations, "5 blanks must not hide a 1000-value unique key"
    assert recommendations[0].evidence["non_null"] == 1000


# --- A2: HLL tolerance follows the sketch precision -------------------------


def _acceptance_rate(precision, *, unique_share, trials=40, n=2000):
    accepted = 0
    for trial in range(trials):
        sketch = ApproxCardinality(precision)
        distinct = int(n * unique_share)
        for i in range(n):
            sketch.add(f"{trial}-{i % distinct}")
        accepted += hll_suggests_unique(
            sketch.estimate(),
            n,
            sketch.relative_std_error,
            z=HeuristicThresholds().hll_z,
        )
    return accepted / trials


def test_a2_unique_keys_are_accepted_at_low_precision():
    # The fixed [0.94, 1.06] window accepted only ~27% at precision 5.
    assert _acceptance_rate(5, unique_share=1.0) >= 0.9


def test_a2_duplicates_are_rejected_at_high_precision():
    # The fixed window accepted columns with ~5% duplicates at precision 14.
    assert _acceptance_rate(14, unique_share=0.95, trials=10) == 0


# --- M1-M3: quantified sample evidence ---------------------------------------


def test_m1_reservoir_has_almost_no_power_on_large_tables():
    # 1,500 sampled rows out of 1,000,000: even 100% duplicated rows would
    # not reliably show a duplicate pair, so the bound saturates at 1.0.
    assert min_detectable_duplicate_share(1_000_000, 1500) == 1.0
    assert min_detectable_duplicate_share(2000, 1500) < 0.01
    assert min_detectable_duplicate_share(1500, 1500) == 0.0
    assert min_detectable_duplicate_share(None, 1500) is None


def test_m2_one_repeated_determinant_is_not_enough():
    weak = dependency_evidence([("a", 1), ("a", 1), ("b", 2), ("c", 3)])
    assert weak.consistent and weak.repeated_determinants == 1
    assert (
        weak.repeated_determinants
        < HeuristicThresholds().min_fd_repeated_determinants
    )
    approximate = dependency_evidence([("a", 1), ("a", 1), ("a", 2), ("b", 3)])
    assert not approximate.consistent
    assert approximate.g3 == pytest.approx(0.25)


def test_m3_rule_of_three_and_good_turing():
    assert rule_of_three_upper(1500) == pytest.approx(0.002)
    assert rule_of_three_upper(0) is None
    assert good_turing_unseen_mass(["A", "A", "B", "C"]) == pytest.approx(0.5)
    assert good_turing_unseen_mass([]) is None


# --- Unified thresholds -------------------------------------------------------


def test_both_advisors_share_one_threshold_object():
    assert DiscoveryBudget().thresholds == HeuristicThresholds()


def test_threshold_validation():
    with pytest.raises(ValueError):
        HeuristicThresholds(max_category_unique_share=0)
    with pytest.raises(ValueError):
        HeuristicThresholds(min_pattern_sample=0)
    with pytest.raises(ValueError):
        HeuristicThresholds(hll_z=math.inf)


def test_pandas_advisor_uses_shared_width_and_iso_confidence():
    pd = pytest.importorskip("pandas")
    from quaxdim import discover

    frame = pd.DataFrame(
        {
            "CODE_ID": [str(10**34 + i) for i in range(3)],  # width 35
            "fecha_corte": ["2026-01-01", "2026-02-01", "2026-03-01"],
        }
    )
    report = discover(frame)
    widths = [
        r
        for r in report.recommendations
        if r.evaluator == "builtin.regex_fullmatch"
    ]
    # The pandas advisor used to stop at width 30, the native one at 40.
    assert widths and widths[0].expectation["pattern"] == "[0-9]{35}"
    iso = [r for r in report.recommendations if "ISO-8601" in r.title]
    # Three matching values no longer earn "high" confidence.
    assert iso and iso[0].confidence == "medium"
