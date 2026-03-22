# agent-introspect

Tools for AI coding agents to introspect their own state and coordinate other agents.

## The Problem

AI coding agents (Claude Code, Codex, etc.) are blind to their own operational state. They don't know:
- How much of their rate limit they've used
- How full their context window is
- Whether they should spawn sub-agents or conserve budget
- When to gracefully stop and restart

This repo provides tools that let agents answer these questions about themselves.

## What's Here

### `introspection/usage.py` — Plan Usage Probe

Launches a headless Claude Code session, opens `/usage`, parses the TUI output, and returns structured JSON with session/week usage percentages, reset times, and recommended effort levels. Designed to be called by agents to make budget decisions.

```bash
python3 introspection/usage.py --json
```

Returns:
```json
{
  "session": {"used_pct": 45, "remaining_pct": 55, "resets_in_seconds": 3600},
  "week": {"used_pct": 30, "remaining_pct": 70, "resets_in_seconds": 259200},
  "agent_budget": {"primary_available_pct": 44.0},
  "recommended_effort": "high"
}
```

### `orchestration/launch_loop.sh` — Launch Autonomous Agent Loops

Starts a [Ralph Loop](https://github.com/anthropics/claude-code-plugins) session in a new iTerm window via AppleScript. Handles window targeting, permissions, and the `/ralph-loop` command syntax.

```bash
bash orchestration/launch_loop.sh /path/to/project "read CLAUDE.md and follow instructions" 100
```

### `analysis/session_analyzer.py` — Analyze Loop Efficiency

Reads Claude Code JSONL session transcripts and reports how many iterations were productive vs wasted. Useful for debugging autonomous loops that get stuck.

```bash
python3 analysis/session_analyzer.py ~/.claude/projects/<project>/<session>.jsonl
```

## Planned

See [TODO.md](TODO.md) for ideas we haven't built yet, including context window introspection, hook-based watchdogs, and cross-agent coordination.

## Philosophy

Everything here is **for the agent to use about itself**, not for a human dashboard. External monitoring tools exist (ccusage, tokscale, Grafana dashboards). This repo fills a different gap: self-awareness.
