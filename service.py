"""Standalone FastAPI app for the NL2Cypher POC.

Deliberately separate from graphrag_service.main — a different app on a
different port (9060 by default, see start_nl2cypher.sh), so this experimental
surface can be iterated on/torn down without touching the RCA backend's app or
release cycle. No auth is wired here yet: this is a local POC test surface, not
a production endpoint — see docs/implementation_nl2cypher.md "Not done yet"
before exposing this beyond localhost.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .engine import NL2CypherEngine
from .results_writer import append_result

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="NL2Cypher POC",
    description="Ask the supply-chain knowledge graph a question in plain English; "
    "get back the generated (read-only) Cypher query and its results.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_engine: NL2CypherEngine | None = None


@app.on_event("startup")
def _startup() -> None:
    global _engine
    _engine = NL2CypherEngine()
    logger.info("NL2Cypher engine initialized")


@app.on_event("shutdown")
def _shutdown() -> None:
    if _engine is not None:
        _engine.close()


class QueryRequest(BaseModel):
    question: str
    max_rows: int = 100
    save: bool = True


class QueryResponse(BaseModel):
    question: str
    cypher: str | None
    rows: list[dict]
    error: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/nl2cypher/query", response_model=QueryResponse)
def query(payload: QueryRequest) -> QueryResponse:
    assert _engine is not None, "engine not initialized"
    result = _engine.ask(payload.question, max_rows=payload.max_rows)
    if payload.save:
        append_result(result)
    return QueryResponse(
        question=result.question,
        cypher=result.cypher,
        rows=result.rows,
        error=result.error,
    )
