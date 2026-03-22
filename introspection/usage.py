#!/usr/bin/env python3
"""
Reliable Claude Code usage probe.

This script launches Claude Code in a pseudo-terminal, opens /usage, renders
the terminal screen locally, and parses the final on-screen values. It avoids
sleep-based scraping of raw ANSI output and keeps a machine-readable summary
for agents.

Usage:
    python3 usage.py --json                    # JSON output for agents
    python3 usage.py --cwd /path/to/project    # specify trusted project dir
    python3 usage.py --progress                # show progress on stderr

Environment variables:
    CLAUDE_USAGE_CWD       Trusted working directory (default: current dir)
    CLAUDE_USAGE_TIMEOUT   Timeout in seconds (default: 20)
    CLAUDE_USAGE_TZ        Timezone for reset times (default: UTC)
    AGENT_BUDGET           Fraction of remaining budget for agents (default: 0.8)
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pty
import re
import select
import signal
import shutil
import struct
import sys
import termios
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo


DEFAULT_CWD = os.getcwd()
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_AGENT_BUDGET = 0.8
DEFAULT_TZ = "UTC"
SCREEN_COLUMNS = 120
SCREEN_ROWS = 40
USAGE_LABELS = (
    "Current session",
    "Current week (all models)",
    "Current week (Sonnet only)",
)


@dataclass
class UsageWindow:
    label: str
    used_pct: int
    reset_text: str | None = None
    reset_dt: datetime | None = None

    @property
    def remaining_pct(self) -> int:
        return max(0, 100 - self.used_pct)


class TerminalScreen:
    """Very small ANSI screen renderer for Claude's usage TUI."""

    def __init__(self, width: int = SCREEN_COLUMNS, height: int = SCREEN_ROWS) -> None:
        self.width = width
        self.height = height
        self.lines = [[" "] * width for _ in range(height)]
        self.x = 0
        self.y = 0
        self.saved = (0, 0)
        self.state = "normal"
        self.csi = ""

    def scroll(self) -> None:
        self.lines.pop(0)
        self.lines.append([" "] * self.width)
        self.y = self.height - 1

    def put(self, ch: str) -> None:
        if ch == "\t":
            for _ in range(8 - (self.x % 8)):
                self.put(" ")
            return

        if self.x >= self.width:
            self.x = 0
            self.y += 1
            if self.y >= self.height:
                self.scroll()

        if 0 <= self.y < self.height and 0 <= self.x < self.width:
            self.lines[self.y][self.x] = ch
        self.x += 1

    def erase_line(self, mode: int) -> None:
        if not (0 <= self.y < self.height):
            return

        if mode == 0:
            start, end = self.x, self.width
        elif mode == 1:
            start, end = 0, self.x + 1
        else:
            start, end = 0, self.width

        for i in range(max(0, start), min(self.width, end)):
            self.lines[self.y][i] = " "

    def erase_display(self, mode: int) -> None:
        if mode == 2:
            for row in self.lines:
                for i in range(self.width):
                    row[i] = " "
            self.x = 0
            self.y = 0
            return

        if mode == 0:
            self.erase_line(0)
            for y in range(self.y + 1, self.height):
                for x in range(self.width):
                    self.lines[y][x] = " "
            return

        if mode == 1:
            self.erase_line(1)
            for y in range(0, self.y):
                for x in range(self.width):
                    self.lines[y][x] = " "

    def handle_csi(self, params: str, final: str) -> None:
        private = params.startswith("?")
        if private:
            params = params[1:]

        parts = [part for part in params.split(";") if part]
        nums = [int(part) if part.isdigit() else 0 for part in parts] if parts else []
        first = nums[0] if nums else 1

        if final == "A":
            self.y = max(0, self.y - first)
        elif final == "B":
            self.y = min(self.height - 1, self.y + first)
        elif final == "C":
            self.x = min(self.width - 1, self.x + first)
        elif final == "D":
            self.x = max(0, self.x - first)
        elif final in ("H", "f"):
            row = (nums[0] if len(nums) >= 1 and nums[0] else 1) - 1
            col = (nums[1] if len(nums) >= 2 and nums[1] else 1) - 1
            self.y = max(0, min(self.height - 1, row))
            self.x = max(0, min(self.width - 1, col))
        elif final == "G":
            col = (nums[0] if nums and nums[0] else 1) - 1
            self.x = max(0, min(self.width - 1, col))
        elif final == "K":
            self.erase_line(nums[0] if nums else 0)
        elif final == "J":
            self.erase_display(nums[0] if nums else 0)
        elif final == "s":
            self.saved = (self.x, self.y)
        elif final == "u":
            self.x, self.y = self.saved

    def feed(self, text: str) -> None:
        for ch in text:
            if self.state == "normal":
                if ch == "\x1b":
                    self.state = "esc"
                elif ch == "\r":
                    self.x = 0
                elif ch == "\n":
                    self.y += 1
                    if self.y >= self.height:
                        self.scroll()
                elif ch == "\b":
                    self.x = max(0, self.x - 1)
                elif ch >= " ":
                    self.put(ch)
            elif self.state == "esc":
                if ch == "[":
                    self.state = "csi"
                    self.csi = ""
                elif ch == "7":
                    self.saved = (self.x, self.y)
                    self.state = "normal"
                elif ch == "8":
                    self.x, self.y = self.saved
                    self.state = "normal"
                else:
                    self.state = "normal"
            elif self.state == "csi":
                if "@" <= ch <= "~":
                    self.handle_csi(self.csi, ch)
                    self.state = "normal"
                    self.csi = ""
                else:
                    self.csi += ch

    def text(self) -> str:
        return "\n".join("".join(line).rstrip() for line in self.lines)


