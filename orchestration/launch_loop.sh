#!/bin/bash
# Launch a Ralph loop in a new iTerm window
# Usage: bash launch_loop.sh <working_dir> [prompt] [max_iterations] [project_dir]
#
# Arguments:
#   working_dir   - Directory where Claude Code will run (must contain CLAUDE.md)
#   prompt        - The prompt sent each iteration (default: "read CLAUDE.md and follow instructions")
#   max_iterations - Safety cap on iterations (default: 100)
#   project_dir   - Project dir for Claude plugins (default: same as working_dir)
#
# Prerequisites:
#   - iTerm2 installed
#   - Claude Code CLI installed and authenticated
#   - ralph-loop plugin installed (https://github.com/anthropics/claude-code-plugins)
#
# The script creates a new iTerm window, starts Claude Code with
# --dangerously-skip-permissions, and sends the /ralph-loop command.
# Window ID is tracked so commands target the correct terminal.

set -euo pipefail

WORK_DIR="${1:?Usage: bash launch_loop.sh <working_dir> [prompt] [max_iterations]}"
PROMPT="${2:-read CLAUDE.md and follow instructions}"
MAX_ITER="${3:-100}"
PROJECT_DIR="${4:-$WORK_DIR}"

echo "Launching Ralph loop..."
echo "  Dir: $WORK_DIR"
echo "  Prompt: $PROMPT"
echo "  Max iterations: $MAX_ITER"

# 1. Create new iTerm window and capture its ID for reliable targeting
WIN_ID=$(osascript << APPLESCRIPT
tell application "iTerm"
    set newWindow to (create window with default profile)
    return id of newWindow as string
end tell
APPLESCRIPT
)
echo "  Window ID: $WIN_ID"

# Helper: send text to our specific window by ID
send_to_window() {
    osascript << APPLESCRIPT
tell application "iTerm"
    repeat with w in windows
        if (id of w as string) = "$WIN_ID" then
            tell current session of w
                write text "$1"
            end tell
        end if
    end repeat
end tell
APPLESCRIPT
}

# 2. Start Claude with --dangerously-skip-permissions for fully autonomous operation
#    ANTHROPIC_API_KEY is unset to avoid interfering with OAuth authentication
send_to_window "cd $PROJECT_DIR && ANTHROPIC_API_KEY='' claude --dangerously-skip-permissions"
echo "  Sent claude command, waiting 3s for init..."
sleep 3

# 3. Append window ID to file so the agent can self-restart
#    Each launch appends — the agent's own ID is always the last line
WINDOW_ID_FILE="$WORK_DIR/.claude/window_id"
mkdir -p "$(dirname "$WINDOW_ID_FILE")"
echo "$WIN_ID" >> "$WINDOW_ID_FILE"
echo "  Window ID written to: $WINDOW_ID_FILE"

# 4. Send /ralph-loop command
send_to_window "/ralph-loop:ralph-loop $PROMPT --max-iterations $MAX_ITER"
echo ""
echo "Ralph loop started in iTerm window $WIN_ID"
echo "To stop: close the iTerm window or run /cancel-ralph in it"
