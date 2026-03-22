#!/usr/bin/env python3
"""
Analyze Claude Code session transcripts for loop efficiency.

Reads a JSONL transcript file (from ~/.claude/projects/<project>/<session>.jsonl)
and reports how many iterations were productive vs wasted. Useful for debugging
autonomous loops that get stuck polling or repeating without doing real work.

Usage:
    python3 session_analyzer.py /path/to/session.jsonl
    python3 session_analyzer.py /path/to/session.jsonl --min-tools 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def analyze_transcript(path: str, min_tools: int = 2) -> dict:
    lines = Path(path).read_text().splitlines()

    # Find iteration boundaries (stop hook feedback or ralph loop activation)
    boundaries: list[int] = []
    for i, line in enumerate(lines):
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        content = str(msg.get("message", {}).get("content", ""))
        if "Stop hook feedback" in content or "Ralph loop activated" in content:
            boundaries.append(i)

    if not boundaries:
        return {"error": "No iteration boundaries found — is this a ralph-loop transcript?"}

    # Analyze each iteration
    iterations: list[dict] = []
    for idx in range(len(boundaries)):
        start = boundaries[idx]
        end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(lines)

        tools = 0
        agents = 0
        texts: list[str] = []

        for j in range(start, end):
            try:
                msg = json.loads(lines[j])
            except (json.JSONDecodeError, ValueError):
                continue

            if msg.get("type") == "assistant":
                content = msg.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "tool_use":
                                tools += 1
                                if block.get("name") == "Agent":
                                    agents += 1
                            elif block.get("type") == "text" and block.get("text", "").strip():
                                texts.append(block["text"].strip()[:120])

        iterations.append({
            "index": idx + 1,
            "tools": tools,
            "agents": agents,
            "msg_count": end - start,
            "last_text": texts[-1] if texts else "",
        })

    productive = [it for it in iterations if it["tools"] > min_tools]
    empty = [it for it in iterations if it["tools"] <= min_tools]

    # Find where productive work stopped
    last_productive_idx = 0
    for it in iterations:
        if it["tools"] > min_tools:
            last_productive_idx = it["index"]

    wasted_after = len(iterations) - last_productive_idx if last_productive_idx > 0 else 0

    return {
        "transcript": path,
        "total_lines": len(lines),
        "total_iterations": len(iterations),
        "productive_iterations": len(productive),
        "empty_iterations": len(empty),
        "productive_pct": round(len(productive) / max(len(iterations), 1) * 100, 1),
        "last_productive_iteration": last_productive_idx,
        "wasted_iterations_after_last_productive": wasted_after,
        "waste_pct": round(wasted_after / max(len(iterations), 1) * 100, 1),
        "productive_details": [
            {
                "iteration": it["index"],
                "tools": it["tools"],
                "agents": it["agents"],
                "text": it["last_text"],
            }
            for it in productive[:30]
        ],
        "tail_sample": [
            {
                "iteration": it["index"],
                "tools": it["tools"],
                "text": it["last_text"],
            }
            for it in iterations[-5:]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Claude Code session transcripts")
    parser.add_argument("transcript", help="Path to .jsonl transcript file")
    parser.add_argument(
        "--min-tools",
        type=int,
        default=2,
        help="Minimum tool_use blocks to count an iteration as productive (default: 2)",
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    result = analyze_transcript(args.transcript, args.min_tools)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    print(f"Session: {result['transcript']}")
    print(f"Lines: {result['total_lines']}")
    print(f"Iterations: {result['total_iterations']}")
    print(f"Productive: {result['productive_iterations']} ({result['productive_pct']}%)")
    print(f"Empty: {result['empty_iterations']}")
    print(f"Last productive: iteration {result['last_productive_iteration']}")
    print(f"Wasted after: {result['wasted_iterations_after_last_productive']} ({result['waste_pct']}%)")

    print(f"\n--- Productive iterations ---")
    for it in result["productive_details"]:
        print(f"  #{it['iteration']}: {it['tools']} tools, {it['agents']} agents — {it['text'][:80]}")

    print(f"\n--- Last 5 iterations ---")
    for it in result["tail_sample"]:
        print(f"  #{it['iteration']}: {it['tools']} tools — {it['text'][:80]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