def set_winsize(fd: int, rows: int = SCREEN_ROWS, cols: int = SCREEN_COLUMNS) -> None:
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


def read_until(
    fd: int,
    screen: TerminalScreen,
    predicate: Callable[[str], bool],
    timeout_seconds: float,
) -> tuple[bool, str]:
    start = time.time()
    raw_chunks: list[str] = []

    while time.time() - start < timeout_seconds:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if fd not in ready:
            continue

        try:
            data = os.read(fd, 65536)
        except OSError:
            break

        if not data:
            break

        text = data.decode("utf-8", "ignore")
        raw_chunks.append(text)
        screen.feed(text)

        if predicate(screen.text()):
            return True, "".join(raw_chunks)

    return False, "".join(raw_chunks)


def cleanup_child(pid: int, fd: int | None, grace_seconds: float = 1.0) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass

    deadline = time.time() + grace_seconds

    def wait_nonblocking() -> bool:
        try:
            waited_pid, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return True
        return waited_pid == pid

    if wait_nonblocking():
        return

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return

        while time.time() < deadline or sig == signal.SIGKILL:
            if wait_nonblocking():
                return
            time.sleep(0.05)
            if sig == signal.SIGKILL:
                deadline = time.time() + 0.5


def maybe_progress(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[usage] {message}", file=sys.stderr, flush=True)


def summarize_screen(screen_text: str, max_lines: int = 24) -> str:
    lines = [line.rstrip() for line in screen_text.splitlines()]
    non_empty = [line for line in lines if line.strip()]
    tail = non_empty[-max_lines:] if non_empty else []
    return "\n".join(tail).strip()


def normalize_screen_text(screen_text: str) -> str:
    text = screen_text.replace("\xa0", " ")
    text = re.sub(r"[\u2500-\u257F\u2580-\u259F]", " ", text)
    text = text.replace("\u276f", " ").replace("\u2733", " ").replace("\u23f5", " ").replace("\u25cf", " ")

    normalized_lines: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned:
            normalized_lines.append(cleaned)

    return "\n".join(normalized_lines)


def canonicalize_clock(value: str) -> str:
    value = value.strip().lower().replace(" ", "")
    hour, minute = parse_clock(value)
    suffix = "am" if hour < 12 else "pm"
    display_hour = hour % 12
    if display_hour == 0:
        display_hour = 12
    if ":" in value:
        return f"{display_hour}:{minute:02d}{suffix}"
    return f"{display_hour}{suffix}"


def parse_usage_screen(
    screen_text: str,
    tz_name: str = DEFAULT_TZ,
    require_sonnet: bool = True,
) -> tuple[UsageWindow, UsageWindow, UsageWindow | None]:
    normalized = normalize_screen_text(screen_text)
    positions: dict[str, int] = {}

    for label in USAGE_LABELS:
        index = normalized.find(label)
        if index >= 0:
            positions[label] = index

    # Build a timezone-aware pattern — Claude shows resets in the user's local tz
    tz_pattern = re.escape(tz_name).replace(r"/", r"/")

    def parse_window(label: str) -> UsageWindow:
        if label not in positions:
            raise ValueError(f"Could not find {label!r} in Claude /usage screen")

        start = positions[label] + len(label)
        next_positions = [
            positions[other]
            for other in USAGE_LABELS
            if other in positions and positions[other] > positions[label]
        ]
        end = min(next_positions) if next_positions else len(normalized)
        block_text = normalized[start:end]

        used_match = re.search(r"(\d+)\s*%\W*used", block_text, re.I)
        if not used_match:
            raise ValueError(f"Could not parse usage percentage for {label!r}")

        reset_text = None
        week_reset_match = re.search(
            r"([A-Z][a-z]{2}\s+\d{1,2})\s+at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s*\(" + tz_pattern + r"\)",
            block_text,
            re.I,
        )
        if week_reset_match:
            reset_text = (
                f"{week_reset_match.group(1)} at "
                f"{canonicalize_clock(week_reset_match.group(2))} ({tz_name})"
            )
        else:
            session_reset_match = re.search(
                r"(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s*\(" + tz_pattern + r"\)",
                block_text,
                re.I,
            )
            if session_reset_match:
                reset_text = f"{canonicalize_clock(session_reset_match.group(1))} ({tz_name})"

        return UsageWindow(
            label=label,
            used_pct=int(used_match.group(1)),
            reset_text=reset_text,
        )

    session = parse_window("Current session")
    week = parse_window("Current week (all models)")
    sonnet = None
    if require_sonnet or "Current week (Sonnet only)" in positions:
        sonnet = parse_window("Current week (Sonnet only)")
    return session, week, sonnet


def has_complete_primary_usage(screen_text: str) -> bool:
    try:
        session, week, _ = parse_usage_screen(screen_text, require_sonnet=False)
    except ValueError:
        return False

    return session.reset_text is not None and week.reset_text is not None


def parse_clock(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)", value.strip().lower())
    if not match:
        raise ValueError(f"Unsupported clock format: {value}")

    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    suffix = match.group(3)

    if suffix == "pm" and hour != 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0

    return hour, minute


def attach_reset_datetime(window: UsageWindow, now: datetime) -> None:
    if not window.reset_text:
        return

    tz_label_match = re.search(r"\(([^)]+)\)$", window.reset_text)
    tz_name = tz_label_match.group(1) if tz_label_match else DEFAULT_TZ

    session_match = re.fullmatch(
        r"(\d{1,2}(?::\d{2})?(?:am|pm)) \(" + re.escape(tz_name) + r"\)",
        window.reset_text,
    )
    if session_match:
        hour, minute = parse_clock(session_match.group(1))
        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        window.reset_dt = dt
        return

    week_match = re.fullmatch(
        r"([A-Z][a-z]{2} \d{1,2}) at (\d{1,2}(?::\d{2})?(?:am|pm)) \(" + re.escape(tz_name) + r"\)",
        window.reset_text,
    )
    if week_match:
        day_str = week_match.group(1)
        hour, minute = parse_clock(week_match.group(2))
        dt = datetime.strptime(f"{day_str} {now.year}", "%b %d %Y").replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
            tzinfo=now.tzinfo,
        )
        if dt <= now - timedelta(days=1):
            dt = dt.replace(year=now.year + 1)
        window.reset_dt = dt


