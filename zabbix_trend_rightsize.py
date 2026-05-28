#!/usr/bin/env python3
"""Export Zabbix trend metrics for right-sizing analysis.

The script talks to the Zabbix JSON-RPC API directly and writes a compact CSV
summary from hourly trend rows. It intentionally avoids pandas so it can run on
small jump hosts with only Python and requests installed.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import getpass
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Iterable

import requests


NUMERIC_VALUE_TYPES = {"0", "3"}  # 0=float, 3=numeric unsigned


DEFAULT_METRIC_RULES: list[dict[str, Any]] = [
    {
        "metric": "cpu_iowait_pct",
        "include": [r"^key:system\.cpu\.util\[.*,iowait\]$"],
        "exclude": [],
        "unit": "percent",
        "transform": "identity",
    },
    {
        "metric": "cpu_steal_pct",
        "include": [r"^key:system\.cpu\.util\[.*,steal\]$"],
        "exclude": [],
        "unit": "percent",
        "transform": "identity",
    },
    {
        "metric": "cpu_util_pct",
        "include": [
            r"^key:system\.cpu\.util$",
            r"^name:.*\b(cpu|processor).*(utilization|util|usage|busy)\b",
        ],
        "exclude": [r"key:.*\[,idle\]", r"name:\bidle\b"],
        "unit": "percent",
        "transform": "identity",
    },
    {
        "metric": "cpu_load",
        "include": [r"^key:system\.cpu\.load(\[.*\])?$"],
        "exclude": [],
        "unit": "load",
        "transform": "identity",
    },
    {
        "metric": "memory_used_pct",
        "include": [
            r"^key:vm\.memory\.utilization(\[.*\])?$",
            r"^key:vm\.memory\.size\[pused\]$",
            r"^name:.*\bmemory.*(util|usage|used).*\b%",
        ],
        "exclude": [],
        "unit": "percent",
        "transform": "identity",
    },
    {
        "metric": "memory_used_pct",
        "include": [r"^key:vm\.memory\.size\[pavailable\]$"],
        "exclude": [],
        "unit": "percent",
        "transform": "free_percent_to_used_percent",
    },
    {
        "metric": "memory_used_bytes",
        "include": [r"^key:vm\.memory\.size\[used\]$"],
        "exclude": [],
        "unit": "bytes",
        "transform": "identity",
    },
    {
        "metric": "memory_available_bytes",
        "include": [r"^key:vm\.memory\.size\[available\]$"],
        "exclude": [],
        "unit": "bytes",
        "transform": "identity",
    },
    {
        "metric": "memory_total_bytes",
        "include": [r"^key:vm\.memory\.size\[total\]$"],
        "exclude": [],
        "unit": "bytes",
        "transform": "identity",
    },
    {
        "metric": "swap_used_pct",
        "include": [r"^key:system\.swap\.size\[.*,pused\]$"],
        "exclude": [],
        "unit": "percent",
        "transform": "identity",
    },
    {
        "metric": "swap_used_pct",
        "include": [r"^key:system\.swap\.size\[.*,pfree\]$"],
        "exclude": [],
        "unit": "percent",
        "transform": "free_percent_to_used_percent",
    },
    {
        "metric": "swap_free_bytes",
        "include": [r"^key:system\.swap\.size\[.*,free\]$"],
        "exclude": [],
        "unit": "bytes",
        "transform": "identity",
    },
    {
        "metric": "swap_total_bytes",
        "include": [r"^key:system\.swap\.size\[.*,total\]$"],
        "exclude": [],
        "unit": "bytes",
        "transform": "identity",
    },
    {
        "metric": "disk_used_pct",
        "include": [
            r"^key:vfs\.fs\.size\[.*,pused\]$",
            r"^key:vfs\.fs\.dependent\.size\[.*,pused\]$",
        ],
        "exclude": [],
        "unit": "percent",
        "transform": "identity",
    },
    {
        "metric": "disk_used_pct",
        "include": [
            r"^key:vfs\.fs\.size\[.*,pfree\]$",
            r"^key:vfs\.fs\.dependent\.size\[.*,pfree\]$",
        ],
        "exclude": [],
        "unit": "percent",
        "transform": "free_percent_to_used_percent",
    },
    {
        "metric": "disk_used_bytes",
        "include": [
            r"^key:vfs\.fs\.size\[.*,used\]$",
            r"^key:vfs\.fs\.dependent\.size\[.*,used\]$",
        ],
        "exclude": [],
        "unit": "bytes",
        "transform": "identity",
    },
    {
        "metric": "disk_free_bytes",
        "include": [
            r"^key:vfs\.fs\.size\[.*,free\]$",
            r"^key:vfs\.fs\.dependent\.size\[.*,free\]$",
        ],
        "exclude": [],
        "unit": "bytes",
        "transform": "identity",
    },
    {
        "metric": "disk_total_bytes",
        "include": [
            r"^key:vfs\.fs\.size\[.*,total\]$",
            r"^key:vfs\.fs\.dependent\.size\[.*,total\]$",
        ],
        "exclude": [],
        "unit": "bytes",
        "transform": "identity",
    },
    {
        "metric": "disk_inode_used_pct",
        "include": [r"^key:vfs\.fs\.(dependent\.)?inode\[.*,pused\]$"],
        "exclude": [],
        "unit": "percent",
        "transform": "identity",
    },
    {
        "metric": "disk_inode_used_pct",
        "include": [r"^key:vfs\.fs\.(dependent\.)?inode\[.*,pfree\]$"],
        "exclude": [],
        "unit": "percent",
        "transform": "free_percent_to_used_percent",
    },
    {
        "metric": "disk_read_rate",
        "include": [r"^key:vfs\.dev\.read\.rate\[.*\]$"],
        "exclude": [],
        "unit": "reads_per_second",
        "transform": "identity",
    },
    {
        "metric": "disk_write_rate",
        "include": [r"^key:vfs\.dev\.write\.rate\[.*\]$"],
        "exclude": [],
        "unit": "writes_per_second",
        "transform": "identity",
    },
    {
        "metric": "disk_read_await_ms",
        "include": [r"^key:vfs\.dev\.read\.await\[.*\]$"],
        "exclude": [],
        "unit": "milliseconds",
        "transform": "identity",
    },
    {
        "metric": "disk_write_await_ms",
        "include": [r"^key:vfs\.dev\.write\.await\[.*\]$"],
        "exclude": [],
        "unit": "milliseconds",
        "transform": "identity",
    },
    {
        "metric": "disk_queue_size",
        "include": [r"^key:vfs\.dev\.queue_size\[.*\]$"],
        "exclude": [],
        "unit": "queue",
        "transform": "identity",
    },
    {
        "metric": "disk_util_pct",
        "include": [r"^key:vfs\.dev\.util\[.*\]$"],
        "exclude": [],
        "unit": "percent",
        "transform": "identity",
    },
    {
        "metric": "net_in",
        "include": [r"^key:net\.if\.in\[.*\]$"],
        "exclude": [r"key:.*,errors\]$", r"key:.*,dropped\]$"],
        "unit": "item_units",
        "transform": "identity",
    },
    {
        "metric": "net_out",
        "include": [r"^key:net\.if\.out\[.*\]$"],
        "exclude": [r"key:.*,errors\]$", r"key:.*,dropped\]$"],
        "unit": "item_units",
        "transform": "identity",
    },
    {
        "metric": "net_in_errors",
        "include": [r"^key:net\.if\.in\[.*,errors\]$"],
        "exclude": [],
        "unit": "errors",
        "transform": "identity",
    },
    {
        "metric": "net_out_errors",
        "include": [r"^key:net\.if\.out\[.*,errors\]$"],
        "exclude": [],
        "unit": "errors",
        "transform": "identity",
    },
    {
        "metric": "net_in_dropped",
        "include": [r"^key:net\.if\.in\[.*,dropped\]$"],
        "exclude": [],
        "unit": "drops",
        "transform": "identity",
    },
    {
        "metric": "net_out_dropped",
        "include": [r"^key:net\.if\.out\[.*,dropped\]$"],
        "exclude": [],
        "unit": "drops",
        "transform": "identity",
    },
]


@dataclass(frozen=True)
class MetricRule:
    metric: str
    include: tuple[re.Pattern[str], ...]
    exclude: tuple[re.Pattern[str], ...]
    unit: str
    transform: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MetricRule":
        return cls(
            metric=str(data["metric"]),
            include=tuple(re.compile(str(pattern), re.I | re.M) for pattern in data.get("include", [])),
            exclude=tuple(re.compile(str(pattern), re.I | re.M) for pattern in data.get("exclude", [])),
            unit=str(data.get("unit", "item_units")),
            transform=str(data.get("transform", "identity")),
        )


class ZabbixApiError(RuntimeError):
    pass


class ZabbixClient:
    def __init__(self, url: str, verify_tls: bool = True, timeout: int = 60) -> None:
        self.url = url.rstrip("/")
        if not self.url.endswith("/api_jsonrpc.php"):
            self.url = f"{self.url}/api_jsonrpc.php"
        self.verify_tls = verify_tls
        self.timeout = timeout
        self.auth: str | None = None
        self._request_id = 0
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json-rpc"})

    def call(self, method: str, params: dict[str, Any] | list[Any] | None = None) -> Any:
        self._request_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self._request_id,
        }
        if self.auth:
            payload["auth"] = self.auth

        response = self.session.post(
            self.url,
            data=json.dumps(payload),
            timeout=self.timeout,
            verify=self.verify_tls,
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            error = data["error"]
            message = error.get("data") or error.get("message") or str(error)
            raise ZabbixApiError(f"{method}: {message}")
        return data["result"]

    def login(self, username: str | None, password: str | None, token: str | None) -> None:
        if token:
            self.auth = token
            self.call("host.get", {"output": ["hostid"], "limit": 1})
            return

        if not username or password is None:
            raise ValueError("username/password or API token is required")

        try:
            self.auth = self.call(
                "user.login",
                {"username": username, "password": password},
            )
        except ZabbixApiError:
            self.auth = self.call("user.login", {"user": username, "password": password})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Zabbix trend data into a right-sizing CSV summary.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", default=os.getenv("ZABBIX_URL"), help="Zabbix URL or API endpoint")
    parser.add_argument("--user", default=os.getenv("ZABBIX_USER"), help="Zabbix username")
    parser.add_argument("--password", default=os.getenv("ZABBIX_PASSWORD"), help="Zabbix password")
    parser.add_argument("--token", default=os.getenv("ZABBIX_TOKEN"), help="Zabbix API token")
    parser.add_argument("--group", action="append", default=[], help="Host group name filter, can repeat")
    parser.add_argument("--host", action="append", default=[], help="Host name/visible-name filter, can repeat")
    parser.add_argument(
        "--host-match",
        choices=("contains", "exact"),
        default="contains",
        help="How --host filters are matched against host and visible-name",
    )
    parser.add_argument("--days", type=int, default=30, help="Lookback period when --from is omitted")
    parser.add_argument("--from", dest="time_from", help="Start time, e.g. 2026-05-01 or 2026-05-01T00:00:00")
    parser.add_argument("--to", dest="time_to", help="End time, e.g. 2026-05-25 or 2026-05-25T23:59:59")
    parser.add_argument("--output", default="zabbix_trend_rightsize_summary.csv", help="Summary CSV path")
    parser.add_argument("--raw-output", help="Optional raw hourly trend CSV path")
    parser.add_argument("--wide-output", help="Optional wide hourly CSV path for FinOps reporting")
    parser.add_argument("--rules", help="Optional JSON file with metric rules")
    parser.add_argument("--batch-size", type=int, default=100, help="Number of item IDs per trend.get call")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP timeout in seconds")
    parser.add_argument("--no-verify", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument("--include-unmatched", action="store_true", help="Export all numeric items as unmatched")
    parser.add_argument("--cpu-low-pct", type=float, default=20.0, help="CPU p95 below this is marked low")
    parser.add_argument("--cpu-high-pct", type=float, default=80.0, help="CPU p95 above this is marked high")
    parser.add_argument("--mem-low-pct", type=float, default=40.0, help="Memory-used p95 below this is marked low")
    parser.add_argument("--mem-high-pct", type=float, default=85.0, help="Memory-used p95 above this is marked high")
    parser.add_argument("--min-samples", type=int, default=24, help="Minimum hourly trend samples before low/high signals are trusted")
    return parser.parse_args()


def parse_local_time(value: str, is_end: bool = False) -> int:
    formats = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d")
    for fmt in formats:
        try:
            parsed = dt.datetime.strptime(value, fmt)
            if fmt == "%Y-%m-%d" and is_end:
                parsed = parsed.replace(hour=23, minute=59, second=59)
            return int(parsed.timestamp())
        except ValueError:
            pass
    raise ValueError(f"Invalid time format: {value}")


def resolve_timerange(args: argparse.Namespace) -> tuple[int, int]:
    now = int(time.time())
    time_to = parse_local_time(args.time_to, is_end=True) if args.time_to else now
    time_from = parse_local_time(args.time_from) if args.time_from else time_to - args.days * 86400
    if time_from >= time_to:
        raise ValueError("--from must be earlier than --to")
    return time_from, time_to


def load_rules(path: str | None) -> list[MetricRule]:
    raw_rules = DEFAULT_METRIC_RULES
    if path:
        with open(path, "r", encoding="utf-8") as handle:
            raw_rules = json.load(handle)
    return [MetricRule.from_dict(rule) for rule in raw_rules]


def target_text(item: dict[str, Any]) -> str:
    return f"key:{item.get('key_', '')}\nname:{item.get('name', '')}"


def split_key_params(key: str) -> list[str]:
    match = re.search(r"\[(.*)\]$", key)
    if not match:
        return []
    params: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in match.group(1):
        if char in {"'", '"'}:
            quote = None if quote == char else char
            continue
        if char == "," and quote is None:
            params.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    params.append("".join(current).strip())
    return params


def extract_resource(metric: str, key: str) -> str:
    params = split_key_params(key)
    if not params:
        return ""
    if metric.startswith("disk_") or metric.startswith("net_"):
        return params[0]
    return ""


def match_rule(item: dict[str, Any], rules: list[MetricRule]) -> MetricRule | None:
    text = target_text(item)
    for rule in rules:
        if rule.include and not any(pattern.search(text) for pattern in rule.include):
            continue
        if any(pattern.search(text) for pattern in rule.exclude):
            continue
        return rule
    return None


def get_group_ids(client: ZabbixClient, group_names: list[str]) -> list[str] | None:
    if not group_names:
        return None
    groups = client.call(
        "hostgroup.get",
        {
            "output": ["groupid", "name"],
            "filter": {"name": group_names},
        },
    )
    found = {group["name"]: group["groupid"] for group in groups}
    missing = sorted(set(group_names) - set(found))
    if missing:
        raise ValueError(f"Host group not found: {', '.join(missing)}")
    return list(found.values())


def get_hosts(
    client: ZabbixClient,
    group_ids: list[str] | None,
    host_filters: list[str],
    host_match: str = "contains",
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "output": ["hostid", "host", "name"],
        "selectInterfaces": ["interfaceid", "type", "main", "useip", "ip", "dns", "port"],
        "filter": {"status": "0"},
        "sortfield": "name",
    }
    if group_ids:
        params["groupids"] = group_ids

    hosts = client.call("host.get", params)
    if host_filters:
        needles = [value.lower() for value in host_filters]
        if host_match == "exact":
            hosts = [
                host
                for host in hosts
                if str(host.get("host", "")).lower() in needles or str(host.get("name", "")).lower() in needles
            ]
        else:
            hosts = [
                host
                for host in hosts
                if any(
                    needle in str(host.get("host", "")).lower() or needle in str(host.get("name", "")).lower()
                    for needle in needles
                )
            ]
    if not hosts:
        raise ValueError("No enabled hosts matched the filters")
    return hosts


def main_interface(host: dict[str, Any]) -> dict[str, Any]:
    interfaces = host.get("interfaces") or []
    for interface in interfaces:
        if str(interface.get("main")) == "1":
            return interface
    return interfaces[0] if interfaces else {}


def host_interface_fields(host: dict[str, Any]) -> dict[str, str]:
    interface = main_interface(host)
    interface_type = {"1": "agent", "2": "snmp", "3": "ipmi", "4": "jmx"}.get(
        str(interface.get("type", "")),
        str(interface.get("type", "")),
    )
    return {
        "host_ip": str(interface.get("ip", "")),
        "host_dns": str(interface.get("dns", "")),
        "host_interface_type": interface_type,
        "host_interface_port": str(interface.get("port", "")),
    }


def get_items(client: ZabbixClient, host_ids: list[str]) -> list[dict[str, Any]]:
    return client.call(
        "item.get",
        {
            "output": ["itemid", "hostid", "name", "key_", "value_type", "units", "status", "state", "lastvalue"],
            "hostids": host_ids,
            "filter": {"status": "0"},
            "sortfield": "name",
        },
    )


def chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def get_trends(
    client: ZabbixClient,
    item_ids: list[str],
    time_from: int,
    time_to: int,
    batch_size: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in chunks(item_ids, batch_size):
        result = client.call(
            "trend.get",
            {
                "output": ["itemid", "clock", "num", "value_min", "value_avg", "value_max"],
                "itemids": batch,
                "time_from": time_from,
                "time_till": time_to,
                "sortfield": ["itemid", "clock"],
                "sortorder": "ASC",
            },
        )
        rows.extend(result)
    return rows


def transform_values(rule: MetricRule, row: dict[str, Any]) -> tuple[float, float, float]:
    value_min = float(row["value_min"])
    value_avg = float(row["value_avg"])
    value_max = float(row["value_max"])

    if rule.transform == "identity":
        return value_min, value_avg, value_max
    if rule.transform == "free_percent_to_used_percent":
        return 100.0 - value_max, 100.0 - value_avg, 100.0 - value_min
    raise ValueError(f"Unsupported transform: {rule.transform}")


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    return ordered[lower] * (upper - rank) + ordered[upper] * (rank - lower)


def fmt_time(epoch: int) -> str:
    return dt.datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


def fmt_number(value: float | int | None) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    return f"{value:.6f}".rstrip("0").rstrip(".")


def bytes_to_gib(value: float | None) -> float | None:
    if value is None:
        return None
    return value / (1024**3)


def parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_capacity_by_host(items: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    capacity: dict[str, dict[str, float | None]] = {}
    disk_totals_by_host: dict[str, list[float]] = {}

    for item in items:
        host_id = item["hostid"]
        host_capacity = capacity.setdefault(
            host_id,
            {
                "vcpu": None,
                "memory_total_gb": None,
                "disk_total_gb": None,
            },
        )
        key = item.get("key_", "")
        value = parse_float(item.get("lastvalue"))
        if value is None:
            continue
        if key == "system.cpu.num":
            if value > 0:
                host_capacity["vcpu"] = value
        elif key == "vm.memory.size[total]":
            if value > 0:
                host_capacity["memory_total_gb"] = bytes_to_gib(value)
        elif re.match(r"^vfs\.fs\.(dependent\.)?size\[.*,total\]$", key):
            if value > 0:
                disk_totals_by_host.setdefault(host_id, []).append(value)

    for host_id, disk_totals in disk_totals_by_host.items():
        capacity.setdefault(
            host_id,
            {
                "vcpu": None,
                "memory_total_gb": None,
                "disk_total_gb": None,
            },
        )["disk_total_gb"] = bytes_to_gib(sum(disk_totals))

    formatted: dict[str, dict[str, str]] = {}
    for host_id, values in capacity.items():
        formatted[host_id] = {
            "vcpu": fmt_number(values.get("vcpu")),
            "memory_total_gb": fmt_number(values.get("memory_total_gb")),
            "disk_total_gb": fmt_number(values.get("disk_total_gb")),
        }
    return formatted


def classify_signal(metric: str, p95_avg: float | None, samples: int, args: argparse.Namespace) -> str:
    if p95_avg is None:
        return "no_data"
    if samples < args.min_samples:
        return "insufficient_data"
    if metric == "cpu_util_pct":
        if p95_avg < args.cpu_low_pct:
            return "cpu_low"
        if p95_avg > args.cpu_high_pct:
            return "cpu_high"
    if metric == "memory_used_pct":
        if p95_avg < args.mem_low_pct:
            return "memory_low"
        if p95_avg > args.mem_high_pct:
            return "memory_high"
    return "normal"


def summarize(
    trends_by_item: dict[str, list[dict[str, Any]]],
    items_by_id: dict[str, dict[str, Any]],
    host_by_id: dict[str, dict[str, Any]],
    rules_by_item: dict[str, MetricRule],
    capacity_by_host: dict[str, dict[str, str]],
    time_from: int,
    time_to: int,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for item_id, item in sorted(items_by_id.items(), key=lambda pair: (pair[1]["hostid"], pair[1]["name"])):
        rule = rules_by_item[item_id]
        rows = trends_by_item.get(item_id, [])
        mins: list[float] = []
        avgs: list[float] = []
        maxes: list[float] = []
        latest_clock: int | None = None
        latest_avg: float | None = None
        latest_max: float | None = None

        for row in rows:
            value_min, value_avg, value_max = transform_values(rule, row)
            mins.append(value_min)
            avgs.append(value_avg)
            maxes.append(value_max)
            clock = int(row["clock"])
            if latest_clock is None or clock > latest_clock:
                latest_clock = clock
                latest_avg = value_avg
                latest_max = value_max

        host = host_by_id[item["hostid"]]
        interface_fields = host_interface_fields(host)
        capacity_fields = capacity_by_host.get(item["hostid"], {})
        item_key = item.get("key_", "")
        p95_avg = percentile(avgs, 0.95)
        summaries.append(
            {
                "host": host.get("host", ""),
                "visible_name": host.get("name", ""),
                **interface_fields,
                "vcpu": capacity_fields.get("vcpu", ""),
                "memory_total_gb": capacity_fields.get("memory_total_gb", ""),
                "disk_total_gb": capacity_fields.get("disk_total_gb", ""),
                "metric": rule.metric,
                "resource": extract_resource(rule.metric, item_key),
                "itemid": item_id,
                "item_key": item_key,
                "item_name": item.get("name", ""),
                "units": item.get("units") or rule.unit,
                "period_from": fmt_time(time_from),
                "period_to": fmt_time(time_to),
                "samples": len(rows),
                "min_of_min": fmt_number(min(mins) if mins else None),
                "avg_of_avg": fmt_number(sum(avgs) / len(avgs) if avgs else None),
                "p50_avg": fmt_number(percentile(avgs, 0.50)),
                "p95_avg": fmt_number(p95_avg),
                "p99_avg": fmt_number(percentile(avgs, 0.99)),
                "max_of_max": fmt_number(max(maxes) if maxes else None),
                "latest_avg": fmt_number(latest_avg),
                "latest_max": fmt_number(latest_max),
                "signal": classify_signal(rule.metric, p95_avg, len(rows), args),
            }
        )
    return summaries


def write_csv(path: str, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_raw_rows(
    trends: list[dict[str, Any]],
    items_by_id: dict[str, dict[str, Any]],
    host_by_id: dict[str, dict[str, Any]],
    rules_by_item: dict[str, MetricRule],
    capacity_by_host: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    raw_rows: list[dict[str, Any]] = []
    for row in trends:
        item = items_by_id[row["itemid"]]
        host = host_by_id[item["hostid"]]
        rule = rules_by_item[row["itemid"]]
        value_min, value_avg, value_max = transform_values(rule, row)
        interface_fields = host_interface_fields(host)
        capacity_fields = capacity_by_host.get(item["hostid"], {})
        item_key = item.get("key_", "")
        raw_rows.append(
            {
                "host": host.get("host", ""),
                "visible_name": host.get("name", ""),
                **interface_fields,
                "vcpu": capacity_fields.get("vcpu", ""),
                "memory_total_gb": capacity_fields.get("memory_total_gb", ""),
                "disk_total_gb": capacity_fields.get("disk_total_gb", ""),
                "metric": rule.metric,
                "resource": extract_resource(rule.metric, item_key),
                "itemid": row["itemid"],
                "item_key": item_key,
                "item_name": item.get("name", ""),
                "clock": fmt_time(int(row["clock"])),
                "num": row["num"],
                "value_min": fmt_number(value_min),
                "value_avg": fmt_number(value_avg),
                "value_max": fmt_number(value_max),
            }
        )
    return raw_rows


def update_min_max_avg(target: dict[str, Any], prefix: str, value_min: float, value_avg: float, value_max: float) -> None:
    min_key = f"{prefix}_min"
    max_key = f"{prefix}_max"
    sum_key = f"_{prefix}_sum"
    count_key = f"_{prefix}_count"
    target[min_key] = value_min if target.get(min_key) is None else min(float(target[min_key]), value_min)
    target[max_key] = value_max if target.get(max_key) is None else max(float(target[max_key]), value_max)
    target[sum_key] = float(target.get(sum_key) or 0.0) + value_avg
    target[count_key] = int(target.get(count_key) or 0) + 1


def update_component(
    components: dict[str, dict[str, float | int]],
    metric: str,
    value_min: float,
    value_avg: float,
    value_max: float,
) -> None:
    component = components.setdefault(
        metric,
        {
            "min": value_min,
            "max": value_max,
            "sum": 0.0,
            "count": 0,
        },
    )
    component["min"] = min(float(component["min"]), value_min)
    component["max"] = max(float(component["max"]), value_max)
    component["sum"] = float(component["sum"]) + value_avg
    component["count"] = int(component["count"]) + 1


def component_values(components: dict[str, dict[str, float | int]], metric: str) -> tuple[float, float, float] | None:
    component = components.get(metric)
    if not component:
        return None
    count = int(component["count"])
    if count <= 0:
        return None
    return (
        float(component["min"]),
        float(component["sum"]) / count,
        float(component["max"]),
    )


def safe_percent(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return max(0.0, min(100.0, numerator / denominator * 100.0))


def derive_memory_used_pct(components: dict[str, dict[str, float | int]]) -> tuple[float, float, float] | None:
    total = component_values(components, "memory_total_bytes")
    if not total:
        return None

    total_min, total_avg, total_max = total
    used = component_values(components, "memory_used_bytes")
    if used:
        used_min, used_avg, used_max = used
        derived = (
            safe_percent(used_min, total_max),
            safe_percent(used_avg, total_avg),
            safe_percent(used_max, total_min),
        )
        if all(value is not None for value in derived):
            return derived  # type: ignore[return-value]

    available = component_values(components, "memory_available_bytes")
    if available:
        available_min, available_avg, available_max = available
        available_max_pct = safe_percent(available_max, total_min)
        available_avg_pct = safe_percent(available_avg, total_avg)
        available_min_pct = safe_percent(available_min, total_max)
        derived = (
            None if available_max_pct is None else 100.0 - available_max_pct,
            None if available_avg_pct is None else 100.0 - available_avg_pct,
            None if available_min_pct is None else 100.0 - available_min_pct,
        )
        if all(value is not None for value in derived):
            return derived  # type: ignore[return-value]

    return None


def build_wide_rows(
    trends: list[dict[str, Any]],
    items_by_id: dict[str, dict[str, Any]],
    host_by_id: dict[str, dict[str, Any]],
    rules_by_item: dict[str, MetricRule],
    capacity_by_host: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows_by_host_clock: dict[tuple[str, int], dict[str, Any]] = {}
    metric_prefix = {
        "cpu_util_pct": "cpu",
        "memory_used_pct": "memory",
        "disk_used_pct": "disk",
    }

    for trend in trends:
        item = items_by_id[trend["itemid"]]
        host = host_by_id[item["hostid"]]
        rule = rules_by_item[trend["itemid"]]
        clock = int(trend["clock"])
        key = (item["hostid"], clock)
        interface_fields = host_interface_fields(host)
        capacity_fields = capacity_by_host.get(item["hostid"], {})
        target = rows_by_host_clock.setdefault(
            key,
            {
                "vm_name": host.get("name") or host.get("host", ""),
                "ip": interface_fields.get("host_ip", ""),
                "vcpu": capacity_fields.get("vcpu", ""),
                "memory_total_gb": capacity_fields.get("memory_total_gb", ""),
                "disk_total_gb": capacity_fields.get("disk_total_gb", ""),
                "timestamp": fmt_time(clock),
                "cpu_min": None,
                "cpu_max": None,
                "cpu_avg": None,
                "memory_min": None,
                "memory_max": None,
                "memory_avg": None,
                "disk_min": None,
                "disk_max": None,
                "disk_avg": None,
                "network_in": None,
                "network_out": None,
                "_memory_components": {},
            },
        )
        value_min, value_avg, value_max = transform_values(rule, trend)
        if rule.metric in metric_prefix:
            update_min_max_avg(target, metric_prefix[rule.metric], value_min, value_avg, value_max)
        elif rule.metric in {"memory_used_bytes", "memory_available_bytes", "memory_total_bytes"}:
            update_component(target["_memory_components"], rule.metric, value_min, value_avg, value_max)
        elif rule.metric == "net_in":
            target["network_in"] = float(target.get("network_in") or 0.0) + value_avg
        elif rule.metric == "net_out":
            target["network_out"] = float(target.get("network_out") or 0.0) + value_avg

    wide_rows: list[dict[str, Any]] = []
    for row in rows_by_host_clock.values():
        if not int(row.get("_memory_count") or 0):
            derived_memory = derive_memory_used_pct(row.get("_memory_components") or {})
            if derived_memory:
                update_min_max_avg(row, "memory", *derived_memory)
        row.pop("_memory_components", None)
        for prefix in ("cpu", "memory", "disk"):
            count = int(row.pop(f"_{prefix}_count", 0) or 0)
            total = float(row.pop(f"_{prefix}_sum", 0.0) or 0.0)
            row[f"{prefix}_avg"] = total / count if count else None
        wide_rows.append(
            {
                "vm_name": row["vm_name"],
                "ip": row["ip"],
                "vcpu": row["vcpu"],
                "memory_total_gb": row["memory_total_gb"],
                "disk_total_gb": row["disk_total_gb"],
                "timestamp": row["timestamp"],
                "cpu_min": fmt_number(row["cpu_min"]),
                "cpu_max": fmt_number(row["cpu_max"]),
                "cpu_avg": fmt_number(row["cpu_avg"]),
                "memory_min": fmt_number(row["memory_min"]),
                "memory_max": fmt_number(row["memory_max"]),
                "memory_avg": fmt_number(row["memory_avg"]),
                "disk_min": fmt_number(row["disk_min"]),
                "disk_max": fmt_number(row["disk_max"]),
                "disk_avg": fmt_number(row["disk_avg"]),
                "network_in": fmt_number(row["network_in"]),
                "network_out": fmt_number(row["network_out"]),
            }
        )
    wide_rows.sort(key=lambda row: (row["ip"] or row["vm_name"], row["timestamp"]))
    return wide_rows


def main() -> int:
    args = parse_args()
    if not args.url:
        print("--url or ZABBIX_URL is required", file=sys.stderr)
        return 2

    password = args.password
    if not args.token and args.user and password is None:
        password = getpass.getpass("Zabbix password: ")

    try:
        time_from, time_to = resolve_timerange(args)
        rules = load_rules(args.rules)
        client = ZabbixClient(args.url, verify_tls=not args.no_verify, timeout=args.timeout)
        client.login(args.user, password, args.token)

        group_ids = get_group_ids(client, args.group)
        hosts = get_hosts(client, group_ids, args.host, args.host_match)
        host_by_id = {host["hostid"]: host for host in hosts}
        items = get_items(client, list(host_by_id))
        capacity_by_host = build_capacity_by_host(items)

        selected_items: list[dict[str, Any]] = []
        rules_by_item: dict[str, MetricRule] = {}
        unmatched_rule = MetricRule(
            metric="unmatched_numeric",
            include=tuple(),
            exclude=tuple(),
            unit="item_units",
            transform="identity",
        )

        for item in items:
            if str(item.get("value_type")) not in NUMERIC_VALUE_TYPES:
                continue
            rule = match_rule(item, rules)
            if not rule and args.include_unmatched:
                rule = unmatched_rule
            if rule:
                selected_items.append(item)
                rules_by_item[item["itemid"]] = rule

        if not selected_items:
            print("No numeric items matched the metric rules.", file=sys.stderr)
            return 1

        items_by_id = {item["itemid"]: item for item in selected_items}
        trends = get_trends(client, list(items_by_id), time_from, time_to, args.batch_size)
        trends_by_item: dict[str, list[dict[str, Any]]] = {item_id: [] for item_id in items_by_id}
        for row in trends:
            trends_by_item.setdefault(row["itemid"], []).append(row)

        summary_rows = summarize(
            trends_by_item,
            items_by_id,
            host_by_id,
            rules_by_item,
            capacity_by_host,
            time_from,
            time_to,
            args,
        )
        summary_fields = [
            "host",
            "visible_name",
            "host_ip",
            "host_dns",
            "host_interface_type",
            "host_interface_port",
            "vcpu",
            "memory_total_gb",
            "disk_total_gb",
            "metric",
            "resource",
            "itemid",
            "item_key",
            "item_name",
            "units",
            "period_from",
            "period_to",
            "samples",
            "min_of_min",
            "avg_of_avg",
            "p50_avg",
            "p95_avg",
            "p99_avg",
            "max_of_max",
            "latest_avg",
            "latest_max",
            "signal",
        ]
        write_csv(args.output, summary_rows, summary_fields)

        if args.raw_output:
            raw_rows = build_raw_rows(trends, items_by_id, host_by_id, rules_by_item, capacity_by_host)
            raw_fields = [
                "host",
                "visible_name",
                "host_ip",
                "host_dns",
                "host_interface_type",
                "host_interface_port",
                "vcpu",
                "memory_total_gb",
                "disk_total_gb",
                "metric",
                "resource",
                "itemid",
                "item_key",
                "item_name",
                "clock",
                "num",
                "value_min",
                "value_avg",
                "value_max",
            ]
            write_csv(args.raw_output, raw_rows, raw_fields)

        if args.wide_output:
            wide_rows = build_wide_rows(trends, items_by_id, host_by_id, rules_by_item, capacity_by_host)
            wide_fields = [
                "vm_name",
                "ip",
                "vcpu",
                "memory_total_gb",
                "disk_total_gb",
                "timestamp",
                "cpu_min",
                "cpu_max",
                "cpu_avg",
                "memory_min",
                "memory_max",
                "memory_avg",
                "disk_min",
                "disk_max",
                "disk_avg",
                "network_in",
                "network_out",
            ]
            write_csv(args.wide_output, wide_rows, wide_fields)

        print(
            f"Exported {len(summary_rows)} item summaries from {len(hosts)} hosts "
            f"and {len(trends)} trend rows to {args.output}"
        )
        if args.raw_output:
            print(f"Exported raw trend rows to {args.raw_output}")
        if args.wide_output:
            print(f"Exported wide hourly rows to {args.wide_output}")
        return 0
    except (requests.RequestException, ZabbixApiError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
