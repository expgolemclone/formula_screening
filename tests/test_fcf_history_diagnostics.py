from __future__ import annotations

from formula_screening.fcf_history_diagnostics import (
    CAUSE_MISSING_ITEMS,
    CAUSE_NO_CF_HISTORY,
    CAUSE_OK,
    CAUSE_SHORT_HISTORY_AND_MISSING_ITEMS,
    CAUSE_SHORT_HISTORY_ONLY,
    REVIEW_LIKELY_UNAVOIDABLE,
    REVIEW_NEEDS_SOURCE_HISTORY_CHECK,
    REVIEW_NEEDS_STOCK_DB_CHECK,
    diagnose_record,
    diagnose_records,
    has_valid_fcf,
    missing_fcf_reason,
)


def _record(
    ticker: str,
    periods: list[tuple[str, dict[str, float | None]]],
) -> dict:
    return {"ticker": ticker, "name": ticker, "cf_history": periods}


def test_has_valid_fcf_accepts_free_cf_or_operating_plus_investing_cf() -> None:
    assert has_valid_fcf({"free_cf": 1.0})
    assert has_valid_fcf({"operating_cf": 2.0, "investing_cf": -1.0})
    assert not has_valid_fcf({"operating_cf": 2.0})
    assert not has_valid_fcf({"free_cf": None, "operating_cf": 2.0})


def test_missing_fcf_reason_names_unusable_inputs() -> None:
    assert missing_fcf_reason({"operating_cf": 2.0}) == (
        "free_cf_absent+missing_investing_cf"
    )
    assert missing_fcf_reason({"free_cf": None}) == (
        "free_cf_null+missing_operating_cf+missing_investing_cf"
    )


def test_diagnose_record_classifies_short_history_only_as_likely_unavoidable() -> None:
    diagnostic = diagnose_record(
        _record(
            "1301",
            [
                ("2025-03", {"free_cf": 3.0}),
                ("2024-03", {"free_cf": 2.0}),
            ],
        ),
        required_periods=3,
    )

    assert diagnostic.cause == CAUSE_SHORT_HISTORY_ONLY
    assert diagnostic.review_status == REVIEW_LIKELY_UNAVOIDABLE
    assert diagnostic.valid_periods == 2
    assert diagnostic.missing_periods == 1


def test_diagnose_records_counts_under_coverage_causes() -> None:
    summary = diagnose_records(
        [
            _record(
                "ok",
                [
                    ("2025-03", {"free_cf": 3.0}),
                    ("2024-03", {"free_cf": 2.0}),
                    ("2023-03", {"free_cf": 1.0}),
                ],
            ),
            _record("short", [("2025-03", {"free_cf": 3.0})]),
            _record(
                "mixed",
                [
                    ("2025-03", {"free_cf": 3.0}),
                    ("2024-03", {"operating_cf": 2.0}),
                ],
            ),
            _record(
                "missing",
                [
                    ("2025-03", {"free_cf": 3.0}),
                    ("2024-03", {"operating_cf": 2.0}),
                    ("2023-03", {"free_cf": 1.0}),
                ],
            ),
            _record("none", []),
        ],
        required_periods=3,
    )

    assert summary.cause_counts == {
        CAUSE_OK: 1,
        CAUSE_SHORT_HISTORY_ONLY: 1,
        CAUSE_SHORT_HISTORY_AND_MISSING_ITEMS: 1,
        CAUSE_MISSING_ITEMS: 1,
        CAUSE_NO_CF_HISTORY: 1,
    }
    assert summary.review_counts[REVIEW_NEEDS_STOCK_DB_CHECK] == 2
    assert summary.review_counts[REVIEW_NEEDS_SOURCE_HISTORY_CHECK] == 1
    assert summary.under_required_count == 4