def format_duration(delta: timedelta | None) -> str:
    if delta is None:
        return "?"

    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "now"

    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60

    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"
    return f"{hours}h {minutes}m"


def progress_bar(used_pct: int, width: int = 25) -> str:
    filled = round((used_pct / 100.0) * width)
    filled = max(0, min(width, filled))
    return "\u2588" * filled + "\u2591" * (width - filled)


def recommended_effort(session: UsageWindow, week: UsageWindow, now: datetime) -> str:
    if session.remaining_pct < 15 or week.remaining_pct < 20:
        return "low"
    if session.reset_dt is not None:
        hours = (session.reset_dt - now).total_seconds() / 3600
        if hours < 1 and session.remaining_pct > 30:
            return "high"
    if session.remaining_pct > 60:
        return "high"
    return "medium"


def run_probe(cwd: str, timeout_seconds: float, progress: bool = False) -> tuple[str, str]:
    claude_path = shutil.which("claude")
    if not claude_path:
        raise RuntimeError("Could not find `claude` in PATH")
    if not os.path.isdir(cwd):
        raise RuntimeError(f"Trusted cwd does not exist: {cwd}")

    maybe_progress(progress, f"launching Claude in {cwd}")
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)
        os.execve(claude_path, [claude_path], env)

    screen = TerminalScreen()
    set_winsize(fd)

    try:
        maybe_progress(progress, "waiting for Claude prompt")
        prompt_ok, _ = read_until(
            fd,
            screen,
            lambda text: "\u276f" in text and "accept edits" in text.lower(),
            timeout_seconds,
        )
        if not prompt_ok:
            snapshot = summarize_screen(screen.text())
            raise RuntimeError(
                "Timed out waiting for Claude prompt. "
                "Set --cwd or CLAUDE_USAGE_CWD to a trusted project directory.\n\n"
                f"Last rendered screen:\n{snapshot or '(empty)'}"
            )

        maybe_progress(progress, "requesting /usage")
        os.write(fd, b"/usage\r")
        usage_ok, _ = read_until(
            fd,
            screen,
            has_complete_primary_usage,
            timeout_seconds,
        )
        if not usage_ok:
            snapshot = summarize_screen(screen.text())
            raise RuntimeError(
                "Timed out waiting for Claude /usage data.\n\n"
                f"Last rendered screen:\n{snapshot or '(empty)'}"
            )

        screen_text = screen.text()
        maybe_progress(progress, "captured usage screen")
        try:
            os.write(fd, b"/exit\r")
            read_until(fd, screen, lambda _: False, 1.0)
        except OSError:
            pass

        return screen_text, cwd
    finally:
        cleanup_child(pid, fd)


