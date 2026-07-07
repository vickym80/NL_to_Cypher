"""Live-derived Neo4j schema context for Text2CypherRetriever.

This module used to hold two hand-curated strings (ONTOLOGY_SCHEMA,
INSTANCE_DATA_SCHEMA) that were correct when written but silently went stale
as the live graph changed — new labels added to the graph (e.g. Port,
TransportLane — see Readme.md §4e) simply weren't there until a human noticed
and edited this file by hand. It also had no way to carry property *value*
knowledge (e.g. the closed set of valid KPI.id strings): that only ever
existed as Python `#` comments in examples.py, which are stripped by the
interpreter before NL2CYPHER_EXAMPLES is built — the LLM never actually saw
them.

Both problems share one fix: derive the schema live, every time it's needed
(subject to a cache TTL), including low-cardinality property *values*, and
render it into the same compact "Node properties: / Relationship properties:
/ The relationships:" shape the old curated string used — not the much
noisier ~14,814-char default `neo4j_graphrag.schema.get_schema()` text (see
Readme.md §4e for that size comparison), which also duplicates every label
and relationship against generic base labels like GraphNode/InstanceRecord.

Introspection reuses `neo4j_graphrag.schema.get_structured_schema(...,
is_enhanced=True)` (APOC-based — APOC is already installed and confirmed
working against this project's Neo4j instance, see Readme.md §4e) since it
already computes exactly the low-cardinality STRING distinct-value data this
module needs; this module's job is turning that into a compact, de-noised,
cached prompt string instead of the library's own noisier renderer.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import neo4j
from neo4j_graphrag.schema import get_structured_schema

logger = logging.getLogger(__name__)

# Matches neo4j_graphrag.schema.DISTINCT_VALUE_LIMIT — is_enhanced=True only
# retains the true distinct_count (not just a sample) for STRING properties
# up to that limit, so a higher cardinality threshold here wouldn't have
# reliable data to back it.
DEFAULT_ENUM_MAX_CARDINALITY = 10
DEFAULT_TTL_SECONDS = 900
# Sampled values longer than this read as truncated free text (descriptions,
# names), not short enum tokens (KPI ids, statuses) — see _introspect().
ENUM_VALUE_MAX_LENGTH = 30

_DATE_LIKE_RE = re.compile(r"date", re.IGNORECASE)
_NODE_LABEL_RE = re.compile(r"\(\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*([A-Za-z_][A-Za-z0-9_]*)")
_REL_TYPE_RE = re.compile(r"\[\s*[A-Za-z_]*\s*:\s*([A-Za-z_][A-Za-z0-9_|]*)")


@dataclass
class SchemaModel:
    """Normalized view of a live Neo4j schema, derived fresh on each cache miss."""

    node_props: dict[str, dict[str, str]] = field(default_factory=dict)
    rel_props: dict[str, dict[str, str]] = field(default_factory=dict)
    relationships: list[tuple[str, str, str]] = field(default_factory=list)  # (start, type, end)
    enum_values: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # (label, prop) -> values
    multi_label_groups: list[tuple[str, ...]] = field(default_factory=list)

    @property
    def labels(self) -> set[str]:
        return set(self.node_props)

    @property
    def rel_types(self) -> set[str]:
        return set(self.rel_props) | {t for _, t, _ in self.relationships}


def _multi_label_groups(driver: neo4j.Driver, database: str | None) -> list[tuple[str, ...]]:
    """Every distinct multi-label combination actually present on live nodes
    (plain Cypher, no APOC) — used to detect base/alias labels like RiskNode
    or CausalMediator that always co-occur with a more specific label."""
    records, _, _ = driver.execute_query(
        "MATCH (n) WITH DISTINCT labels(n) AS ls WHERE size(ls) > 1 "
        "RETURN ls ORDER BY ls LIMIT 200",
        database_=database,
        routing_=neo4j.RoutingControl.READ,
    )
    return sorted({tuple(sorted(r["ls"])) for r in records})


def _introspect(driver: neo4j.Driver, database: str | None, enum_max_cardinality: int) -> SchemaModel:
    structured = get_structured_schema(driver, is_enhanced=True, database=database, sanitize=True)

    node_props: dict[str, dict[str, str]] = {}
    enum_values: dict[tuple[str, str], list[str]] = {}
    for label, props in structured.get("node_props", {}).items():
        node_props[label] = {}
        for prop in props:
            node_props[label][prop["property"]] = prop["type"]
            distinct_count = prop.get("distinct_count")
            values = prop.get("values")
            if (
                prop["type"] == "STRING"
                and values
                and distinct_count is not None
                and distinct_count <= enum_max_cardinality
                # On a small graph, low-cardinality alone doesn't mean "enum" —
                # a free-text field (description, name) on a label with only a
                # few rows also has few distinct values. A true enum's values
                # are short repeated tokens ('otifd', 'high'); free text reads
                # as long, effectively-unique sentences even when truncated by
                # get_structured_schema's own 50-char sampling cap. Requiring
                # short values filters out the latter without hardcoding any
                # property name.
                and max(len(v) for v in values) <= ENUM_VALUE_MAX_LENGTH
            ):
                enum_values[(label, prop["property"])] = sorted(values)

    rel_props = {
        rel_type: {p["property"]: p["type"] for p in props}
        for rel_type, props in structured.get("rel_props", {}).items()
    }

    relationships = [(r["start"], r["type"], r["end"]) for r in structured.get("relationships", [])]

    return SchemaModel(
        node_props=node_props,
        rel_props=rel_props,
        relationships=relationships,
        enum_values=enum_values,
        multi_label_groups=_multi_label_groups(driver, database),
    )


def _label_exclusions_and_notes(schema: SchemaModel) -> tuple[set[str], list[str]]:
    """Fold base/alias labels (e.g. GraphNode, InstanceRecord, RiskNode,
    CausalMediator) out of the main properties/relationships blocks and into
    a short auto-generated Note instead — this is what keeps the rendered
    schema compact instead of reproducing get_schema()'s generic-superlabel
    duplication (see module docstring / Readme.md §4e).

    Classification is by how many *distinct* multi-label combos a label
    participates in, not raw co-occurrence count — a node can legitimately
    carry 3+ labels at once (e.g. a specific type plus a universal base
    label), and counting co-occurring labels directly would misclassify the
    specific type as "generic" too. A label spanning 2+ distinct combos (i.e.
    paired with otherwise-unrelated node types across different nodes) is a
    broad base label; a label confined to exactly one combo, alongside other
    equally single-combo labels, is a narrow 1:1 (or 1:N) alias group."""
    combo_membership: dict[str, set[tuple[str, ...]]] = {}
    for combo in schema.multi_label_groups:
        for label in combo:
            combo_membership.setdefault(label, set()).add(combo)

    broad_base = {label for label, combos in combo_membership.items() if len(combos) >= 2}

    notes: list[str] = []
    for base_label in sorted(broad_base):
        specific_labels = sorted(
            {other for combo in combo_membership[base_label] for other in combo if other != base_label and other not in broad_base}
        )
        if not specific_labels:
            continue
        notes.append(
            f"Note: {base_label} is a shared base label across {len(specific_labels)} node types "
            f"({', '.join(specific_labels)}) — matching a specific label already matches everything "
            f"{base_label} would; you rarely need to match {base_label} directly."
        )

    # Any combo left with 2+ labels once broad-base members are stripped out
    # is a narrow alias group — the same underlying nodes carrying multiple
    # specific labels together (e.g. IntermediateFactor + CausalMediator).
    alias_excluded: set[str] = set()
    reported: set[frozenset[str]] = set()
    for combo in schema.multi_label_groups:
        specific = sorted(set(combo) - broad_base)
        if len(specific) < 2:
            continue
        key = frozenset(specific)
        if key in reported:
            continue
        reported.add(key)
        _canonical, *aliases = specific
        alias_excluded.update(aliases)
        notes.append(
            f"Note: {' and '.join(specific)} nodes are the same underlying nodes (all these labels "
            f"coexist) — matching any one of them matches the same nodes."
        )

    return broad_base | alias_excluded, notes


def _format_node_props(schema: SchemaModel, excluded_labels: set[str]) -> str:
    lines = []
    for label in sorted(schema.node_props):
        if label in excluded_labels:
            continue
        props = schema.node_props[label]
        parts = []
        for prop_name in sorted(props):
            prop_type = props[prop_name]
            values = schema.enum_values.get((label, prop_name))
            parts.append(f"{prop_name}: {prop_type} {values}" if values else f"{prop_name}: {prop_type}")
        lines.append(f"{label} {{{', '.join(parts)}}}")
    return "\n".join(lines)


def _format_rel_props(schema: SchemaModel) -> str:
    lines = []
    for rel_type in sorted(schema.rel_props):
        parts = [f"{name}: {ptype}" for name, ptype in sorted(schema.rel_props[rel_type].items())]
        lines.append(f"{rel_type} {{{', '.join(parts)}}}")
    return "\n".join(lines)


def _format_relationship_patterns(schema: SchemaModel, excluded_labels: set[str]) -> str:
    seen = sorted(
        {(start, rel, end) for start, rel, end in schema.relationships if start not in excluded_labels and end not in excluded_labels}
    )
    return "\n".join(f"(:{start})-[:{rel}]->(:{end})" for start, rel, end in seen)


def _date_like_note(schema: SchemaModel, excluded_labels: set[str]) -> str | None:
    date_like = sorted(
        f"{label}.{prop}"
        for label, props in schema.node_props.items()
        if label not in excluded_labels
        for prop in props
        if props[prop] == "STRING" and _DATE_LIKE_RE.search(prop)
    )
    if not date_like:
        return None
    return (
        "Note: these properties hold date-like values stored as STRING, not a native Neo4j DATE "
        "(confirmed live): " + ", ".join(date_like) + ". Wrap in date(...) before comparing to "
        "date() or duration arithmetic — comparing the raw string silently matches nothing."
    )


def render_schema_text(schema: SchemaModel) -> str:
    excluded_labels, notes = _label_exclusions_and_notes(schema)
    date_note = _date_like_note(schema, excluded_labels)
    if date_note:
        notes.append(date_note)

    sections = [
        "Node properties:",
        _format_node_props(schema, excluded_labels),
        "",
        "Relationship properties:",
        _format_rel_props(schema),
        "",
        "The relationships:",
        _format_relationship_patterns(schema, excluded_labels),
    ]
    if notes:
        sections += [""] + notes
    return "\n".join(sections)


_cache: dict[tuple[int, str | None, int], tuple[SchemaModel, float]] = {}


def get_schema_model(
    driver: neo4j.Driver,
    database: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    enum_max_cardinality: int = DEFAULT_ENUM_MAX_CARDINALITY,
    force_refresh: bool = False,
) -> SchemaModel:
    """Live-introspect (or return the cached) SchemaModel for this driver/database.

    Cached in-process, keyed by driver identity, so one long-lived engine
    process doesn't hit Neo4j on every question, but also doesn't stay stale
    forever — pass force_refresh=True (see NL2CypherEngine.refresh_schema())
    to pick up a graph change immediately instead of waiting out the TTL.
    """
    key = (id(driver), database, enum_max_cardinality)
    cached = _cache.get(key)
    now = time.monotonic()
    if not force_refresh and cached and (now - cached[1]) < ttl_seconds:
        return cached[0]

    try:
        model = _introspect(driver, database, enum_max_cardinality)
    except Exception as exc:  # noqa: BLE001 - surface any APOC/driver failure clearly
        raise RuntimeError(
            "Live schema introspection failed (requires the APOC plugin on this Neo4j "
            f"instance — see Readme.md §4e). Underlying error: {exc}"
        ) from exc

    _cache[key] = (model, now)
    return model


def get_schema_context(
    driver: neo4j.Driver,
    database: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    enum_max_cardinality: int = DEFAULT_ENUM_MAX_CARDINALITY,
    force_refresh: bool = False,
) -> str:
    """Return the live-derived Neo4j schema string to hand to Text2CypherRetriever."""
    return render_schema_text(
        get_schema_model(driver, database, ttl_seconds, enum_max_cardinality, force_refresh)
    )


def filter_valid_examples(examples: list[str], schema: SchemaModel) -> list[str]:
    """Drop few-shot examples that reference a label or relationship type no
    longer present in the live schema, so a graph change doesn't leave a
    stale example actively teaching the LLM an incorrect pattern."""
    valid: list[str] = []
    for example in examples:
        query_part = example.split("QUERY:", 1)[-1]
        labels = set(_NODE_LABEL_RE.findall(query_part))
        rel_types = {rt for match in _REL_TYPE_RE.findall(query_part) for rt in match.split("|")}
        missing_labels = labels - schema.labels
        missing_rels = rel_types - schema.rel_types
        if missing_labels or missing_rels:
            logger.warning(
                "Dropping stale NL2Cypher example (references removed schema elements — "
                "labels=%s rels=%s): %s",
                missing_labels or None,
                missing_rels or None,
                example[:120],
            )
            continue
        valid.append(example)
    return valid
