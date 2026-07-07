"""Compare the hand-curated schema (schema_context.py) against a live introspection
of Neo4j via neo4j_graphrag.schema.get_schema(), to catch drift when the ontology
changes.

Requires the APOC plugin to be installed and enabled on the Neo4j instance
(get_schema() calls apoc.meta.data/apoc.schema.nodes internally) — without it this
raises neo4j.exceptions.ClientError: ProcedureNotFound. APOC is NOT required for
NL2Cypher's normal operation (engine.py uses the curated schema, not live fetch);
this script is a periodic/manual sanity check, not something run on every question.

Usage:
    python -m NL2Cypher.schema_diff
"""

from __future__ import annotations

import re
import sys

import neo4j
from neo4j_graphrag.schema import get_schema

from config.settings import get_settings

from .schema_context import get_schema_context

_LABEL_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\{")


def _extract_labels(schema_text: str) -> set[str]:
    return {
        m.group(1)
        for line in schema_text.splitlines()
        if (m := _LABEL_LINE_RE.match(line.strip()))
    }


def main() -> int:
    settings = get_settings()
    driver = neo4j.GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        live_schema = get_schema(driver)
    finally:
        driver.close()

    curated_schema = get_schema_context(include_instance_data=True)

    live_labels = _extract_labels(live_schema)
    curated_labels = _extract_labels(curated_schema)

    # Generic super-labels every node also carries — not worth flagging.
    live_labels -= {"GraphNode", "InstanceRecord"}

    missing_from_curated = sorted(live_labels - curated_labels)
    stale_in_curated = sorted(curated_labels - live_labels)

    print(f"Live labels:    {len(live_labels)}")
    print(f"Curated labels: {len(curated_labels)}")
    print()

    if missing_from_curated:
        print("Labels live in Neo4j but MISSING from NL2Cypher/schema_context.py:")
        for label in missing_from_curated:
            print(f"  - {label}")
    else:
        print("No labels missing from the curated schema.")

    print()
    if stale_in_curated:
        print("Labels in the curated schema but NOT found live (possibly stale):")
        for label in stale_in_curated:
            print(f"  - {label}")
    else:
        print("No stale labels in the curated schema.")

    return 1 if missing_from_curated else 0


if __name__ == "__main__":
    raise SystemExit(main())
