"""Strict datetime parsing for the legacy :class:`QualitySuite` API.

The native contract executor never guesses date formats or time zones. This
module gives the legacy pandas checks (``freshness_days``, ``delay_days``)
the same guarantees:

* the format must be declared (``date_format``); nothing is inferred from
  the first element, so ``04/11/2026`` is never silently read as 11 April;
* naive timestamps need a declared ``timezone``; UTC is never assumed;
* a non-blank value that does not match the format is a *validity* problem,
  not a timeliness failure, so the whole check errors out instead of
  counting the row as late (the same behaviour as the native executor's
  ``DATE_PARTIALLY_UNPARSEABLE``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._lazy import LazyModule

if TYPE_CHECKING:
    import pandas as pd
else:
    pd = LazyModule("pandas", extra="pandas")


class DateParsingError(ValueError):
    """Raised when temporal values cannot be interpreted safely."""


def _blank(series: pd.Series) -> pd.Series:
    """Return a mask of null or whitespace-only values."""
    return series.isna() | series.map(
        lambda value: isinstance(value, str) and not value.strip()
    )


def localize_to_utc(dates: pd.Series, timezone: str | None) -> pd.Series:
    """Attach ``timezone`` to naive timestamps and convert them to UTC.

    Args:
        dates: Datetime series, naive or aware.
        timezone: IANA zone of naive values, e.g. ``America/Bogota``.

    Returns:
        A UTC-aware datetime series.

    Raises:
        DateParsingError: If values are naive and no zone is declared, or a
            local time is ambiguous or nonexistent in that zone.
    """
    if dates.dt.tz is not None:
        return dates.dt.tz_convert("UTC")
    if not timezone:
        raise DateParsingError(
            "Naive timestamps require params.timezone (UTC is not assumed)"
        )
    try:
        local = dates.dt.tz_localize(
            timezone, ambiguous="raise", nonexistent="raise"
        )
    except (ValueError, TypeError) as exc:
        raise DateParsingError(
            f"Cannot localize timestamps to {timezone}: {exc}"
        ) from exc
    return local.dt.tz_convert("UTC")


def parse_declared_datetimes(
    series: pd.Series, *, date_format: str | None, timezone: str | None
) -> pd.Series:
    """Parse ``series`` with one declared format and return UTC timestamps.

    Blank and null values become ``NaT``; the caller's null policy decides
    how they count.

    Args:
        series: Values to parse; datetime dtypes are used as they are.
        date_format: ``strftime`` pattern of text values.
        timezone: IANA zone of naive values.

    Returns:
        A UTC-aware datetime series aligned with ``series``.

    Raises:
        DateParsingError: If the format is missing for text values, any
            non-blank value does not match it, or the zone is missing.
    """
    if pd.api.types.is_datetime64_any_dtype(series.dtype):
        return localize_to_utc(series, timezone)
    if not date_format:
        raise DateParsingError(
            "Text dates require params.date_format; formats are not inferred"
        )
    blank = _blank(series)
    text = series.where(~blank)
    try:
        parsed = pd.to_datetime(text, format=date_format, errors="coerce")
    except (ValueError, TypeError) as exc:
        raise DateParsingError(f"Invalid date_format: {exc}") from exc
    unparseable = int((~blank & parsed.isna()).sum())
    if unparseable:
        raise DateParsingError(
            f"{unparseable} value(s) do not match date_format "
            f"{date_format!r}; fix validity before measuring timeliness"
        )
    return localize_to_utc(parsed, timezone)


def as_utc_instant(value: Any, timezone: str | None) -> pd.Timestamp:
    """Interpret a reference instant (``as_of``) consistently with the data.

    A naive value is read in ``timezone`` when declared, otherwise in UTC
    (the historical behaviour of ``QualitySuite.run``).
    """
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(timezone or "UTC")
    return stamp.tz_convert("UTC")
