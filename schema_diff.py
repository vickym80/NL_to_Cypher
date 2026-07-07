"""Preview the schema NL2Cypher currently derives live from Neo4j.

Schema is no longer hand-curated (see schema_context.py) — there's nothing
left to diff against a static file. This is now a debugging/eyeballing tool:
run it right after changing the graph to confirm the new labels/relationships/
enum values show up, without waiting out the cache TTL or starting the full
engine.

Requires the APOC plugin (schema_context.py's introspection uses
neo4j_graphrag.schema.get_structured_schema, which is APOC-based).

Usage:
    python -m NL2Cypher.schema_diff
"""

from __future__ import annotations

from config.settings import get_settings

import neo4j

from .schema_context import get_schema_model, render_schema_text


def main() -> int:
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

    print(f"Labels:        {len(model.labels)}")
    print(f"Relationships: {len(model.rel_types)}")
    print(f"Enum-annotated properties: {len(model.enum_values)}")
    for (label, prop), values in sorted(model.enum_values.items()):
        print(f"  - {label}.{prop}: {values}")
    print()
    print(render_schema_text(model))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
