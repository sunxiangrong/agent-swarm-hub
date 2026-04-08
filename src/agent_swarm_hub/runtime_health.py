from __future__ import annotations
"""Provider runtime health helpers shared by operational probes.

Keep this module below the CLI router/entry layers so runtime health checks can
be reused by maintenance commands without importing native entry workflows.
"""

import os
import re
import signal
import subprocess
import time


def find_running_codex_session(*, session_id: str | None, work_dir: str | None) -> dict[str, str] | None:
    if not session_id and not work_dir:
        return None
    for process in list_running_codex_processes():
        command = process["command"]
        if session_id and f"resume {session_id}" in command:
            return process
        if work_dir and f"-C {work_dir}" in command:
            return process
    return None


def _extract_codex_resume_session_id(command: str) -> str:
    match = re.search(r"\bresume\s+([^\s]+)", command or "")
    return match.group(1).strip() if match else ""


def _extract_codex_work_dir(command: str) -> str:
    match = re.search(r"\s-C\s+([^\s]+)", command or "")
    return match.group(1).strip() if match else ""


def list_running_codex_processes() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    stdout = str(getattr(result, "stdout", "") or "")
    if not stdout:
        return []
    processes: list[dict[str, str]] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or "codex-aarch64-apple-darwin" not in line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid, command = parts
        processes.append(
            {
                "pid": pid.strip(),
                "command": command,
                "session_id": _extract_codex_resume_session_id(command),
                "work_dir": _extract_codex_work_dir(command),
            }
        )
    return processes


def _read_process_stat(pid: str, field: str) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", f"{field}="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return str(getattr(result, "stdout", "") or "").strip()


def _parse_ps_time_to_seconds(value: str) -> int:
    raw = (value or "").strip()
    if not raw:
        return 0
    day_seconds = 0
    if "-" in raw:
        day_text, raw = raw.split("-", 1)
        try:
            day_seconds = int(day_text) * 86400
        except ValueError:
            day_seconds = 0
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = [int(part) for part in parts]
            return day_seconds + hours * 3600 + minutes * 60 + seconds
        if len(parts) == 2:
            minutes, seconds = [int(part) for part in parts]
            return day_seconds + minutes * 60 + seconds
    except ValueError:
        return 0
    return 0


def codex_process_health(
    *,
    pid: str,
    cpu_threshold: float = 95.0,
    cpu_time_threshold_seconds: int = 600,
) -> dict[str, object]:
    cpu_value = _read_process_stat(pid, "%cpu")
    time_value = _read_process_stat(pid, "time")
    try:
        cpu_percent = float(cpu_value)
    except ValueError:
        cpu_percent = 0.0
    cpu_time_seconds = _parse_ps_time_to_seconds(time_value)
    return {
        "cpu_percent": cpu_percent,
        "cpu_time_seconds": cpu_time_seconds,
        "unhealthy": cpu_percent >= cpu_threshold and cpu_time_seconds >= cpu_time_threshold_seconds,
    }


def terminate_process(pid: str) -> None:
    if not pid:
        return
    try:
        os.kill(int(pid), signal.SIGTERM)
    except (OSError, ValueError):
        return
    time.sleep(0.2)
    try:
        os.kill(int(pid), 0)
    except OSError:
        return
    try:
        os.kill(int(pid), signal.SIGKILL)
    except (OSError, ValueError):
        return
