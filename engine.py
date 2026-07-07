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
from typing import Any, Literal

import neo4j
from neo4j_graphrag.exceptions import SchemaFetchError, Text2CypherRetrievalError
from neo4j_graphrag.retrievers import Text2CypherRetriever
from neo4j_graphrag.schema import get_schema as get_live_schema
from neo4j_graphrag.types import RetrieverResultItem

from config.settings import Settings, get_settings

from .examples import NL2CYPHER_EXAMPLES
from .llm_provider import build_llm
from .schema_context import get_schema_context

SchemaSource = Literal["curated", "live"]

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
        include_instance_schema: bool = True,
        schema_source: SchemaSource = "live",
    ) -> None:
        """schema_source="live" (default) fetches the schema straight from Neo4j via
        neo4j_graphrag.schema.get_schema() on every engine startup — requires the
        APOC plugin. schema_source="curated" uses the hand-written schema_context.py
        string instead (works without APOC, smaller prompt, but can drift from the
        live graph — see NL2Cypher/schema_diff.py to check for that)."""
        self._settings = settings or get_settings()
        self.schema_source = schema_source
        self.examples = list(NL2CYPHER_EXAMPLES)
        self._driver = neo4j.GraphDatabase.driver(
            self._settings.neo4j_uri,
            auth=(
                self._settings.neo4j_user,
                self._settings.neo4j_password.get_secret_value(),
            ),
        )

        if schema_source == "live":
            try:
                self.schema_context = get_live_schema(self._driver)
            except Exception as exc:  # noqa: BLE001 - surface any APOC/driver failure clearly
                self._driver.close()
                raise RuntimeError(
                    "Live schema fetch failed (requires the APOC plugin on this Neo4j "
                    "instance). Pass schema_source='curated' to use the hand-written "
                    f"schema instead. Underlying error: {exc}"
                ) from exc
        else:
            self.schema_context = get_schema_context(include_instance_data=include_instance_schema)

        self._llm = build_llm(self._settings)
        self._retriever = Text2CypherRetriever(
            driver=self._driver,
            llm=self._llm,
            neo4j_schema=self.schema_context,
            examples=self.examples,
            result_formatter=_record_to_item,
            neo4j_database=self._settings.neo4j_database,
        )

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
