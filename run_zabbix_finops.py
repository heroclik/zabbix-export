#!/usr/bin/env python3
"""Small config-file runner for Zabbix FinOps exports."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from finops_html_report import generate_html_report


DEFAULT_CONFIG = "zabbix_finops_config.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Zabbix FinOps export from a JSON config file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config JSON path")
    parser.add_argument("--mode", choices=("monthly", "single"), default="monthly", help="monthly=batch export, single=one range export")
    parser.add_argument("--month", action="append", default=[], help="Specific calendar month to export in monthly mode, YYYY-MM. Can repeat")
    parser.add_argument("--timestamp-output-dir", action="store_true", help="Append YYYYMMDD_HHMMSS to output directory for this run")
    parser.add_argument("--no-report", action="store_true", help="Disable HTML report generation")
    parser.add_argument("--dry-run", action="store_true", help="Show planned monthly jobs without exporting")
    return parser.parse_args()


def load_config(path: str) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        example = Path(__file__).with_name("zabbix_finops_config.example.json")
        raise FileNotFoundError(f"{path} not found. Copy {example.name} to {path} and edit it first.")
    with open(config_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def config_value(config: dict[str, Any], key: str, default: Any = None) -> Any:
    value = config.get(key, default)
    return default if value is None else value


def build_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    mappings = {
        "zabbix_url": "ZABBIX_URL",
        "zabbix_user": "ZABBIX_USER",
        "zabbix_password": "ZABBIX_PASSWORD",
        "zabbix_token": "ZABBIX_TOKEN",
    }
    for config_key, env_key in mappings.items():
        value = str(config.get(config_key) or "")
        if value:
            env[env_key] = value
    return env


def add_if(command: list[str], flag: str, value: Any) -> None:
    if value not in (None, ""):
        command.extend([flag, str(value)])


def config_list(config: dict[str, Any], key: str) -> list[str]:
    value = config.get(key)
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def timestamped_output_dir(output_dir: str) -> Path:
    suffix = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = Path(output_dir)
    return base.with_name(f"{base.name}_{suffix}")


def place_plain_file_under_dir(output_dir: Path, output_path: str) -> str:
    path = Path(output_path)
    if path.is_absolute() or path.parent != Path("."):
        return str(path)
    return str(output_dir / path.name)


def run_monthly(config: dict[str, Any], dry_run: bool, env: dict[str, str]) -> int:
    script = Path(__file__).with_name("run_finops_export_batches.py")
    selected_months = config_list(config, "month") + config_list(config, "months_selected")
    command = [
        sys.executable,
        str(script),
        "--host-batch-size",
        str(config_value(config, "host_batch_size", 10)),
        "--output-dir",
        str(config_value(config, "output_dir", "exports_finops")),
        "--combined-output",
        str(config_value(config, "summary_output", "finops_summary.csv")),
        "--combined-wide-output",
        str(config_value(config, "wide_output", "finops_wide.csv")),
        "--report-output",
        str(config_value(config, "report_output", "finops_report.html")),
        "--timeout",
        str(config_value(config, "timeout", 120)),
        "--min-samples",
        str(config_value(config, "min_samples", 500)),
    ]
    if selected_months:
        for month in selected_months:
            command.extend(["--month", str(month)])
    else:
        command.extend(["--months", str(config_value(config, "months", 6))])
    add_if(command, "--hosts-file", config.get("hosts_file"))
    add_if(command, "--group", config.get("host_group"))
    if config_value(config, "raw", False):
        command.append("--raw")
    if config_value(config, "resume", True):
        command.append("--resume")
    if config_value(config, "completed_months", False):
        command.append("--completed-months")
    if config_value(config, "no_verify", False):
        command.append("--no-verify")
    if config_value(config, "timestamp_output_dir", False):
        command.append("--timestamp-output-dir")
    if config_value(config, "no_report", False):
        command.append("--no-report")
    if dry_run:
        command.append("--dry-run")
    return subprocess.run(command, env=env).returncode


def run_single(config: dict[str, Any], env: dict[str, str]) -> int:
    script = Path(__file__).with_name("zabbix_trend_rightsize.py")
    summary_output = str(config_value(config, "summary_output", "rightsize.csv"))
    wide_output = str(config_value(config, "wide_output", "rightsize_wide.csv"))
    report_output = str(config_value(config, "report_output", "finops_report.html"))
    if config_value(config, "timestamp_output_dir", False):
        output_dir = timestamped_output_dir(str(config_value(config, "output_dir", "exports_finops")))
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_output = place_plain_file_under_dir(output_dir, summary_output)
        wide_output = place_plain_file_under_dir(output_dir, wide_output)
        report_output = place_plain_file_under_dir(output_dir, report_output)
    command = [
        sys.executable,
        str(script),
        "--days",
        str(config_value(config, "days", 30)),
        "--output",
        summary_output,
        "--wide-output",
        wide_output,
        "--timeout",
        str(config_value(config, "timeout", 120)),
        "--min-samples",
        str(config_value(config, "min_samples", 24)),
    ]
    add_if(command, "--group", config.get("host_group"))
    if config.get("from"):
        command.extend(["--from", str(config["from"])])
    if config.get("to"):
        command.extend(["--to", str(config["to"])])
    if config_value(config, "no_verify", False):
        command.append("--no-verify")
    result = subprocess.run(command, env=env)
    if result.returncode == 0 and not config_value(config, "no_report", False):
        report_rows = generate_html_report(summary_output, wide_output, report_output)
        print(f"generated HTML report from {report_rows} wide rows -> {report_output}", flush=True)
    return result.returncode


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    env = build_env(config)
    if not env.get("ZABBIX_URL"):
        print("ERROR: zabbix_url is required in config", file=sys.stderr)
        return 2
    if not (env.get("ZABBIX_TOKEN") or (env.get("ZABBIX_USER") and env.get("ZABBIX_PASSWORD"))):
        print("ERROR: zabbix_token or zabbix_user/zabbix_password is required in config", file=sys.stderr)
        return 2

    if args.mode == "single":
        if args.dry_run:
            print("ERROR: --dry-run is only supported with --mode monthly", file=sys.stderr)
            return 2
        if args.timestamp_output_dir:
            config = {**config, "timestamp_output_dir": True}
        if args.no_report:
            config = {**config, "no_report": True}
        return run_single(config, env)
    if args.month:
        config = {**config, "month": config_list(config, "month") + args.month}
    if args.timestamp_output_dir:
        config = {**config, "timestamp_output_dir": True}
    if args.no_report:
        config = {**config, "no_report": True}
    return run_monthly(config, args.dry_run, env)


if __name__ == "__main__":
    raise SystemExit(main())
