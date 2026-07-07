"""Quick manual test harness — no FastAPI server required.

Every run is appended to NL2Cypher/results/nl2cypher_log.txt (NL query, the
GraphRAG context retrieved, the generated Cypher, and the result) unless
--no-save is passed. Schema defaults to a LIVE fetch from Neo4j (requires APOC);
pass --curated-schema to use the hand-written schema_context.py instead.

Usage:
    python -m NL2Cypher.cli "Which suppliers ship to DC_EAST?"
    python -m NL2Cypher.cli --curated-schema "Which suppliers ship to DC_EAST?"
    python -m NL2Cypher.cli --no-save "Which suppliers ship to DC_EAST?"
    python -m NL2Cypher.cli --save-to path/to/log.txt "..."
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .engine import NL2CypherEngine
from .results_writer import DEFAULT_LOG_PATH, append_result


def main() -> int:
    args = sys.argv[1:]
    save = True
    log_path = DEFAULT_LOG_PATH
    schema_source = "live"

    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if flag == "--no-save":
            save = False
        elif flag == "--curated-schema":
            schema_source = "curated"
        elif flag == "--save-to":
            log_path = Path(args.pop(0))
        else:
            print(f"Unknown flag: {flag}", file=sys.stderr)
            return 1

    if not args:
        print(
            'Usage: python -m NL2Cypher.cli ["--no-save"] ["--curated-schema"] '
            '["--save-to <path>"] "<question>"',
            file=sys.stderr,
        )
        return 1

    question = " ".join(args)
    with NL2CypherEngine(schema_source=schema_source) as engine:
        result = engine.ask(question)

    print(f"Question: {result.question}")
    print(f"Cypher:   {result.cypher}")
    if result.error:
        print(f"Error:    {result.error}")
    else:
        print(f"Rows ({len(result.rows)}):")
        print(json.dumps(result.rows, indent=2, default=str))

    if save:
        written_to = append_result(result, log_path)
        print(f"\nSaved to: {written_to}")

    return 1 if result.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
