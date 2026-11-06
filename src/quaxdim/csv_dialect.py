"""CSV read options shared by discovery and every execution engine.

How a CSV file is read is part of the asset's contract, not an engine
default. Before this module, discovery accepted a delimiter and an encoding
that evaluation then ignored, and each engine guessed its own dialect
(DuckDB's sniffer could even read a ``,``-separated file as one column).

The options are declared under ``asset.read_options``:

* ``delimiter``: one character; defaults to tab for ``.tsv`` and comma
  otherwise.
* ``encoding``: any Python codec; engines that cannot honour it block the
  rule instead of misreading text.
* ``quote_char``: one character, default ``"``. Quotes are escaped by
  doubling them (RFC 4180).
* ``header``: must be ``true``; headerless files need declared names.
* ``malformed_rows``: ``"error"`` (the only supported policy). A row with
  too few or too many fields is a structural error, never an ordinary
  missing value.
* ``max_field_chars``: upper bound on a single field, a guard against
  pathological inputs.

The module imports neither pandas nor any optional engine.
"""

from __future__ import annotations

import codecs
import csv
import gzip
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO, Any

DEFAULT_ENCODING = "utf-8-sig"
DEFAULT_MAX_FIELD_CHARS = 1_048_576
MAX_FIELD_CHARS_CEILING = 268_435_456
SUPPORTED_MALFORMED_POLICIES = frozenset({"error"})

# Codec names (as normalised by ``codecs.lookup``) each engine decodes
# natively. Anything else must run on the standard-library stream.
_DUCKDB_ENCODINGS = {
    "utf-8": "utf-8",
    "utf-8-sig": "utf-8",  # DuckDB strips the BOM itself.
    "iso8859-1": "latin-1",
    "utf-16": "utf-16",
}
_POLARS_ENCODINGS = {
    "utf-8": "utf8",
    "utf-8-sig": "utf8",  # Polars strips the BOM itself.
}
_CSV_SUFFIXES = (".csv", ".tsv", ".csv.gz", ".tsv.gz")


class CsvDialectError(ValueError):
    """Raised when declared read options are invalid."""


class MalformedCsvError(ValueError):
    """Raised when a CSV file is structurally malformed."""


def is_csv_path(source: Any) -> bool:
    """Return whether ``source`` is a path to a (possibly gzipped) CSV/TSV."""
    if not isinstance(source, (str, Path)):
        return False
    return str(source).lower().endswith(_CSV_SUFFIXES)


def _is_tsv(path: str | Path) -> bool:
    return str(path).lower().endswith((".tsv", ".tsv.gz"))


@dataclass(frozen=True)
class CsvReadOptions:
    """Validated CSV dialect and safety limits of one asset."""

    delimiter: str = ","
    encoding: str = DEFAULT_ENCODING
    quote_char: str = '"'
    header: bool = True
    malformed_rows: str = "error"
    max_field_chars: int = DEFAULT_MAX_FIELD_CHARS

    def __post_init__(self) -> None:
        """Validate the options, raising :class:`CsvDialectError`."""
        for name in ("delimiter", "quote_char"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 1:
                raise CsvDialectError(f"{name} must be a single character")
            if value in "\r\n":
                raise CsvDialectError(f"{name} cannot be a line break")
        if self.delimiter == self.quote_char:
            raise CsvDialectError("delimiter and quote_char must differ")
        if self.header is not True:
            raise CsvDialectError("Only CSV files with a header are supported")
        if self.malformed_rows not in SUPPORTED_MALFORMED_POLICIES:
            raise CsvDialectError(
                f"Unsupported malformed_rows policy: {self.malformed_rows}"
            )
        if (
            isinstance(self.max_field_chars, bool)
            or not isinstance(self.max_field_chars, int)
            or not 0 < self.max_field_chars <= MAX_FIELD_CHARS_CEILING
        ):
            raise CsvDialectError(
                f"max_field_chars must be an integer in "
                f"1..{MAX_FIELD_CHARS_CEILING}"
            )
        try:
            codecs.lookup(self.encoding)
        except (LookupError, TypeError) as exc:
            raise CsvDialectError(
                f"Unknown encoding: {self.encoding}"
            ) from exc

    @property
    def codec(self) -> str:
        """Canonical codec name, e.g. ``iso8859-1`` for ``latin-1``."""
        return codecs.lookup(self.encoding).name

    @classmethod
    def from_mapping(
        cls, options: Mapping[str, Any] | None, *, path: str | Path | None
    ) -> CsvReadOptions:
        """Build options from a contract mapping and the source path.

        Args:
            options: ``asset.read_options`` (may be ``None``).
            path: Source path; selects the tab default for ``.tsv``.

        Raises:
            CsvDialectError: If an option is unknown or invalid.
        """
        declared = dict(options or {})
        unknown = set(declared) - set(cls.__dataclass_fields__)
        if unknown:
            raise CsvDialectError(f"Unknown read options: {sorted(unknown)}")
        if "delimiter" not in declared and path is not None and _is_tsv(path):
            declared["delimiter"] = "\t"
        return cls(**declared)

    @classmethod
    def from_asset(
        cls, asset: Mapping[str, Any], *, path: str | Path | None
    ) -> CsvReadOptions:
        """Build options from a contract asset declaration."""
        return cls.from_mapping(asset.get("read_options"), path=path)

    def to_contract(self) -> dict[str, Any]:
        """Return the mapping stored under ``asset.read_options``."""
        return asdict(self)

    def engine_encoding(self, engine: str) -> str | None:
        """Return the engine's name for this encoding, or ``None``.

        ``None`` means the engine cannot decode it faithfully and the rule
        must be blocked (``csv_stream`` decodes any Python codec).
        """
        if engine == "csv_stream":
            return self.encoding
        table = {"duckdb": _DUCKDB_ENCODINGS, "polars": _POLARS_ENCODINGS}
        return table.get(engine, {}).get(self.codec)


def open_text(path: str | Path, options: CsvReadOptions) -> IO[str]:
    """Open a CSV (optionally gzipped) as text with the declared encoding."""
    if str(path).lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding=options.encoding, newline="")
    return open(path, encoding=options.encoding, newline="")


