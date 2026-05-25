#!/usr/bin/env python3
"""Run Zabbix FinOps trend exports by month and host batch, then combine CSVs."""

from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import ipaddress
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from zabbix_trend_rightsize import ZabbixClient, get_group_ids, get_hosts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch Zabbix FinOps trend export by host chunks and monthly ranges.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", default=os.getenv("ZABBIX_URL"), help="Zabbix URL or API endpoint")
    parser.add_argument("--user", default=os.getenv("ZABBIX_USER"), help="Zabbix username")
    parser.add_argument("--password", default=os.getenv("ZABBIX_PASSWORD"), help="Zabbix password")
    parser.add_argument("--token", default=os.getenv("ZABBIX_TOKEN"), help="Zabbix API token")
    parser.add_argument("--group", action="append", default=[], help="Host group filter when reading hosts from Zabbix")
    parser.add_argument("--hosts-file", help="Optional text file with one host name per line")
    parser.add_argument("--months", type=int, default=6, help="Number of calendar months to export")
    parser.add_argument("--completed-months", action="store_true", help="Use only completed months, excluding current month")
    parser.add_argument("--host-batch-size", type=int, default=10, help="Number of hosts per export job")
    parser.add_argument("--output-dir", default="exports_finops", help="Directory for per-batch output files")
    parser.add_argument("--combined-output", default="finops_combined_summary.csv", help="Combined summary CSV path")
    parser.add_argument("--combined-wide-output", default="finops_combined_wide.csv", help="Combined wide hourly CSV path")
    parser.add_argument(
        "--exporter",
        default=str(Path(__file__).with_name("zabbix_trend_rightsize.py")),
        help="Path to zabbix_trend_rightsize.py",
    )
    parser.add_argument("--raw", action="store_true", help="Also export raw hourly trend CSV per batch")
    parser.add_argument("--no-wide", action="store_true", help="Disable wide hourly CSV output")
    parser.add_argument("--resume", action="store_true", help="Skip per-batch summary files that already exist and are non-empty")
    parser.add_argument("--dry-run", action="store_true", help="Print planned jobs without running exporter")
    parser.add_argument("--timeout", type=int, default=120, help="HTTP timeout passed to exporter")
    parser.add_argument("--item-batch-size", type=int, default=100, help="Item ID batch size passed to exporter")
    parser.add_argument("--min-samples", type=int, default=500, help="Minimum hourly samples before low/high signals are trusted")
    parser.add_argument("--no-verify", action="store_true", help="Disable TLS certificate verification")
    return parser.parse_args()


def add_months(value: dt.date, months: int) -> dt.date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def month_ranges(months: int, completed_months: bool) -> list[tuple[str, str, str]]:
    today = dt.date.today()
    anchor_month = dt.date(today.year, today.month, 1)
    if completed_months:
        anchor_month = add_months(anchor_month, -1)

    ranges: list[tuple[str, str, str]] = []
    first_month = add_months(anchor_month, -(months - 1))
    for offset in range(months):
        start = add_months(first_month, offset)
        last_day = calendar.monthrange(start.year, start.month)[1]
        end = dt.date(start.year, start.month, last_day)
        if not completed_months and start.year == today.year and start.month == today.month:
            end = today
        ranges.append((start.strftime("%Y-%m"), start.isoformat(), end.isoformat()))
    return ranges


