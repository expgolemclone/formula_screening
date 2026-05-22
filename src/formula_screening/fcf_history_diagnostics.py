"""Diagnostics for FCF history coverage."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
import csv

CAUSE_OK = "ok"
CAUSE_NO_CF_HISTORY = "no_cf_history"
CAUSE_SHORT_HISTORY_ONLY = "short_history_only"
CAUSE_SHORT_HISTORY_AND_MISSING_ITEMS = "short_history_and_missing_fcf_items"
CAUSE_MISSING_ITEMS = "missing_fcf_items"

REVIEW_LIKELY_UNAVOIDABLE = "likely_unavoidable_short_history"
REVIEW_NEEDS_SOURCE_HISTORY_CHECK = "needs_source_history_check"
REVIEW_NEEDS_STOCK_DB_CHECK = "needs_stock_db_parse_or_source_check"
REVIEW_OK = "ok"

CAUSE_ORDER = (
    CAUSE_OK,
    CAUSE_SHORT_HISTORY_ONLY,
    CAUSE_SHORT_HISTORY_AND_MISSING_ITEMS,
    CAUSE_MISSING_ITEMS,
    CAUSE_NO_CF_HISTORY,
)


@dataclass(frozen=True, slots=True)
class PeriodDiagnostic:
    period: str
    reason: str
    item_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FcfHistoryDiagnostic:
    ticker: str
    name: str
    loaded_periods: int
    valid_periods: int
    required_periods: int
    cause: str
    review_status: str
    invalid_periods: tuple[PeriodDiagnostic, ...]

    @property
    def missing_periods(self) -> int:
        return max(0, self.required_periods - self.loaded_periods)

    @property
    def invalid_period_count(self) -> int:
        return len(self.invalid_periods)

    @property
    def under_required_periods(self) -> bool:
        return self.valid_periods < self.required_periods


@dataclass(frozen=True, slots=True)
class FcfHistorySummary:
    diagnostics: tuple[FcfHistoryDiagnostic, ...]
    cause_counts: Mapping[str, int]
    review_counts: Mapping[str, int]
    missing_reason_counts: Mapping[str, int]

    @property
    def total(self) -> int:
        return len(self.diagnostics)

    @property
    def under_required_count(self) -> int:
        return sum(1 for item in self.diagnostics if item.under_required_periods)

    @property
    def ok_count(self) -> int:
        return self.cause_counts.get(CAUSE_OK, 0)


def has_valid_fcf(items: Mapping[str, float | None]) -> bool:
    """Return true when one period has enough values to derive FCF."""

    if items.get("free_cf") is not None:
        return True
    return items.get("operating_cf") is not None and items.get("investing_cf") is not None


def missing_fcf_reason(items: Mapping[str, float | None]) -> str | None:
    """Return a compact reason for an invalid FCF period."""

    if has_valid_fcf(items):
        return None

    free_cf_state = "free_cf_null" if "free_cf" in items else "free_cf_absent"
    missing_parts: list[str] = []
    if items.get("operating_cf") is None:
        missing_parts.append("missing_operating_cf")
    if items.get("investing_cf") is None:
        missing_parts.append("missing_investing_cf")
    if not missing_parts:
        missing_parts.append("missing_usable_fcf_inputs")
    return f"{free_cf_state}+{'+'.join(missing_parts)}"


def diagnose_record(
    record: Mapping[str, object],
    *,
    required_periods: int,
) -> FcfHistoryDiagnostic:
    cf_history = _cf_history(record)[:required_periods]
    invalid_periods = tuple(
        PeriodDiagnostic(
            period=period,
            reason=reason,
            item_names=tuple(sorted(items)),
        )
        for period, items in cf_history
        if (reason := missing_fcf_reason(items)) is not None
    )
    loaded_periods = len(cf_history)
    valid_periods = loaded_periods - len(invalid_periods)
    cause = _cause(loaded_periods, valid_periods, required_periods)
    return FcfHistoryDiagnostic(
        ticker=str(record.get("ticker", "")),
        name=str(record.get("name", "")),
        loaded_periods=loaded_periods,
        valid_periods=valid_periods,
        required_periods=required_periods,
        cause=cause,
        review_status=_review_status(cause),
        invalid_periods=invalid_periods,
    )


def summarize_diagnostics(
    diagnostics: Iterable[FcfHistoryDiagnostic],
) -> FcfHistorySummary:
    items = tuple(diagnostics)
    cause_counts = Counter(item.cause for item in items)
    review_counts = Counter(item.review_status for item in items)
    missing_reason_counts = Counter(
        period.reason for item in items for period in item.invalid_periods
    )
    return FcfHistorySummary(
        diagnostics=items,
        cause_counts=dict(cause_counts),
        review_counts=dict(review_counts),
        missing_reason_counts=dict(missing_reason_counts),
    )


def diagnose_records(
    records: Iterable[Mapping[str, object]],
    *,
    required_periods: int,
) -> FcfHistorySummary:
    return summarize_diagnostics(
        diagnose_record(record, required_periods=required_periods) for record in records
    )


def format_summary(summary: FcfHistorySummary, *, sample_count: int) -> str:
    lines = [
        f"total={summary.total}",
        f"ok={summary.ok_count}",
        f"under_required={summary.under_required_count}",
        "",
        "cause_counts:",
    ]
    for cause in CAUSE_ORDER:
        lines.append(f"  {cause}: {summary.cause_counts.get(cause, 0)}")
    lines.append("")
    lines.append("review_counts:")
    for review_status, count in sorted(summary.review_counts.items()):
        lines.append(f"  {review_status}: {count}")
    lines.append("")
    lines.append("missing_reason_counts:")
    for reason, count in sorted(
        summary.missing_reason_counts.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        lines.append(f"  {reason}: {count}")
    lines.append("")
    lines.append("samples:")
    for cause in CAUSE_ORDER:
        if cause == CAUSE_OK:
            continue
        samples = [
            item
            for item in summary.diagnostics
            if item.cause == cause and item.under_required_periods
        ][:sample_count]
        if not samples:
            continue
        lines.append(f"  {cause}:")
        for item in samples:
            details = "; ".join(
                f"{period.period}:{period.reason}"
                for period in item.invalid_periods[:3]
            )
            if not details:
                details = "invalid_periods=none"
            lines.append(
                "    "
                f"{item.ticker} {item.name} "
                f"valid={item.valid_periods}/{item.required_periods} "
                f"loaded={item.loaded_periods} "
                f"missing_periods={item.missing_periods} "
                f"review={item.review_status} "
                f"{details}"
            )
    return "\n".join(lines)


def write_diagnostics_csv(
    path: Path,
    diagnostics: Iterable[FcfHistoryDiagnostic],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "ticker",
                "name",
                "loaded_periods",
                "valid_periods",
                "required_periods",
                "missing_periods",
                "invalid_periods",
                "cause",
                "review_status",
                "invalid_period_details",
            ],
        )
        writer.writeheader()
        for item in diagnostics:
            if not item.under_required_periods:
                continue
            writer.writerow(
                {
                    "ticker": item.ticker,
                    "name": item.name,
                    "loaded_periods": item.loaded_periods,
                    "valid_periods": item.valid_periods,
                    "required_periods": item.required_periods,
                    "missing_periods": item.missing_periods,
                    "invalid_periods": item.invalid_period_count,
                    "cause": item.cause,
                    "review_status": item.review_status,
                    "invalid_period_details": "; ".join(
                        f"{period.period}:{period.reason}:"
                        f"{'|'.join(period.item_names)}"
                        for period in item.invalid_periods
                    ),
                }
            )


def _cf_history(
    record: Mapping[str, object],
) -> list[tuple[str, Mapping[str, float | None]]]:
    raw_history = record.get("cf_history")
    if not isinstance(raw_history, list):
        return []

    result: list[tuple[str, Mapping[str, float | None]]] = []
    for row in raw_history:
        if not isinstance(row, tuple) or len(row) != 2:
            continue
        period, items = row
        if not isinstance(items, Mapping):
            continue
        result.append((str(period), items))
    return result


def _cause(loaded_periods: int, valid_periods: int, required_periods: int) -> str:
    invalid_periods = loaded_periods - valid_periods
    if valid_periods >= required_periods:
        return CAUSE_OK
    if loaded_periods == 0:
        return CAUSE_NO_CF_HISTORY
    if loaded_periods < required_periods and invalid_periods == 0:
        return CAUSE_SHORT_HISTORY_ONLY
    if loaded_periods < required_periods:
        return CAUSE_SHORT_HISTORY_AND_MISSING_ITEMS
    return CAUSE_MISSING_ITEMS


def _review_status(cause: str) -> str:
    if cause == CAUSE_OK:
        return REVIEW_OK
    if cause == CAUSE_SHORT_HISTORY_ONLY:
        return REVIEW_LIKELY_UNAVOIDABLE
    if cause == CAUSE_NO_CF_HISTORY:
        return REVIEW_NEEDS_SOURCE_HISTORY_CHECK
    return REVIEW_NEEDS_STOCK_DB_CHECK
