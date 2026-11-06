"""Discovery heuristics shared by the pandas and the native advisors.

Both advisors used to carry their own regular expressions and thresholds,
which had drifted apart (closed-domain share 0.25 vs 0.35, code width 30 vs
40, different start/end vocabularies). Everything that decides *whether* a
hypothesis is proposed now lives here; the advisors only gather evidence.

Name semantics are matched on **tokens**, never substrings: ``country`` is
not a count, ``tamaño_empresa`` is not a year and ``fecha_financiacion`` is
not an end date (audit finding A3).

Sample-based evidence is quantified instead of being labelled only
``low``/``medium`` (audit findings M1–M3):

* rule of three: zero violations in ``n`` values bound the violation share
  below ``3/n`` with ~95% confidence;
* Good–Turing: the share of the population in categories never seen in the
  sample is estimated by ``f1/n`` (``f1`` = categories seen exactly once);
* duplicate detection power: the smallest duplicate share a reservoir
  sample would reveal with ~95% probability.

The module imports neither pandas nor any optional engine.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True)
class HeuristicThresholds:
    """Every threshold that decides whether a hypothesis is proposed.

    Attributes:
        max_code_width: Longest fixed width proposed as a code pattern.
        min_pattern_sample: Fewest values that may support a pattern.
        min_iso_sample_high_confidence: Values needed before an ISO date
            hypothesis is labelled ``high`` confidence.
        min_category_sample: Fewest values that may support a domain.
        max_category_unique_share: Largest distinct/observed share for a
            candidate closed domain.
        min_hll_non_null: Fewest non-blank values for an HLL hypothesis.
        hll_z: Standard errors of tolerance below a ratio of one.
        min_exact_key_non_null: Fewest non-blank values for an exact key.
        min_pair_sample: Fewest complete pairs for a composite key.
        min_pair_coverage: Least share of complete pairs in the sample.
        min_fd_sample_pairs: Fewest complete pairs for a dependency.
        min_fd_repeated_determinants: Determinants that must repeat before
            a dependency is proposed (one repetition is not evidence).
    """

    max_code_width: int = 40
    min_pattern_sample: int = 3
    min_iso_sample_high_confidence: int = 30
    min_category_sample: int = 12
    max_category_unique_share: float = 0.25
    min_hll_non_null: int = 20
    hll_z: float = 2.5
    min_exact_key_non_null: int = 3
    min_pair_sample: int = 4
    min_pair_coverage: float = 0.9
    min_fd_sample_pairs: int = 8
    min_fd_repeated_determinants: int = 3

    def __post_init__(self) -> None:
        """Validate ranges, raising ``ValueError`` on invalid values."""
        positive_ints = (
            "max_code_width",
            "min_pattern_sample",
            "min_iso_sample_high_confidence",
            "min_category_sample",
            "min_hll_non_null",
            "min_exact_key_non_null",
            "min_pair_sample",
            "min_fd_sample_pairs",
            "min_fd_repeated_determinants",
        )
        for name in positive_ints:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            if value < 1:
                raise ValueError(f"{name} must be positive")
        for name in ("max_category_unique_share", "min_pair_coverage"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        if not 0 < self.hll_z <= 10:
            raise ValueError("hll_z must be in (0, 10]")


# --- Name tokens and semantic roles -----------------------------------------

_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)
_SEPARATORS = re.compile(r"[^0-9a-z]+")

ROLE_VOCABULARY: dict[str, frozenset[str]] = {
    "identifier": frozenset(
        {"id", "uuid", "guid", "code", "codigo", "cod", "sku", "zip", "postal"}
    ),
    "temporal": frozenset(
        {
            "fecha",
            "date",
            "time",
            "timestamp",
            "datetime",
            "created",
            "updated",
            "periodo",
            "period",
            "year",
            "ano",  # "año" after accent folding
            "quarter",
            "trimestre",
            "month",
            "mes",
        }
    ),
    "geographical": frozenset(
        {
            "municipio",
            "departamento",
            "country",
            "pais",
            "city",
            "ciudad",
            "region",
            "location",
            "dpt",
            "mpo",
        }
    ),
    "count": frozenset(
        {
            "count",
            "cantidad",
            "conteo",
            "total",
            "numero",
            "num",
            "qty",
            "quantity",
            "vacantes",
            "inscritos",
            "colocaciones",
            "poblacion",
            "population",
        }
    ),
    "category": frozenset(
        {
            "estado",
            "status",
            "tipo",
            "type",
            "clase",
            "category",
            "categoria",
            "genero",
            "gender",
            "sexo",
        }
    ),
}
BEGIN_TOKENS = frozenset({"inicio", "start", "begin", "desde", "created"})
END_TOKENS = frozenset({"fin", "end", "finish", "hasta", "updated"})
_TEMPORAL_LOGICAL_TYPES = frozenset({"date", "datetime", "timestamp"})


def _fold_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def name_tokens(name: str) -> tuple[str, ...]:
    """Split a column name into lower-case, accent-folded tokens.

    Splits on ``_``, ``-``, spaces, dots and camelCase boundaries:
    ``fechaInicio_Contrato`` -> ``("fecha", "inicio", "contrato")``.
    """
    spaced = _CAMEL_BOUNDARY.sub(" ", _fold_accents(name))
    return tuple(t for t in _SEPARATORS.split(spaced.lower()) if t)


def has_role(name: str, role: str) -> bool:
    """Return whether ``name`` contains a token of ``role``'s vocabulary."""
    return bool(set(name_tokens(name)) & ROLE_VOCABULARY[role])


