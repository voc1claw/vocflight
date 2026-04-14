"""agent-browser wrapper: open, wait, snapshot, close, batch management."""

import subprocess
import os
import time
from typing import Optional

from .config import AGENT_BROWSER, MAX_SESSIONS


def _run(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    env = os.environ.copy()
    env["MSYS_NO_PATHCONV"] = "1"
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", f"command not found: {args[0]}"


def _ab(session: str, *cmd_args: str, timeout: int = 60) -> tuple[int, str, str]:
    """Run an agent-browser command on a session."""
    args = [AGENT_BROWSER, "--session", session] + list(cmd_args)
    return _run(args, timeout=timeout)


def cleanup() -> None:
    """Kill all stale browser sessions and orphaned Chromium processes."""
    # Close tracked sessions
    rc, out, _ = _run([AGENT_BROWSER, "session", "list"], timeout=10)
    if rc == 0 and out.strip():
        for line in out.strip().splitlines():
            session_id = line.split()[0] if line.strip() else None
            if session_id:
                _ab(session_id, "close", timeout=5)

    # Force-kill orphaned browser processes
    env = os.environ.copy()
    env["MSYS_NO_PATHCONV"] = "1"
    for proc in ["chrome.exe", "chromium.exe"]:
        subprocess.run(
            ["taskkill", "/IM", proc, "/F"],
            capture_output=True,
            env=env,
        )

    time.sleep(2)  # Let ports free up


def open_session(session: str, url: str) -> tuple[int, str, str]:
    """Open a URL in a named session."""
    return _ab(session, "open", url)


def wait_networkidle(session: str, timeout: int = 30) -> tuple[int, str, str]:
    """Wait for network to settle."""
    return _ab(session, "wait", "--load", "networkidle", timeout=timeout)


def wait_ms(session: str, ms: int) -> tuple[int, str, str]:
    """Wait fixed milliseconds."""
    return _ab(session, "wait", str(ms))


def snapshot(session: str) -> str:
    """Take an interactive snapshot and return the text."""
    rc, out, err = _ab(session, "snapshot", "-i", timeout=15)
    if rc != 0:
        return ""
    return out


def close_session(session: str) -> None:
    """Close a session."""
    _ab(session, "close", timeout=10)


def click(session: str, ref: str) -> tuple[int, str, str]:
    """Click an element by ref."""
    return _ab(session, "click", ref)


def _popen_env() -> dict:
    """Build env dict with MSYS_NO_PATHCONV for all Popen calls."""
    env = os.environ.copy()
    env["MSYS_NO_PATHCONV"] = "1"
    return env


def batch_search(urls: list[tuple[str, str]]) -> dict[str, str]:
    """
    Execute a batch of searches in parallel, respecting MAX_SESSIONS.

    Args:
        urls: list of (session_name, url) tuples

    Returns:
        dict mapping session_name to snapshot text
    """
    results = {}
    batches = [urls[i:i + MAX_SESSIONS] for i in range(0, len(urls), MAX_SESSIONS)]
    env = _popen_env()

    for batch in batches:
        # Open all in parallel
        procs = []
        for session_name, url in batch:
            args = [AGENT_BROWSER, "--session", session_name, "open", url]
            p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            procs.append((session_name, p))

        # Wait for all opens to complete
        for _, p in procs:
            p.wait(timeout=30)

        # Wait for networkidle in parallel
        wait_procs = []
        for session_name, _ in batch:
            args = [AGENT_BROWSER, "--session", session_name, "wait", "--load", "networkidle"]
            p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            wait_procs.append((session_name, p))

        for _, p in wait_procs:
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()

        # Snapshot sequentially (need to read output)
        for session_name, _ in batch:
            snap = snapshot(session_name)
            results[session_name] = snap

        # Close all in parallel
        close_procs = []
        for session_name, _ in batch:
            args = [AGENT_BROWSER, "--session", session_name, "close"]
            p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            close_procs.append(p)

        for p in close_procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()

    return results
