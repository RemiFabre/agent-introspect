# TODO

Ideas for future tools. Nothing here has been built yet.

## Context Window Introspection

An agent currently has no way to know how full its context is. The `/context` command shows this to users but there's no programmatic equivalent.

Possible approaches:
- Parse `/context` output the same way we parse `/usage` (pty + screen scraping)
- Use the OpenTelemetry export if it exposes context metrics
- Track token counts from API responses (if accessible from within Claude Code)

This would let agents decide when to compact, restart, or delegate to sub-agents.

Related: [anthropics/claude-code#34879](https://github.com/anthropics/claude-code/issues/34879)

## Hook-Based Context Watchdog

A Claude Code hook that runs after each tool call and warns the agent when context exceeds a threshold (e.g., 70%). Could inject a system reminder like "Context at 75% — consider wrapping up."

## Loop Health Monitor

A background process that watches a running ralph-loop and detects when it's stuck:
- Parse the transcript file in real-time
- Alert if N consecutive iterations have zero tool_use blocks
- Optionally kill and restart the loop automatically

## Cross-Agent Coordination

When a master agent spawns multiple loops, it needs to:
- Track which loops are alive (check iTerm window IDs)
- Allocate budget across loops based on remaining plan quota
- Collect results from all loops into a single summary

## MCP Server for Introspection

Package the usage probe and context introspection as an MCP server so any Claude Code session can call `get_usage()` and `get_context()` as tools without shelling out.
