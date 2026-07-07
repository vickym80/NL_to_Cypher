"""Append each NL2Cypher run to a human-readable txt log: the NL question, the
GraphRAG context retrieved to generate the Cypher (schema + few-shot examples),
the generated Cypher itself, and the result rows.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .engine import NL2CypherResult

DEFAULT_LOG_PATH = Path(__file__).parent / "results" / "nl2cypher_log.txt"

_SEPARATOR = "=" * 100


def format_entry(result: NL2CypherResult) -> str:
    timestamp = datetime.now(timezone.utc).isoformat()
    examples_block = "\n".join(f"  - {ex}" for ex in result.examples_used) or "  (none)"

    lines = [
        _SEPARATOR,
        f"Timestamp: {timestamp}",
        "",
        "NL QUERY:",
        f"  {result.question}",
        "",
        "GRAPHRAG CONTEXT RETRIEVED (schema passed to the LLM):",
        result.schema_context.rstrip(),
        "",
        "GRAPHRAG CONTEXT RETRIEVED (few-shot examples passed to the LLM):",
        examples_block,
        "",
        "GENERATED CYPHER:",
        f"  {result.cypher or '(none — see error)'}",
        "",
    ]

    if result.error:
        lines += ["ERROR:", f"  {result.error}", ""]
    else:
        lines += [
            f"RESULT ({len(result.rows)} row(s)):",
            json.dumps(result.rows, indent=2, default=str),
            "",
        ]

    return "\n".join(lines)


def append_result(result: NL2CypherResult, path: Path = DEFAULT_LOG_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(format_entry(result))
        f.write("\n")
    return path
