# Orchestration: Autonomous Agent Loops

How to run long-lived autonomous Claude Code sessions using the [ralph-loop plugin](https://github.com/anthropics/claude-code-plugins).

## Quick Start

```bash
bash orchestration/launch_loop.sh /path/to/project "read CLAUDE.md and follow instructions" 100
```

This creates a new iTerm window, starts Claude Code, and begins a ralph-loop that reads your CLAUDE.md each iteration.

## How Ralph Loop Works

Ralph loop is a **stop hook** — it intercepts Claude's exit and feeds the prompt back:

1. Claude processes the prompt and does work
2. Claude tries to exit
3. The stop hook blocks the exit, increments the iteration counter, and feeds the same prompt again
4. This repeats until max_iterations or the completion promise is output

Key facts:
- **One continuous session.** Context accumulates across iterations — it does NOT reset
- **No session restart.** If your CLAUDE.md says "wait for session restart", nothing happens
- **Every response costs context.** Even "Holding." wastes tokens over thousands of iterations

## Writing a Good Loop Prompt (CLAUDE.md)

Lessons learned from debugging loops that wasted 93% of iterations:

### Do

- **Use foreground workers.** Background workers finish after the iteration ends, causing polling loops
- **Set a completion promise.** Give the agent a way to cleanly stop: `--completion-promise PAUSE_FOR_RESTART`
- **Explain the loop mechanics.** Tell the agent it's in a continuous session with no resets
- **Keep responses minimal.** Tell the agent context accumulates — don't write essays
- **Cap iterations per session.** "After ~20 iterations, output PAUSE_FOR_RESTART" prevents context overflow

### Don't

- **Don't use background workers.** They cause 10x wasted iterations from polling
- **Don't mention session resets.** There are none — the agent will "hold" forever
- **Don't skip the completion promise.** Without one, the only exit is max_iterations or killing the window
- **Don't let the agent track usage.** It wastes iterations checking limits instead of doing work

## iTerm Window Targeting

The launch script tracks the window ID for reliable targeting:

```applescript
-- Create and capture ID
set newWindow to (create window with default profile)
set winId to id of newWindow as string

-- Send commands to ONLY that window
repeat with w in windows
    if (id of w as string) = winId then
        tell current session of w
            write text "your command here"
        end tell
    end if
end repeat
```

Never use `current window` — it targets whatever window has focus.

## Gotchas

1. **ANTHROPIC_API_KEY must be unset** — it interferes with OAuth. The launch script handles this
2. **`--dangerously-skip-permissions`** is required for fully autonomous operation. Equivalent to `--permission-mode bypassPermissions`
3. **3-second init wait** is enough for Claude to start and auto-accept the permissions prompt
4. **The `/ralph-loop` syntax uses colons**: `/ralph-loop:ralph-loop <prompt> --max-iterations N`
