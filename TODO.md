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

## Usage Limit Recovery (Auto-Restart)

When an agent hits 100% usage, the Claude Code session freezes — the agent can't do anything until the limit resets. The agent itself can't recover from this because it can't execute any code.

Solution: an external watchdog script that:
- Periodically checks usage via `introspection/usage.py --json`
- When it detects limits have refreshed (usage drops below a threshold), sends a message to the iTerm window to resume work
- Could also detect a frozen session (no transcript changes for N minutes while usage is at 100%) and automatically restart the loop with `launch_loop.sh`

This would make loops truly autonomous — they scale down near limits, and if they accidentally hit 100%, the watchdog brings them back.

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