@contextmanager
def field_size_limit(limit: int) -> Iterator[None]:
    """Temporarily set ``csv.field_size_limit``.

    The limit is process-wide in the standard library, so it is restored on
    exit. Concurrent readers in other threads see the temporary value.
    """
    previous = csv.field_size_limit(limit)
    try:
        yield
    finally:
        csv.field_size_limit(previous)


def _reader(handle: IO[str], options: CsvReadOptions) -> Any:
    return csv.reader(
        handle,
        delimiter=options.delimiter,
        quotechar=options.quote_char,
        doublequote=True,
        strict=True,
    )


def _validated_header(row: list[str] | None) -> list[str]:
    if not row:
        raise MalformedCsvError("CSV file has no header row")
    if any(not name for name in row):
        raise MalformedCsvError("CSV header contains an empty column name")
    if len(set(row)) != len(row):
        raise MalformedCsvError("CSV header contains duplicate column names")
    return row


def read_header(path: str | Path, options: CsvReadOptions) -> list[str]:
    """Read and validate the header (non-empty, unique column names).

    Raises:
        MalformedCsvError: If the header is missing, empty or duplicated.
    """
    with field_size_limit(options.max_field_chars):
        with open_text(path, options) as handle:
            try:
                return _validated_header(next(_reader(handle, options), None))
            except csv.Error as exc:
                raise MalformedCsvError(
                    f"Unreadable CSV header: {exc}"
                ) from exc


def iter_records(
    path: str | Path, options: CsvReadOptions
) -> Iterator[dict[str, str]]:
    """Yield each data row as ``{column: text}`` in one sequential pass.

    Raises:
        MalformedCsvError: On a bad header, a row with a different number of
            fields, an oversized field or a quoting error. The row number
            counts data rows from 1.
    """
    with field_size_limit(options.max_field_chars):
        with open_text(path, options) as handle:
            reader = _reader(handle, options)
            try:
                header = _validated_header(next(reader, None))
                width = len(header)
                for number, row in enumerate(reader, start=1):
                    if len(row) != width:
                        raise MalformedCsvError(
                            f"Malformed CSV row {number}: expected {width} "
                            f"fields, found {len(row)}"
                        )
                    yield dict(zip(header, row, strict=True))
            except csv.Error as exc:
                raise MalformedCsvError(
                    f"Malformed CSV near data row {reader.line_num - 1}: {exc}"
                ) from exc


def validate_structure(path: str | Path, options: CsvReadOptions) -> int:
    """Check every row's field count in one pass; return the row count.

    Used before engines that cannot reject short rows themselves (Polars
    pads them with empty values).
    """
    return sum(1 for _ in iter_records(path, options))


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def duckdb_csv_relation(
    path: str | Path,
    options: CsvReadOptions,
    columns: list[str],
    *,
    null_sentinel: str,
) -> str:
    """Build a strict DuckDB ``read_csv`` call with no dialect sniffing.

    Args:
        path: CSV path.
        options: Declared read options.
        columns: Header names, as read by :func:`read_header`.
        null_sentinel: The only string DuckDB may read as NULL; a value that
            never occurs keeps empty fields as ``""``.

    Raises:
        CsvDialectError: If DuckDB cannot decode the declared encoding.
    """
    encoding = options.engine_encoding("duckdb")
    if encoding is None:
        raise CsvDialectError(
            f"DuckDB cannot decode {options.encoding}; use csv_stream"
        )
    column_map = ", ".join(
        f"{_sql_literal(name)}: 'VARCHAR'" for name in columns
    )
    return (
        f"read_csv({_sql_literal(str(path))}, auto_detect=false, header=true, "
        f"delim={_sql_literal(options.delimiter)}, "
        f"quote={_sql_literal(options.quote_char)}, "
        f"escape={_sql_literal(options.quote_char)}, "
        f"encoding={_sql_literal(encoding)}, "
        f"columns={{{column_map}}}, strict_mode=true, null_padding=false, "
        f"max_line_size={options.max_field_chars * max(len(columns), 1)}, "
        f"nullstr={_sql_literal(null_sentinel)})"
    )
