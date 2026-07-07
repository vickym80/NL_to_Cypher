"""Integration tests for the NL2Cypher POC — require a live Neo4j instance AND a
configured LLM provider (this hits the real OpenAI/Anthropic API per question, so
it costs a few cents and several seconds per run; that's expected for this kind of
LLM-in-the-loop test, not a bug).

Every question here is deliberately NOT one of the few-shot examples in
examples.py — the point is to check the retriever generalizes to new phrasing on
this schema, not that it memorized the exact example strings.

Run with: pytest -m neo4j NL2Cypher/tests/test_nl2cypher.py -v
"""

from __future__ import annotations

import pytest

from NL2Cypher.engine import NL2CypherEngine

QUESTION_BANK = [
    "List all KPIs and their directionality.",
    "Which suppliers have tier 1?",
    "How many purchase orders have a status of delivered?",
    "What is the average actual lead time for purchase orders from Supplier_A?",
    "Which causal drivers directly contribute to forecast_error?",
    "What causal drivers can eventually affect fill_rate?",
    "List interventions owned by Procurement.",
    "Which shipments were not on-time-in-full?",
    "Show me distribution centers in the East region.",
    "Which supplier supplies SKU_1002?",
]


@pytest.fixture(scope="module")
def engine():
    with NL2CypherEngine() as eng:
        yield eng


@pytest.mark.neo4j
@pytest.mark.parametrize("question", QUESTION_BANK)
def test_generates_and_executes_read_only_cypher(engine: NL2CypherEngine, question: str) -> None:
    result = engine.ask(question)

    assert result.error is None, f"NL2Cypher failed for {question!r}: {result.error}"
    assert result.cypher, f"No Cypher generated for {question!r}"
    assert result.cypher.strip().upper().startswith(
        ("MATCH", "WITH", "CALL", "RETURN", "OPTIONAL")
    ), f"Generated query for {question!r} doesn't look read-only: {result.cypher}"

    forbidden = ("CREATE ", "MERGE ", "DELETE ", "SET ", "REMOVE ", "DROP ")
    upper_cypher = result.cypher.upper()
    assert not any(kw in upper_cypher for kw in forbidden), (
        f"Generated query for {question!r} contains a write keyword: {result.cypher}"
    )
