"""Preview the schema (and example set) NL2Cypher currently derives live from Neo4j.

Schema is no longer hand-curated (see schema_context.py) — there's nothing
left to diff against a static file. This is now a debugging/eyeballing tool:
run it right after changing the graph to confirm the new labels/relationships/
enum values show up, without waiting out the cache TTL or starting the full
engine. Every run is also appended to results/schema_snapshot_log.txt (same
style as results_writer.py's per-question log) so you can see how the
rendered schema and the filtered example set evolve as the live graph
changes, without having to ask a question through the full engine.

NL2CypherEngine also calls snapshot_if_new_graph() automatically on startup
(see engine.py) — whenever the (neo4j_uri, neo4j_database) it connects to
differs from the last one recorded in results/.last_connected_graph.txt, a
snapshot is appended here automatically, without needing to run this module
by hand.

Requires the APOC plugin (schema_context.py's introspection uses
neo4j_graphrag.schema.get_structured_schema, which is APOC-based).

Usage:
    python -m NL2Cypher.schema_diff
    python -m NL2Cypher.schema_diff --no-save
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from config.settings import Settings, get_settings

import neo4j

from .examples import NL2CYPHER_EXAMPLES
from .schema_context import SchemaModel, filter_valid_examples, get_schema_model, render_schema_text

DEFAULT_SNAPSHOT_LOG_PATH = Path(__file__).parent / "results" / "schema_snapshot_log.txt"
LAST_CONNECTED_GRAPH_MARKER = Path(__file__).parent / "results" / ".last_connected_graph.txt"

_SEPARATOR = "=" * 100

logger = logging.getLogger(__name__)


def _format_snapshot(model: SchemaModel, kept_examples: list[str], dropped_examples: list[str]) -> str:
    timestamp = datetime.now(timezone.utc).isoformat()
    lines = [
        _SEPARATOR,
        f"Timestamp: {timestamp}",
        "",
        f"Labels: {len(model.labels)}   Relationships: {len(model.rel_types)}   "
        f"Enum-annotated properties: {len(model.enum_values)}",
        "",
        "ENUM VALUES SAMPLED LIVE (label.property: values):",
    ]
    for (label, prop), values in sorted(model.enum_values.items()):
        lines.append(f"  - {label}.{prop}: {values}")
    lines += [
        "",
        "RENDERED SCHEMA TEXT (what NL2CypherEngine hands the LLM as neo4j_schema):",
        render_schema_text(model),
        "",
        f"FEW-SHOT EXAMPLES KEPT ({len(kept_examples)} / {len(kept_examples) + len(dropped_examples)}):",
    ]
    lines += [f"  - {ex}" for ex in kept_examples] or ["  (none)"]
    lines += [
        "",
        f"FEW-SHOT EXAMPLES DROPPED as stale ({len(dropped_examples)}):",
    ]
    lines += [f"  - {ex}" for ex in dropped_examples] or ["  (none)"]
    lines.append("")
    return "\n".join(lines)


def save_snapshot(snapshot: str, path: Path = DEFAULT_SNAPSHOT_LOG_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(snapshot)
        f.write("\n")
    return path


def _graph_fingerprint(settings: Settings) -> str:
    return f"{settings.neo4j_uri}|{settings.neo4j_database}"


def snapshot_if_new_graph(
    settings: Settings,
    model: SchemaModel,
    kept_examples: list[str],
    dropped_examples: list[str],
    marker_path: Path = LAST_CONNECTED_GRAPH_MARKER,
) -> bool:
    """Append a schema/examples snapshot iff this (neo4j_uri, neo4j_database)
    pair differs from the last one recorded in marker_path — called from
    NL2CypherEngine.__init__ so connecting to a graph you haven't seen before
    (or haven't seen since results/.last_connected_graph.txt was last
    updated) automatically leaves a record of what was derived from it,
    without needing to run this module by hand. Reuses the SchemaModel the
    engine already fetched — no extra Neo4j round trip. Returns True iff a
    snapshot was taken."""
    fingerprint = _graph_fingerprint(settings)
    try:
        previous = marker_path.read_text(encoding="utf-8").strip() if marker_path.exists() else None
    except OSError as exc:
        logger.warning("Could not read %s, snapshotting anyway: %s", marker_path, exc)
        previous = None

    if previous == fingerprint:
        return False

    snapshot = _format_snapshot(model, kept_examples, dropped_examples)
    try:
        save_snapshot(f"[auto: new graph connection detected — {fingerprint}]\n" + snapshot)
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(fingerprint, encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to save auto schema snapshot for %s: %s", fingerprint, exc)
        return False

    logger.info("New Neo4j graph connection detected (%s) — schema snapshot saved to %s", fingerprint, DEFAULT_SNAPSHOT_LOG_PATH)
    return True


def main() -> int:
    save = "--no-save" not in sys.argv[1:]

    settings = get_settings()
    driver = neo4j.GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        model = get_schema_model(
            driver,
            database=settings.neo4j_database,
            enum_max_cardinality=settings.schema_enum_max_cardinality,
            force_refresh=True,
        )
    finally:
        driver.close()

    kept_examples = filter_valid_examples(list(NL2CYPHER_EXAMPLES), model)
    dropped_examples = [ex for ex in NL2CYPHER_EXAMPLES if ex not in kept_examples]

    snapshot = _format_snapshot(model, kept_examples, dropped_examples)
    print(snapshot)

    if save:
        path = save_snapshot(snapshot)
        LAST_CONNECTED_GRAPH_MARKER.parent.mkdir(parents=True, exist_ok=True)
        LAST_CONNECTED_GRAPH_MARKER.write_text(_graph_fingerprint(settings), encoding="utf-8")
        print(f"Saved to: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
