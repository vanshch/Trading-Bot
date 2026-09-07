#!/usr/bin/env python3
"""Production Service Health & Log Monitor for trade-bot.

Monitors the systemd service for a specified duration, periodically checking
service health, memory, CPU, journal logs, and news fetch statuses.
At completion, outputs a structured operational summary and log extract.

Usage:
    python scripts/monitor_service.py --duration 3600
    python scripts/monitor_service.py --duration 60 --interval 10
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time


def run_cmd(cmd: list[str]) -> tuple[int, str]:
    """Run a shell command and return exit code and stripped output."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        return proc.returncode, proc.stdout.strip()
    except Exception as e:
        return -1, str(e)


def get_service_status(service_name: str = "trade-bot") -> dict:
    """Retrieve systemctl status fields."""
    code, out = run_cmd([
        "systemctl", "show", service_name,
        "--property=ActiveState,SubState,MainPID,ActiveEnterTimestamp,MemoryCurrent,CPUUsageNSec"
    ])
    props = {}
    if code == 0:
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
    return props


def get_journal_logs(service_name: str = "trade-bot", since: str | None = None) -> list[str]:
    """Fetch systemd journal logs since a specified timestamp."""
    cmd = ["journalctl", "-u", service_name, "--no-pager", "-o", "short-iso"]
    if since:
        cmd.extend(["--since", since])
    code, out = run_cmd(cmd)
    if code == 0 and out:
        return out.splitlines()
    return []


def analyze_logs(logs: list[str]) -> dict:
    """Extract metrics from journal logs."""
    stats = {
        "total_lines": len(logs),
        "forex_fetches": 0,
        "forex_429_errors": 0,
        "errors": 0,
        "warnings": 0,
        "last_forex_status": "N/A",
    }
    for line in logs:
        if "429" in line:
            stats["forex_429_errors"] += 1
        if "[ERROR]" in line or "trade-bot.news: Failed" in line:
            stats["errors"] += 1
        elif "[WARNING]" in line:
            stats["warnings"] += 1

        if "Successfully fetched" in line and "Forex Factory" in line:
            stats["forex_fetches"] += 1
            stats["last_forex_status"] = line
        elif "Forex Factory returned HTTP 429" in line:
            stats["last_forex_status"] = line

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor trade-bot systemd service.")
    parser.add_argument(
        "--duration",
        type=int,
        default=3600,
        help="Monitoring duration in seconds (default: 3600 / 1 hour)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Check interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--service",
        type=str,
        default="trade-bot",
        help="Systemd service name (default: trade-bot)",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=None,
        help="Optional path to save JSON summary report",
    )
    args = parser.parse_args()

    start_utc = datetime.datetime.now(datetime.timezone.utc)
    start_iso = start_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{start_iso}] Starting service monitor for '{args.service}'")
    print(f"  Duration: {args.duration}s ({args.duration/3600:.2f}h) | Interval: {args.interval}s")

    initial_props = get_service_status(args.service)
    print(f"  Initial State: {initial_props.get('ActiveState', 'unknown')} ({initial_props.get('SubState', 'unknown')})")
    print(f"  Main PID: {initial_props.get('MainPID', 'unknown')}")
    print("-" * 65)

    elapsed = 0
    checks_count = 0

    try:
        while elapsed < args.duration:
            time.sleep(min(args.interval, args.duration - elapsed))
            elapsed += args.interval
            checks_count += 1

            props = get_service_status(args.service)
            state = props.get("ActiveState", "unknown")
            pid = props.get("MainPID", "unknown")
            mem_raw = int(props.get("MemoryCurrent", 0) or 0)
            mem_mb = mem_raw / (1024 * 1024)

            now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S")
            print(f"[{now_str}] Check #{checks_count} (+{elapsed}s) -> State: {state} | PID: {pid} | Mem: {mem_mb:.1f} MB")

            if state != "active":
                print(f"ALERT: Service is {state}! Checking logs...")
                break

    except KeyboardInterrupt:
        print("\nMonitoring interrupted by user.")

    # Final report compilation
    end_utc = datetime.datetime.now(datetime.timezone.utc)
    logs = get_journal_logs(args.service, since=start_utc.strftime("%Y-%m-%d %H:%M:%S"))
    analysis = analyze_logs(logs)
    final_props = get_service_status(args.service)

    report = {
        "service": args.service,
        "monitored_duration_seconds": elapsed,
        "start_time_utc": start_utc.isoformat(),
        "end_time_utc": end_utc.isoformat(),
        "final_state": final_props.get("ActiveState", "unknown"),
        "final_pid": final_props.get("MainPID", "unknown"),
        "log_analysis": analysis,
        "recent_logs": logs[-30:] if logs else [],
    }

    print("\n" + "=" * 65)
    print("MONITORING REPORT")
    print("=" * 65)
    print(f"Service:             {report['service']}")
    print(f"Duration Monitored:  {report['monitored_duration_seconds']}s")
    print(f"Final State:         {report['final_state']} (PID {report['final_pid']})")
    print(f"Forex Fetches:       {analysis['forex_fetches']}")
    print(f"Forex 429 Errors:    {analysis['forex_429_errors']}")
    print(f"Warnings / Errors:   {analysis['warnings']} / {analysis['errors']}")
    print(f"Last Forex Status:   {analysis['last_forex_status']}")
    print("=" * 65)

    if args.output_json:
        try:
            with open(args.output_json, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            print(f"Report written to {args.output_json}")
        except Exception as e:
            print(f"Failed to write JSON report: {e}", file=sys.stderr)

    return 0 if analysis["errors"] == 0 and analysis["forex_429_errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