def format_text_output(
    session: UsageWindow,
    week: UsageWindow,
    sonnet: UsageWindow,
    now: datetime,
    agent_budget: float,
    source_cwd: str,
    screen_text: str | None = None,
) -> str:
    attach_reset_datetime(session, now)
    attach_reset_datetime(week, now)
    attach_reset_datetime(sonnet, now)

    effort = recommended_effort(session, week, now)

    lines = [
        f"Claude Code Usage  ({now.strftime('%H:%M %a %b %d')})",
        "=" * 55,
        f"Session:  [{progress_bar(session.used_pct)}] {session.used_pct}% used, {session.remaining_pct}% left",
        f"          Resets in {format_duration(session.reset_dt - now if session.reset_dt else None)}",
        f"Week:     [{progress_bar(week.used_pct)}] {week.used_pct}% used, {week.remaining_pct}% left",
        f"          Resets in {format_duration(week.reset_dt - now if week.reset_dt else None)}",
        f"Sonnet:   [{progress_bar(sonnet.used_pct)}] {sonnet.used_pct}% used, {sonnet.remaining_pct}% left",
    ]

    if sonnet.reset_dt is not None:
        lines.append(
            f"          Resets in {format_duration(sonnet.reset_dt - now)}"
        )

    lines.extend(
        [
            "",
            f"Agent budget: {agent_budget:.0%} of remaining (set AGENT_BUDGET to change)",
            "-" * 55,
            f"Session available for agents: {session.remaining_pct * agent_budget:.0f}%",
        ]
    )

    if session.reset_dt is not None:
        hours = (session.reset_dt - now).total_seconds() / 3600
        if hours > 0:
            burn_rate = (session.remaining_pct * agent_budget) / hours
            lines.append(
                f"  Can spend ~{burn_rate:.0f}%/hour for next {format_duration(session.reset_dt - now)}"
            )

    lines.append(f"Week available for agents: {week.remaining_pct * agent_budget:.0f}%")
    if week.reset_dt is not None:
        hours = (week.reset_dt - now).total_seconds() / 3600
        if hours > 0:
            burn_rate = (week.remaining_pct * agent_budget) / (hours / 24)
            lines.append(
                f"  Can spend ~{burn_rate:.0f}%/day for next {format_duration(week.reset_dt - now)}"
            )

    lines.extend(
        [
            "",
            f"Recommended effort: {effort.upper()}",
            "",
            f"# session_remaining={session.remaining_pct}% week_remaining={week.remaining_pct}% "
            f"sonnet_remaining={sonnet.remaining_pct}% effort={effort} agent_budget={agent_budget}",
            f"# source=claude_slash_usage cwd={source_cwd}",
        ]
    )

    if screen_text:
        lines.extend(
            [
                "",
                "--- SCREEN ---",
                screen_text,
                "--- END SCREEN ---",
            ]
        )

    return "\n".join(lines)


