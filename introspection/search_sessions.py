#!/usr/bin/env python3
"""Search Claude Code session history for keywords.

Designed for agents: returns compact, token-efficient output.
Avoids dumping large text — reports matches as structured summaries.

Usage:
    # Quick search — just list matching sessions
    python3 search_sessions.py "set_full_target"

    # Search with multiple keywords (AND logic)
    python3 search_sessions.py "set_full_target" "branch 224"

    # OR logic — match any keyword
    python3 search_sessions.py --any "set_full_target" "SetFullTargetCmd"

    # Show short context snippets around matches
    python3 search_sessions.py --snippets "set_full_target"

    # Search only user messages in history.jsonl (fastest, cheapest)
    python3 search_sessions.py --history-only "set_full_target"

    # Limit to specific project folder pattern
    python3 search_sessions.py --project "reachy-mini" "set_full_target"

    # JSON output for programmatic use
    python3 search_sessions.py --json "set_full_target"

    # Exclude the current session
    python3 search_sessions.py --exclude c4ab8086 "set_full_target"
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"


def find_session_files(project_filter: str | None = None) -> list[Path]:
    """Find all main session JSONL files (exclude subagents)."""
    if not PROJECTS_DIR.exists():
        return []
    results = []
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        if project_filter and project_filter.lower() not in project_dir.name.lower():
            continue
        for f in project_dir.glob("*.jsonl"):
            # Skip sessions-index.json and subagent files
            if "subagents" in str(f):
                continue
            results.append(f)
    return sorted(results, key=lambda f: f.stat().st_mtime, reverse=True)


def search_file_for_keywords(filepath: Path, keywords: list[str], match_any: bool = False) -> dict | None:
    """Search a file for keywords. Returns match info or None.

    match_any=False: AND logic (all keywords must be present)
    match_any=True: OR logic (any keyword matches)
    """
    try:
        content = filepath.read_text(errors="replace")
    except (OSError, PermissionError):
        return None

    keyword_matches = {}
    for kw in keywords:
        count = len(re.findall(re.escape(kw), content, re.IGNORECASE))
        if count > 0:
            keyword_matches[kw] = count

    if not keyword_matches:
        return None
    if not match_any and len(keyword_matches) < len(keywords):
        return None

    # Extract metadata
    stat = filepath.stat()
    mod_time = datetime.fromtimestamp(stat.st_mtime)
    size_kb = stat.st_size / 1024

    # Try to extract git branch, cwd, and session ID from first few lines
    git_branch = None
    cwd = None
    session_id = filepath.stem

    with open(filepath, "r", errors="replace") as f:
        first_line = f.readline()
    try:
        first_obj = json.loads(first_line)
        git_branch = first_obj.get("gitBranch")
        cwd = first_obj.get("cwd")
    except (json.JSONDecodeError, ValueError):
        pass

    # If cwd not in first line, try to find it in the first few lines
    if not cwd:
        with open(filepath, "r", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 5:
                    break
                try:
                    obj = json.loads(line)
                    if obj.get("cwd"):
                        cwd = obj["cwd"]
                        break
                except (json.JSONDecodeError, ValueError):
                    continue

    return {
        "session_id": session_id,
        "project": cwd or filepath.parent.name,
        "git_branch": git_branch,
        "modified": mod_time.strftime("%Y-%m-%d %H:%M"),
        "size_kb": round(size_kb),
        "keyword_matches": keyword_matches,
        "path": str(filepath),
    }


def get_snippets(filepath: Path, keywords: list[str], max_snippets: int = 3, context_chars: int = 80) -> list[str]:
    """Extract short text snippets around keyword matches (from user messages only)."""
    snippets = []
    try:
        with open(filepath, "r", errors="replace") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                # Only look at user messages
                if obj.get("role") != "human":
                    # Also check history.jsonl format
                    if "display" not in obj:
                        continue

                # Get the text content
                text = ""
                if "display" in obj:
                    text = obj["display"]
                elif "content" in obj:
                    for block in obj.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            text += block.get("text", "")

                if not text:
                    continue

                for kw in keywords:
                    idx = text.lower().find(kw.lower())
                    if idx >= 0:
                        start = max(0, idx - context_chars)
                        end = min(len(text), idx + len(kw) + context_chars)
                        snippet = text[start:end].replace("\n", " ").strip()
                        if start > 0:
                            snippet = "..." + snippet
                        if end < len(text):
                            snippet = snippet + "..."
                        snippets.append(snippet)
                        if len(snippets) >= max_snippets:
                            return snippets
    except (OSError, PermissionError):
        pass
    return snippets


def search_history_only(keywords: list[str], match_any: bool = False) -> list[dict]:
    """Search only history.jsonl (user messages). Much faster for broad searches."""
    if not HISTORY_FILE.exists():
        return []

    # Group matches by session
    sessions = {}
    with open(HISTORY_FILE, "r", errors="replace") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

            display = obj.get("display", "")
            if not display:
                continue

            # Check keywords (AND or OR)
            display_lower = display.lower()
            if match_any:
                if not any(kw.lower() in display_lower for kw in keywords):
                    continue
            else:
                if not all(kw.lower() in display_lower for kw in keywords):
                    continue

            sid = obj.get("sessionId", "unknown")
            ts = obj.get("timestamp", 0)
            project = obj.get("project", "")
            dt = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M") if ts else "unknown"

            if sid not in sessions:
                sessions[sid] = {
                    "session_id": sid,
                    "project": project,
                    "first_match": dt,
                    "match_count": 0,
                    "sample": display[:120].replace("\n", " "),
                }
            sessions[sid]["match_count"] += 1

    return sorted(sessions.values(), key=lambda x: x.get("first_match", ""), reverse=True)


def format_results(results: list[dict], show_snippets: bool = False, keywords: list[str] | None = None) -> str:
    """Format results as compact human-readable text."""
    if not results:
        return "No matching sessions found."

    lines = [f"Found {len(results)} matching session(s):\n"]
    for r in results:
        lines.append(f"  Session: {r['session_id']}")
        lines.append(f"  Project: {r.get('project', '?')}")
        if r.get("git_branch"):
            lines.append(f"  Branch:  {r['git_branch']}")
        lines.append(f"  Date:    {r.get('modified', r.get('first_match', '?'))}")

        if "size_kb" in r:
            lines.append(f"  Size:    {r['size_kb']}KB")
        if "keyword_matches" in r:
            matches_str = ", ".join(f'"{k}": {v}' for k, v in r["keyword_matches"].items())
            lines.append(f"  Matches: {matches_str}")
        if "match_count" in r:
            lines.append(f"  Matches: {r['match_count']}")
        if r.get("sample"):
            lines.append(f"  Sample:  {r['sample']}")

        if show_snippets and keywords and "path" in r:
            snippets = get_snippets(Path(r["path"]), keywords)
            if snippets:
                lines.append("  Snippets:")
                for s in snippets:
                    lines.append(f"    - {s}")

        lines.append(f"  Resume:  claude --resume {r['session_id']}")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Search Claude Code session history")
    parser.add_argument("keywords", nargs="+", help="Keywords to search for (AND logic by default)")
    parser.add_argument("--any", action="store_true", dest="match_any", help="OR logic: match any keyword instead of all")
    parser.add_argument("--snippets", action="store_true", help="Show context snippets around matches")
    parser.add_argument("--history-only", action="store_true", help="Search only user messages in history.jsonl (fastest)")
    parser.add_argument("--project", type=str, help="Filter to projects matching this pattern")
    parser.add_argument("--exclude", type=str, help="Exclude sessions whose ID starts with this prefix")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    parser.add_argument("--limit", type=int, default=10, help="Max results to show (default: 10)")
    args = parser.parse_args()

    if args.history_only:
        results = search_history_only(args.keywords, match_any=args.match_any)
    else:
        files = find_session_files(args.project)
        results = []
        for f in files:
            match = search_file_for_keywords(f, args.keywords, match_any=args.match_any)
            if match:
                results.append(match)

    if args.exclude:
        results = [r for r in results if not r["session_id"].startswith(args.exclude)]

    results = results[: args.limit]

    if args.json:
        # Remove 'path' from JSON output to keep it clean
        clean = [{k: v for k, v in r.items() if k != "path"} for r in results]
        print(json.dumps(clean, indent=2))
    else:
        print(format_results(results, show_snippets=args.snippets, keywords=args.keywords))


if __name__ == "__main__":
    main()