def semantic_roles(name: str, logical_type: str = "") -> list[str]:
    """Return the semantic roles suggested by a column name and type."""
    tokens = set(name_tokens(name))
    roles = [
        role
        for role, vocabulary in ROLE_VOCABULARY.items()
        if tokens & vocabulary
    ]
    if logical_type in _TEMPORAL_LOGICAL_TYPES and "temporal" not in roles:
        roles.append("temporal")
    return roles or ["unknown"]


def _event_root(
    tokens: Sequence[str], markers: frozenset[str]
) -> tuple[str, ...]:
    return tuple(t for t in tokens if t not in markers)


def start_end_pairs(names: Iterable[str]) -> list[tuple[str, str]]:
    """Pair start and end columns that describe the same event.

    A pair needs a begin marker on one side, an end marker on the other and
    the *same remaining tokens* (``fecha_inicio_contrato`` pairs with
    ``fecha_fin_contrato`` but not with ``fecha_fin_vigencia``).
    """
    tokenised = {n: name_tokens(n) for n in names}
    begins = [n for n, t in tokenised.items() if set(t) & BEGIN_TOKENS]
    ends = [n for n, t in tokenised.items() if set(t) & END_TOKENS]
    pairs = []
    for start, end in product(begins, ends):
        if start == end:
            continue
        if _event_root(tokenised[start], BEGIN_TOKENS) == _event_root(
            tokenised[end], END_TOKENS
        ):
            pairs.append((start, end))
    return pairs


# --- Approximate cardinality -------------------------------------------------


def hll_suggests_unique(
    approx_distinct: float,
    observed: int,
    relative_std_error: float,
    *,
    z: float,
) -> bool:
    """Return whether an HLL estimate is compatible with all-distinct values.

    The tolerance scales with the sketch's own error (audit finding A2): a
    fixed ``[0.94, 1.06]`` window rejected 73% of truly unique keys at
    precision 5 and accepted ~5% duplicates at precision 14. Ratios above
    one are estimator noise, so only the lower side is tested.
    """
    if observed <= 0:
        return False
    return approx_distinct / observed >= 1 - z * relative_std_error


# --- Quantified sample evidence ----------------------------------------------


def rule_of_three_upper(sample_size: int) -> float | None:
    """Return the ~95% upper bound on a violation share after zero hits."""
    if sample_size <= 0:
        return None
    return min(1.0, 3 / sample_size)


def good_turing_unseen_mass(values: Iterable[Hashable]) -> float | None:
    """Estimate the population share in categories absent from a sample."""
    counts = Counter(values)
    n = sum(counts.values())
    if n == 0:
        return None
    singletons = sum(1 for c in counts.values() if c == 1)
    return singletons / n


def min_detectable_duplicate_share(
    population_rows: int | None, sample_rows: int
) -> float | None:
    """Smallest share of rows in duplicate pairs a sample reveals at ~95%.

    With ``D`` duplicate pairs among ``N`` rows, a uniform sample of ``n``
    rows contains on average ``D·C(n,2)/C(N,2)`` of them; at least one shows
    up with ~95% probability when that mean reaches 3. The share of rows
    involved is ``2D/N``. A value of 1.0 means the sample cannot reveal
    duplicates at any realistic rate (audit finding M1).
    """
    if population_rows is None or sample_rows < 2 or population_rows < 2:
        return None
    if sample_rows >= population_rows:
        return 0.0
    pairs_population = math.comb(population_rows, 2)
    pairs_sample = math.comb(sample_rows, 2)
    duplicate_pairs = 3 * pairs_population / pairs_sample
    return min(1.0, 2 * duplicate_pairs / population_rows)


@dataclass(frozen=True)
class DependencyEvidence:
    """Support for a candidate functional dependency ``X -> Y``.

    Attributes:
        consistent: Every repeated determinant maps to one value.
        pairs: Complete ``(x, y)`` pairs examined.
        determinants: Distinct ``x`` values.
        repeated_determinants: ``x`` values seen more than once; only these
            can contradict the dependency.
        g3: Kivinen–Mannila error, the least share of pairs to remove for
            the dependency to hold exactly.
    """

    consistent: bool
    pairs: int
    determinants: int
    repeated_determinants: int
    g3: float


def dependency_evidence(
    pairs: Iterable[tuple[Hashable, Hashable]],
) -> DependencyEvidence:
    """Measure how strongly sample pairs support ``X -> Y``."""
    by_determinant: dict[Hashable, Counter[Hashable]] = {}
    total = 0
    for x, y in pairs:
        by_determinant.setdefault(x, Counter())[y] += 1
        total += 1
    keep = sum(c.most_common(1)[0][1] for c in by_determinant.values())
    repeated = sum(1 for c in by_determinant.values() if sum(c.values()) > 1)
    return DependencyEvidence(
        consistent=all(len(c) == 1 for c in by_determinant.values()),
        pairs=total,
        determinants=len(by_determinant),
        repeated_determinants=repeated,
        g3=(total - keep) / total if total else 0.0,
    )