def build_json_output(
    session: UsageWindow,
    week: UsageWindow,
    sonnet: UsageWindow,
    now: datetime,
    agent_budget: float,
    source_cwd: str,
) -> dict[str, object]:
    attach_reset_datetime(session, now)
    attach_reset_datetime(week, now)
    attach_reset_datetime(sonnet, now)

    effort = recommended_effort(session, week, now)

    def seconds_until(dt: datetime | None) -> int | None:
        if dt is None:
            return None
        return max(0, int((dt - now).total_seconds()))

    def serialize(window: UsageWindow) -> dict[str, object]:
        reset_seconds = seconds_until(window.reset_dt)
        return {
            "label": window.label,
            "used_pct": window.used_pct,
            "remaining_pct": window.remaining_pct,
            "reset_text": window.reset_text,
            "resets_at": int(window.reset_dt.timestamp()) if window.reset_dt else None,
            "resets_in_seconds": reset_seconds,
        }

    session_available_pct = round(session.remaining_pct * agent_budget, 1)
    week_available_pct = round(week.remaining_pct * agent_budget, 1)
    session_reset_seconds = seconds_until(session.reset_dt)
    week_reset_seconds = seconds_until(week.reset_dt)
    session_pct_per_hour = None
    week_pct_per_day = None

    if session_reset_seconds and session_reset_seconds > 0:
        session_pct_per_hour = round(session_available_pct / (session_reset_seconds / 3600), 1)
    if week_reset_seconds and week_reset_seconds > 0:
        week_pct_per_day = round(week_available_pct / (week_reset_seconds / 86400), 1)

    return {
        "source": "claude_slash_usage",
        "cwd": source_cwd,
        "captured_at": now.isoformat(),
        "session": serialize(session),
        "week": serialize(week),
        "sonnet": serialize(sonnet),
        "agent_budget": {
            "fraction": agent_budget,
            "session_available_pct": session_available_pct,
            "week_available_pct": week_available_pct,
            "session_pct_per_hour": session_pct_per_hour,
            "week_pct_per_day": week_pct_per_day,
            "primary_available_pct": round(min(session_available_pct, week_available_pct), 1),
        },
        "recommended_effort": effort,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Programmatic Claude Code usage probe")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of formatted text")
    parser.add_argument("--debug-screen", action="store_true", help="Append the rendered /usage screen to the text output")
    parser.add_argument(
        "--cwd",
        default=os.environ.get("CLAUDE_USAGE_CWD", DEFAULT_CWD),
        help="Trusted working directory to launch Claude from (default: current dir or CLAUDE_USAGE_CWD)",
    )
    parser.add_argument(
        "--tz",
        default=os.environ.get("CLAUDE_USAGE_TZ", DEFAULT_TZ),
        help="Timezone for parsing reset times (default: UTC or CLAUDE_USAGE_TZ)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("CLAUDE_USAGE_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)),
        help="Timeout in seconds for prompt and usage loading",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Emit progress messages to stderr while probing",
    )
    args = parser.parse_args()

    agent_budget = float(os.environ.get("AGENT_BUDGET", DEFAULT_AGENT_BUDGET))
    progress = args.progress or os.environ.get("CLAUDE_USAGE_PROGRESS") == "1"

    try:
        screen_text, source_cwd = run_probe(args.cwd, args.timeout, progress=progress)
        session, week, sonnet = parse_usage_screen(screen_text, tz_name=args.tz)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if sonnet is None:
        print("Error: Claude /usage screen was missing the Sonnet section", file=sys.stderr)
        return 1

    now = datetime.now(ZoneInfo(args.tz))

    if args.json:
        print(
            json.dumps(
                build_json_output(session, week, sonnet, now, agent_budget, source_cwd),
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print(
        format_text_output(
            session=session,
            week=week,
            sonnet=sonnet,
            now=now,
            agent_budget=agent_budget,
            source_cwd=source_cwd,
            screen_text=screen_text if args.debug_screen else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