def read_hosts_file(path: str) -> list[str]:
    hosts: list[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            hosts.append(value)
    return dedupe_keep_order(hosts)


def fetch_hosts_from_zabbix(args: argparse.Namespace) -> list[str]:
    if not args.url:
        raise ValueError("--url or ZABBIX_URL is required when --hosts-file is not used")
    client = ZabbixClient(args.url, verify_tls=not args.no_verify, timeout=args.timeout)
    client.login(args.user, args.password, args.token)
    group_ids = get_group_ids(client, args.group)
    hosts = get_hosts(client, group_ids, [], "contains")
    return [host["host"] for host in hosts]


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def run_job(
    args: argparse.Namespace,
    hosts: list[str],
    month_label: str,
    start: str,
    end: str,
    batch_index: int,
    env: dict[str, str],
) -> tuple[Path, Path | None]:
    month_dir = Path(args.output_dir) / month_label
    month_dir.mkdir(parents=True, exist_ok=True)
    summary_path = month_dir / f"rightsize_{month_label}_batch{batch_index:03d}.csv"
    raw_path = month_dir / f"rightsize_{month_label}_batch{batch_index:03d}_hourly.csv"
    wide_path = None if args.no_wide else month_dir / f"rightsize_{month_label}_batch{batch_index:03d}_wide.csv"

    if (
        args.resume
        and summary_path.exists()
        and summary_path.stat().st_size > 0
        and (wide_path is None or (wide_path.exists() and wide_path.stat().st_size > 0))
    ):
        print(f"skip existing {summary_path}", flush=True)
        return summary_path, wide_path

    command = [
        sys.executable,
        str(Path(args.exporter)),
        "--from",
        start,
        "--to",
        end,
        "--output",
        str(summary_path),
        "--host-match",
        "exact",
        "--timeout",
        str(args.timeout),
        "--batch-size",
        str(args.item_batch_size),
        "--min-samples",
        str(args.min_samples),
    ]
    if args.no_verify:
        command.append("--no-verify")
    if args.raw:
        command.extend(["--raw-output", str(raw_path)])
    if wide_path is not None:
        command.extend(["--wide-output", str(wide_path)])
    for host in hosts:
        command.extend(["--host", host])

    if args.dry_run:
        host_label = ", ".join(hosts)
        print(f"dry-run {month_label} batch{batch_index:03d}: {start}..{end} hosts=[{host_label}]", flush=True)
        return summary_path, wide_path

    print(f"run {month_label} batch{batch_index:03d}: {len(hosts)} hosts -> {summary_path}", flush=True)
    subprocess.run(command, check=True, env=env)
    return summary_path, wide_path


def sortable_host_address(row: dict[str, Any]) -> str:
    ip = row.get("ip") or row.get("host_ip") or ""
    if ip:
        try:
            return f"0:{int(ipaddress.ip_address(ip)):039d}"
        except ValueError:
            return f"1:{ip.lower()}"
    fallback = row.get("host_dns") or row.get("vm_name") or row.get("host") or ""
    return f"2:{fallback.lower()}"


def sort_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        sortable_host_address(row),
        row.get("timestamp") or row.get("period_from", ""),
        row.get("period_to", ""),
        row.get("vm_name") or row.get("host", ""),
        row.get("metric", ""),
    )


def combine_csv(paths: list[Path], output_path: str) -> int:
    rows: list[dict[str, Any]] = []
    fieldnames: list[str] = []
    seen_fields: set[str] = set()

    for path in paths:
        if not path.exists() or path.stat().st_size == 0:
            continue
        with open(path, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                continue
            for field in reader.fieldnames:
                if field not in seen_fields:
                    seen_fields.add(field)
                    fieldnames.append(field)
            rows.extend(reader)

    rows.sort(key=sort_key)
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)


def build_env(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    if args.url:
        env["ZABBIX_URL"] = args.url
    if args.user:
        env["ZABBIX_USER"] = args.user
    if args.password:
        env["ZABBIX_PASSWORD"] = args.password
    if args.token:
        env["ZABBIX_TOKEN"] = args.token
    return env


def main() -> int:
    args = parse_args()
    if args.months < 1:
        print("ERROR: --months must be >= 1", file=sys.stderr)
        return 2
    if args.host_batch_size < 1:
        print("ERROR: --host-batch-size must be >= 1", file=sys.stderr)
        return 2

    try:
        if not args.url:
            print("ERROR: --url or ZABBIX_URL is required", file=sys.stderr)
            return 2
        hosts = read_hosts_file(args.hosts_file) if args.hosts_file else fetch_hosts_from_zabbix(args)
        if not hosts:
            print("ERROR: no hosts found", file=sys.stderr)
            return 1

        host_batches = chunks(hosts, args.host_batch_size)
        ranges = month_ranges(args.months, args.completed_months)
        env = build_env(args)
        summary_paths: list[Path] = []
        wide_paths: list[Path] = []

        print(
            f"hosts={len(hosts)} host_batches={len(host_batches)} months={len(ranges)} "
            f"jobs={len(host_batches) * len(ranges)}",
            flush=True,
        )
        for month_label, start, end in ranges:
            for index, host_batch in enumerate(host_batches, start=1):
                summary_path, wide_path = run_job(args, host_batch, month_label, start, end, index, env)
                summary_paths.append(summary_path)
                if wide_path is not None:
                    wide_paths.append(wide_path)

        if args.dry_run:
            print("dry-run only; no combined CSV written", flush=True)
            return 0

        combined_rows = combine_csv(summary_paths, args.combined_output)
        print(f"combined {combined_rows} rows -> {args.combined_output}", flush=True)
        if not args.no_wide:
            combined_wide_rows = combine_csv(wide_paths, args.combined_wide_output)
            print(f"combined {combined_wide_rows} wide rows -> {args.combined_wide_output}", flush=True)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
