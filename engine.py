"""Core NL2Cypher engine: wraps neo4j_graphrag's Text2CypherRetriever against the
live Neo4j instance this project already uses.

Read-only by construction: Text2CypherRetriever itself refuses to execute any
generated query whose query_type isn't "r" (raises Text2CypherRetrievalError).
As defense in depth (the library's check is application code, not enforced by the
database), point NEO4J_USER at a read-only Neo4j role for this feature in any
non-trivial deployment — see docs/implementation_nl2cypher.md "Safety" section.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import neo4j
from neo4j_graphrag.exceptions import SchemaFetchError, Text2CypherRetrievalError
from neo4j_graphrag.retrievers import Text2CypherRetriever
from neo4j_graphrag.types import RetrieverResultItem

from config.settings import Settings, get_settings

from .examples import NL2CYPHER_EXAMPLES
from .llm_provider import build_llm
from .schema_context import filter_valid_examples, get_schema_model, render_schema_text

logger = logging.getLogger(__name__)


@dataclass
class NL2CypherResult:
    question: str
    cypher: str | None
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    schema_context: str = ""
    examples_used: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


def _record_to_item(record: neo4j.Record) -> RetrieverResultItem:
    """Keep result rows as plain dicts (for JSON/UI use) instead of the library's
    default str(record) formatting."""
    return RetrieverResultItem(content=record.data())


class NL2CypherEngine:
    """One engine instance per process — holds a Neo4j driver + LLM client."""

    def __init__(
        self,
        settings: Settings | None = None,
        schema_ttl_seconds: int | None = None,
        force_schema_refresh: bool = False,
    ) -> None:
        """Schema is always derived live from Neo4j (see schema_context.py) —
        cached in-process for schema_ttl_seconds (defaults to
        settings.schema_cache_ttl_seconds) so a long-lived process doesn't
        requery Neo4j on every question. Pass force_schema_refresh=True (or
        call .refresh_schema() later) to bypass the cache and pick up a graph
        change immediately."""
        self._settings = settings or get_settings()
        self._driver = neo4j.GraphDatabase.driver(
            self._settings.neo4j_uri,
            auth=(
                self._settings.neo4j_user,
                self._settings.neo4j_password.get_secret_value(),
            ),
        )
        self._schema_ttl_seconds = (
            schema_ttl_seconds if schema_ttl_seconds is not None else self._settings.schema_cache_ttl_seconds
        )

        try:
            schema_model = get_schema_model(
                self._driver,
                database=self._settings.neo4j_database,
                ttl_seconds=self._schema_ttl_seconds,
                enum_max_cardinality=self._settings.schema_enum_max_cardinality,
                force_refresh=force_schema_refresh,
            )
        except RuntimeError:
            self._driver.close()
            raise

        self.schema_context = render_schema_text(schema_model)
        self.examples = filter_valid_examples(list(NL2CYPHER_EXAMPLES), schema_model)

        self._llm = build_llm(self._settings)
        self._retriever = Text2CypherRetriever(
            driver=self._driver,
            llm=self._llm,
            neo4j_schema=self.schema_context,
            examples=self.examples,
            result_formatter=_record_to_item,
            neo4j_database=self._settings.neo4j_database,
        )

    def refresh_schema(self) -> None:
        """Re-derive the schema + example set from Neo4j right now, bypassing
        the TTL cache, and push them into the live retriever. Text2CypherRetriever
        reads neo4j_schema/examples fresh on every .search() call (confirmed in
        neo4j_graphrag's Text2CypherRetriever.get_search_results), so mutating
        these attributes takes effect on the very next ask() — no need to rebuild
        the driver, LLM, or retriever."""
        schema_model = get_schema_model(
            self._driver,
            database=self._settings.neo4j_database,
            ttl_seconds=self._schema_ttl_seconds,
            enum_max_cardinality=self._settings.schema_enum_max_cardinality,
            force_refresh=True,
        )
        self.schema_context = render_schema_text(schema_model)
        self.examples = filter_valid_examples(list(NL2CYPHER_EXAMPLES), schema_model)
        self._retriever.neo4j_schema = self.schema_context
        self._retriever.examples = self.examples

    def ask(self, question: str, max_rows: int = 100) -> NL2CypherResult:
        try:
            result = self._retriever.search(query_text=question)
        except Text2CypherRetrievalError as exc:
            logger.warning("NL2Cypher retrieval failed for %r: %s", question, exc)
            return NL2CypherResult(
                question=question,
                cypher=None,
                error=str(exc),
                schema_context=self.schema_context,
                examples_used=self.examples,
            )
        except SchemaFetchError as exc:
            logger.error("NL2Cypher schema context error: %s", exc)
            return NL2CypherResult(
                question=question,
                cypher=None,
                error=f"schema error: {exc}",
                schema_context=self.schema_context,
                examples_used=self.examples,
            )

        cypher = (result.metadata or {}).get("cypher")
        rows = [item.content for item in result.items][:max_rows]
        return NL2CypherResult(
            question=question,
            cypher=cypher,
            rows=rows,
            schema_context=self.schema_context,
            examples_used=self.examples,
        )

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "NL2CypherEngine":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()
