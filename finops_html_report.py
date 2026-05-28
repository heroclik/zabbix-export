#!/usr/bin/env python3
"""Generate an HTML monthly report from Zabbix FinOps CSV exports."""

from __future__ import annotations

import csv
import calendar
import datetime as dt
import html
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


WIDE_METRICS = (
    "cpu_avg",
    "cpu_max",
    "memory_avg",
    "memory_max",
    "disk_avg",
    "disk_max",
    "network_in",
    "network_out",
)


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    csv_path = Path(path)
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return []
    with open(csv_path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def month_from_value(value: str | None) -> str:
    if not value or len(value) < 7:
        return "unknown"
    return value[:7]


def fmt_number(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "-"
    if abs(value) >= 100:
        text = f"{value:.0f}"
    elif abs(value) >= 10:
        text = f"{value:.1f}"
    else:
        text = f"{value:.2f}"
    return f"{text}{suffix}"


def fmt_bytes_per_second(value: float | None) -> str:
    if value is None:
        return "-"
    units = ("B/s", "KB/s", "MB/s", "GB/s", "TB/s")
    current = float(value)
    unit = units[0]
    for unit in units:
        if abs(current) < 1024 or unit == units[-1]:
            break
        current /= 1024
    return fmt_number(current, f" {unit}")


def avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def max_or_none(values: list[float]) -> float | None:
    return max(values) if values else None


def expected_hours_for_month(month: str) -> int | None:
    try:
        year, month_num = (int(part) for part in month.split("-", 1))
    except ValueError:
        return None
    return calendar.monthrange(year, month_num)[1] * 24


def status_for_vm(row: dict[str, Any]) -> str:
    coverage_pct = row.get("coverage_pct")
    cpu_p95 = row.get("cpu_p95")
    cpu_p99 = row.get("cpu_p99")
    cpu_max = row.get("cpu_max")
    memory_p95 = row.get("memory_p95")
    memory_p99 = row.get("memory_p99")
    memory_max = row.get("memory_max")
    if cpu_p95 is None and memory_p95 is None:
        return "insufficient data"
    if coverage_pct is not None and coverage_pct < 50:
        return "insufficient data"
    if (
        cpu_p95 is not None
        and cpu_max is not None
        and memory_p95 is not None
        and memory_max is not None
        and cpu_p95 < 20
        and (cpu_p99 is None or cpu_p99 < 50)
        and cpu_max < 60
        and memory_p95 < 50
        and (memory_p99 is None or memory_p99 < 70)
        and memory_max < 75
    ):
        return "downsize candidate"
    if (
        (cpu_p95 is not None and cpu_p95 >= 80)
        or (cpu_p99 is not None and cpu_p99 >= 85)
        or (cpu_max is not None and cpu_max >= 90)
        or (memory_p95 is not None and memory_p95 >= 85)
        or (memory_p99 is not None and memory_p99 >= 88)
        or (memory_max is not None and memory_max >= 90)
    ):
        return "capacity risk"
    return "monitor"


def aggregate_wide_rows(rows: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    metric_values: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for row in rows:
        month = month_from_value(row.get("timestamp"))
        vm_name = row.get("vm_name") or row.get("host") or "unknown"
        key = (month, vm_name)
        target = buckets.setdefault(
            key,
            {
                "month": month,
                "vm_name": vm_name,
                "ip": row.get("ip", ""),
                "vcpu": row.get("vcpu", ""),
                "memory_total_gb": row.get("memory_total_gb", ""),
                "disk_total_gb": row.get("disk_total_gb", ""),
                "samples": 0,
            },
        )
        target["samples"] += 1
        for field in ("ip", "vcpu", "memory_total_gb", "disk_total_gb"):
            if not target.get(field) and row.get(field):
                target[field] = row[field]
        for metric in WIDE_METRICS:
            value = parse_float(row.get(metric))
            if value is not None:
                metric_values[key][metric].append(value)

    for key, target in buckets.items():
        expected_hours = expected_hours_for_month(target["month"])
        values = metric_values[key]
        target["cpu_avg"] = avg(values["cpu_avg"])
        target["cpu_p95"] = percentile(values["cpu_avg"], 0.95)
        target["cpu_p99"] = percentile(values["cpu_avg"], 0.99)
        target["cpu_max"] = max_or_none(values["cpu_max"])
        target["memory_avg"] = avg(values["memory_avg"])
        target["memory_p95"] = percentile(values["memory_avg"], 0.95)
        target["memory_p99"] = percentile(values["memory_avg"], 0.99)
        target["memory_max"] = max_or_none(values["memory_max"])
        target["disk_avg"] = avg(values["disk_avg"])
        target["disk_p95"] = percentile(values["disk_avg"], 0.95)
        target["disk_p99"] = percentile(values["disk_avg"], 0.99)
        target["disk_max"] = max_or_none(values["disk_max"])
        target["network_in"] = avg(values["network_in"])
        target["network_in_p95"] = percentile(values["network_in"], 0.95)
        target["network_in_p99"] = percentile(values["network_in"], 0.99)
        target["network_out"] = avg(values["network_out"])
        target["network_out_p95"] = percentile(values["network_out"], 0.95)
        target["network_out_p99"] = percentile(values["network_out"], 0.99)
        target["expected_hours"] = expected_hours
        target["coverage_pct"] = None if not expected_hours else min(100.0, (target["samples"] / expected_hours) * 100)
        target["status"] = status_for_vm(target)

    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for target in buckets.values():
        by_month[target["month"]].append(target)
    for month_rows in by_month.values():
        month_rows.sort(key=lambda item: (item.get("ip") or "zzzz", item.get("vm_name") or ""))
    return dict(sorted(by_month.items()))


def aggregate_signals(summary_rows: list[dict[str, str]]) -> dict[str, Counter[str]]:
    signals: dict[str, Counter[str]] = defaultdict(Counter)
    for row in summary_rows:
        month = month_from_value(row.get("period_from"))
        signal = row.get("signal") or "none"
        signals[month][signal] += 1
    return dict(signals)


def month_cards(month_rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(row["status"] for row in month_rows)
    return {
        "hosts": len(month_rows),
        "samples": sum(int(row.get("samples") or 0) for row in month_rows),
        "coverage_pct": avg([row["coverage_pct"] for row in month_rows if row.get("coverage_pct") is not None]),
        "cpu_avg": avg([row["cpu_avg"] for row in month_rows if row.get("cpu_avg") is not None]),
        "cpu_p95": avg([row["cpu_p95"] for row in month_rows if row.get("cpu_p95") is not None]),
        "cpu_max": max_or_none([row["cpu_max"] for row in month_rows if row.get("cpu_max") is not None]),
        "memory_avg": avg([row["memory_avg"] for row in month_rows if row.get("memory_avg") is not None]),
        "memory_p95": avg([row["memory_p95"] for row in month_rows if row.get("memory_p95") is not None]),
        "memory_max": max_or_none([row["memory_max"] for row in month_rows if row.get("memory_max") is not None]),
        "disk_avg": avg([row["disk_avg"] for row in month_rows if row.get("disk_avg") is not None]),
        "disk_p95": avg([row["disk_p95"] for row in month_rows if row.get("disk_p95") is not None]),
        "disk_max": max_or_none([row["disk_max"] for row in month_rows if row.get("disk_max") is not None]),
        "downsize": statuses["downsize candidate"],
        "risk": statuses["capacity risk"],
        "insufficient": statuses["insufficient data"],
    }


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def badge_class(status: str) -> str:
    if status == "downsize candidate":
        return "good"
    if status == "capacity risk":
        return "bad"
    if status == "insufficient data":
        return "warn"
    return "neutral"


def percent_bar(value: float | None, label: str) -> str:
    if value is None:
        return '<span class="empty-value">-</span>'
    width = max(0, min(100, value))
    return (
        f'<div class="metric-cell" aria-label="{e(label)} {fmt_number(value, "%")}">'
        f'<span>{fmt_number(value, "%")}</span>'
        f'<i style="--value:{width:.2f}%"></i>'
        "</div>"
    )


def render_signal_list(counter: Counter[str]) -> str:
    if not counter:
        return "<span class=\"muted\">No summary signals</span>"
    items = []
    for signal, count in counter.most_common():
        items.append(f"<span class=\"chip\">{e(signal)}: {count}</span>")
    return "\n".join(items)


def overall_stats(by_month: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    rows = [row for month_rows in by_month.values() for row in month_rows]
    statuses = Counter(row.get("status", "") for row in rows)
    return {
        "months": len(by_month),
        "hosts": len({row.get("vm_name") for row in rows}),
        "monthly_rows": len(rows),
        "samples": sum(int(row.get("samples") or 0) for row in rows),
        "cpu_avg": avg([row["cpu_avg"] for row in rows if row.get("cpu_avg") is not None]),
        "cpu_p95": avg([row["cpu_p95"] for row in rows if row.get("cpu_p95") is not None]),
        "memory_avg": avg([row["memory_avg"] for row in rows if row.get("memory_avg") is not None]),
        "memory_p95": avg([row["memory_p95"] for row in rows if row.get("memory_p95") is not None]),
        "coverage_pct": avg([row["coverage_pct"] for row in rows if row.get("coverage_pct") is not None]),
        "downsize": statuses["downsize candidate"],
        "risk": statuses["capacity risk"],
        "insufficient": statuses["insufficient data"],
    }


def render_month_nav(months: list[str]) -> str:
    return "\n".join(f'<a href="#month-{e(month)}">{e(month)}</a>' for month in months)


def render_overview(stats: dict[str, Any]) -> str:
    items = [
        ("Months", str(stats["months"]), "Calendar periods"),
        ("VMs", str(stats["hosts"]), "Unique names"),
        ("Hourly rows", str(stats["samples"]), "Wide CSV samples"),
        ("Coverage", fmt_number(stats["coverage_pct"], "%"), "Calendar hours"),
        ("CPU p95", fmt_number(stats["cpu_p95"], "%"), "Rightsize basis"),
        ("Memory p95", fmt_number(stats["memory_p95"], "%"), "Rightsize basis"),
        ("Downsize", str(stats["downsize"]), "Candidate rows"),
        ("Risk", str(stats["risk"]), "Capacity risk rows"),
    ]
    return "\n".join(
        f"""
        <div class="overview-item">
          <span>{e(label)}</span>
          <strong>{e(value)}</strong>
          <small>{e(help_text)}</small>
        </div>
"""
        for label, value, help_text in items
    )


def render_month_summary_row(month: str, rows: list[dict[str, Any]]) -> str:
    cards = month_cards(rows)
    risk_width = 0 if not cards["hosts"] else (cards["risk"] / cards["hosts"]) * 100
    candidate_width = 0 if not cards["hosts"] else (cards["downsize"] / cards["hosts"]) * 100
    return f"""
      <div class="month-summary">
        <a href="#month-{e(month)}">{e(month)}</a>
        <div>{cards["hosts"]} VMs</div>
        <div>{fmt_number(cards["coverage_pct"], "%")} coverage</div>
        <div>{fmt_number(cards["cpu_p95"], "%")} CPU p95</div>
        <div>{fmt_number(cards["memory_p95"], "%")} memory p95</div>
        <div class="stacked-bar" aria-label="risk and candidate distribution">
          <span class="risk" style="--value:{risk_width:.2f}%"></span>
          <span class="candidate" style="--offset:{risk_width:.2f}%; --value:{candidate_width:.2f}%"></span>
        </div>
      </div>
"""


def flatten_month_rows(by_month: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [row for month in sorted(by_month) for row in by_month[month]]


def sort_value(value: Any, default: float = 9999.0) -> float:
    parsed = parse_float(value)
    return default if parsed is None else parsed


def risk_score(row: dict[str, Any]) -> float:
    values = [
        sort_value(row.get("cpu_p95"), 0.0),
        sort_value(row.get("cpu_p99"), 0.0),
        sort_value(row.get("cpu_max"), 0.0),
        sort_value(row.get("memory_p95"), 0.0),
        sort_value(row.get("memory_p99"), 0.0),
        sort_value(row.get("memory_max"), 0.0),
    ]
    return max(values)


def rightsize_reason(row: dict[str, Any]) -> str:
    status = row.get("status")
    coverage = row.get("coverage_pct")
    if status == "insufficient data":
        return f"Coverage {fmt_number(coverage, '%')}; collect more trend data"
    if status == "capacity risk":
        return (
            f"CPU p95 {fmt_number(row.get('cpu_p95'), '%')}, "
            f"memory p95 {fmt_number(row.get('memory_p95'), '%')}"
        )
    if status == "downsize candidate":
        return (
            f"Low p95: CPU {fmt_number(row.get('cpu_p95'), '%')}, "
            f"memory {fmt_number(row.get('memory_p95'), '%')}"
        )
    return (
        f"Monitor: CPU p95 {fmt_number(row.get('cpu_p95'), '%')}, "
        f"memory p95 {fmt_number(row.get('memory_p95'), '%')}"
    )


def render_focus_table(rows: list[dict[str, Any]], empty_text: str) -> str:
    if not rows:
        return f'<div class="empty-panel">{e(empty_text)}</div>'
    body = []
    for row in rows:
        status = row.get("status", "")
        body.append(
            "<tr>"
            f"<td>{e(row.get('month'))}</td>"
            f"<td>{e(row.get('vm_name'))}</td>"
            f"<td>{e(row.get('ip'))}</td>"
            f"<td>{fmt_number(row.get('coverage_pct'), '%')}</td>"
            f"<td>{fmt_number(row.get('cpu_p95'), '%')}</td>"
            f"<td>{fmt_number(row.get('memory_p95'), '%')}</td>"
            f"<td><span class=\"badge {badge_class(status)}\">{e(status)}</span></td>"
            f"<td>{e(rightsize_reason(row))}</td>"
            "</tr>"
        )
    return f"""
      <div class="focus-scroll">
        <table class="focus-table">
          <thead>
            <tr><th>Month</th><th>VM</th><th>IP</th><th>Coverage</th><th>CPU p95</th><th>Mem p95</th><th>Status</th><th>Why</th></tr>
          </thead>
          <tbody>{"".join(body)}</tbody>
        </table>
      </div>
"""


def render_dashboard(by_month: dict[str, list[dict[str, Any]]], overview: dict[str, Any]) -> str:
    rows = flatten_month_rows(by_month)
    candidates = sorted(
        [row for row in rows if row.get("status") == "downsize candidate"],
        key=lambda row: (
            -sort_value(row.get("coverage_pct"), 0.0),
            sort_value(row.get("cpu_p95")),
            sort_value(row.get("memory_p95")),
            row.get("vm_name") or "",
        ),
    )[:8]
    risks = sorted(
        [row for row in rows if row.get("status") == "capacity risk"],
        key=lambda row: (-risk_score(row), row.get("vm_name") or ""),
    )[:8]
    gaps = sorted(
        [row for row in rows if sort_value(row.get("coverage_pct"), 0.0) < 90],
        key=lambda row: (sort_value(row.get("coverage_pct"), 0.0), row.get("vm_name") or ""),
    )[:8]
    monitor_count = sum(1 for row in rows if row.get("status") == "monitor")
    action_cards = [
        ("Review candidates", str(overview.get("downsize", 0)), "Low CPU and memory p95 with enough trend coverage"),
        ("Check risks first", str(overview.get("risk", 0)), "High CPU or memory pressure before any downsize"),
        ("Fix data gaps", str(overview.get("insufficient", 0)), "Coverage below decision quality or missing key metrics"),
        ("Monitor", str(monitor_count), "No immediate action from utilization thresholds"),
    ]
    cards_html = "\n".join(
        f"""
        <div class="action-card">
          <span>{e(label)}</span>
          <strong>{e(value)}</strong>
          <small>{e(description)}</small>
        </div>
"""
        for label, value, description in action_cards
    )
    return f"""
    <section class="dashboard-panel" id="right-size-dashboard">
      <div class="section-head">
        <div>
          <p class="section-kicker">Right-size dashboard</p>
          <h2>What to review first</h2>
          <p>Use this section to triage candidates before opening the monthly detail tables.</p>
        </div>
      </div>
      <div class="action-grid">
        {cards_html}
      </div>
      <div class="dashboard-grid">
        <article class="focus-panel wide">
          <div class="panel-head">
            <h3>Top downsize candidates</h3>
            <p>Validate owner, workload schedule, and cost metadata before changing size.</p>
          </div>
          {render_focus_table(candidates, "No downsize candidates found with the current thresholds.")}
        </article>
        <article class="focus-panel">
          <div class="panel-head">
            <h3>Capacity risks</h3>
            <p>Resolve these before reducing resources.</p>
          </div>
          {render_focus_table(risks, "No capacity risk rows found.")}
        </article>
        <article class="focus-panel">
          <div class="panel-head">
            <h3>Data quality gaps</h3>
            <p>Right-size decisions need stable trend coverage.</p>
          </div>
          {render_focus_table(gaps, "No low-coverage rows found.")}
        </article>
        <article class="rule-panel">
          <h3>Decision basis</h3>
          <ul>
            <li>Downsize candidate: CPU p95 below 20%, CPU max below 60%, memory p95 below 50%, and memory max below 75%.</li>
            <li>Capacity risk: CPU or memory p95/p99/max crosses high-utilization thresholds.</li>
            <li>Insufficient data: coverage below 50% or missing CPU and memory p95.</li>
            <li>Use cost, owner, environment, and application criticality as approval metadata outside Zabbix.</li>
          </ul>
        </article>
      </div>
    </section>
"""


def render_vm_table(rows: list[dict[str, Any]]) -> str:
    body = []
    for row in rows:
        status = row.get("status", "")
        body.append(
            "<tr>"
            f"<td>{e(row.get('vm_name'))}</td>"
            f"<td>{e(row.get('ip'))}</td>"
            f"<td>{e(row.get('vcpu'))}</td>"
            f"<td>{e(row.get('memory_total_gb'))}</td>"
            f"<td>{e(row.get('disk_total_gb'))}</td>"
            f"<td>{e(row.get('samples'))}</td>"
            f"<td>{fmt_number(row.get('coverage_pct'), '%')}</td>"
            f"<td>{percent_bar(row.get('cpu_avg'), 'CPU average')}</td>"
            f"<td>{percent_bar(row.get('cpu_p95'), 'CPU p95')}</td>"
            f"<td>{percent_bar(row.get('cpu_max'), 'CPU peak')}</td>"
            f"<td>{percent_bar(row.get('memory_avg'), 'Memory average')}</td>"
            f"<td>{percent_bar(row.get('memory_p95'), 'Memory p95')}</td>"
            f"<td>{percent_bar(row.get('memory_max'), 'Memory peak')}</td>"
            f"<td>{percent_bar(row.get('disk_avg'), 'Disk average')}</td>"
            f"<td>{percent_bar(row.get('disk_p95'), 'Disk p95')}</td>"
            f"<td>{percent_bar(row.get('disk_max'), 'Disk peak')}</td>"
            f"<td>{fmt_bytes_per_second(row.get('network_in'))}</td>"
            f"<td>{fmt_bytes_per_second(row.get('network_in_p95'))}</td>"
            f"<td>{fmt_bytes_per_second(row.get('network_out'))}</td>"
            f"<td>{fmt_bytes_per_second(row.get('network_out_p95'))}</td>"
            f"<td><span class=\"badge {badge_class(status)}\">{e(status)}</span></td>"
            "</tr>"
        )
    return "\n".join(body)


def render_html(
    by_month: dict[str, list[dict[str, Any]]],
    signals: dict[str, Counter[str]],
    summary_csv: str | Path,
    wide_csv: str | Path,
) -> str:
    generated_at = dt.datetime.now().isoformat(timespec="seconds")
    months = sorted(set(by_month) | set(signals))
    if not months:
        months = ["unknown"]
    overview = overall_stats(by_month)
    month_summaries = "\n".join(render_month_summary_row(month, by_month.get(month, [])) for month in months)
    dashboard = render_dashboard(by_month, overview)
    sections = []
    for month in months:
        rows = by_month.get(month, [])
        cards = month_cards(rows)
        sections.append(
            f"""
    <section id="month-{e(month)}" class="month-panel">
      <div class="section-head">
        <div>
          <p class="section-kicker">Calendar month</p>
          <h2>{e(month)}</h2>
        </div>
        <div class="signals">{render_signal_list(signals.get(month, Counter()))}</div>
      </div>
      <div class="cards">
        <div><span>VMs</span><strong>{cards["hosts"]}</strong></div>
        <div><span>Hourly rows</span><strong>{cards["samples"]}</strong></div>
        <div><span>Coverage</span><strong>{fmt_number(cards["coverage_pct"], "%")}</strong></div>
        <div><span>CPU p95</span><strong>{fmt_number(cards["cpu_p95"], "%")}</strong></div>
        <div><span>Peak CPU</span><strong>{fmt_number(cards["cpu_max"], "%")}</strong></div>
        <div><span>Memory p95</span><strong>{fmt_number(cards["memory_p95"], "%")}</strong></div>
        <div><span>Peak memory</span><strong>{fmt_number(cards["memory_max"], "%")}</strong></div>
        <div><span>Risk</span><strong>{cards["risk"]}</strong></div>
      </div>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>VM</th><th>IP</th><th>vCPU</th><th>RAM GiB</th><th>Disk GiB</th><th>Samples</th><th>Coverage</th>
              <th>CPU avg</th><th>CPU p95</th><th>CPU max</th><th>Mem avg</th><th>Mem p95</th><th>Mem max</th>
              <th>Disk avg</th><th>Disk p95</th><th>Disk max</th><th>Net in avg</th><th>Net in p95</th><th>Net out avg</th><th>Net out p95</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            {render_vm_table(rows)}
          </tbody>
        </table>
      </div>
    </section>
"""
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Zabbix FinOps Monthly Report</title>
  <style>
    :root {{
      --bg: #f6f8fb;
      --surface: #ffffff;
      --surface-soft: #f1f5f9;
      --line: #dbe3ec;
      --line-soft: #e7edf3;
      --text: #111827;
      --muted: #64748b;
      --accent: #0f766e;
      --accent-2: #2563eb;
      --accent-soft: #ccfbf1;
      --danger: #b42318;
      --warn: #a15c07;
      --mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font-family: "Geist", "Segoe UI", Arial, sans-serif; }}
    header {{ padding: 34px 28px 22px; background: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%); border-bottom: 1px solid var(--line); }}
    main {{ padding: 24px 28px 52px; }}
    .shell {{ max-width: 1520px; margin: 0 auto; }}
    .hero {{ display: grid; grid-template-columns: minmax(360px, .82fr) minmax(620px, 1.18fr); gap: 28px; align-items: start; }}
    h1 {{ margin: 0 0 10px; font-size: clamp(30px, 3vw, 44px); line-height: 1.03; letter-spacing: 0; }}
    h2 {{ margin: 0; font-size: 22px; letter-spacing: 0; }}
    p {{ margin: 4px 0; color: var(--muted); }}
    code {{ padding: 2px 5px; border-radius: 4px; background: var(--surface-soft); font-family: var(--mono); overflow-wrap: anywhere; }}
    nav {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 22px; }}
    nav a {{ color: var(--text); text-decoration: none; border: 1px solid var(--line); border-radius: 999px; padding: 7px 12px; background: #fbfdff; font-size: 13px; transition: border-color .16s ease, color .16s ease, transform .16s ease; }}
    nav a:hover {{ border-color: var(--accent); color: var(--accent); transform: translateY(-1px); }}
    .overview {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(132px, 1fr)); gap: 10px; }}
    .overview-item {{ min-height: 104px; padding: 13px 13px 12px; border: 1px solid var(--line); border-radius: 8px; background: #fbfdff; box-shadow: 0 12px 28px -24px rgba(15, 23, 42, .45); }}
    .overview-item span, .cards span {{ display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }}
    .overview-item strong, .cards strong {{ display: block; margin-top: 6px; font-family: var(--mono); font-size: clamp(18px, 1.7vw, 25px); line-height: 1.05; letter-spacing: 0; overflow-wrap: anywhere; }}
    .overview-item small {{ display: block; margin-top: 8px; color: var(--muted); font-size: 12px; line-height: 1.35; }}
    .month-strip {{ margin-top: 18px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); overflow: hidden; }}
    .month-summary {{ display: grid; grid-template-columns: 108px 90px 126px 126px 156px minmax(180px, 1fr); gap: 14px; align-items: center; padding: 11px 12px; border-bottom: 1px solid var(--line-soft); font-size: 13px; }}
    .month-summary:last-child {{ border-bottom: 0; }}
    .month-summary a {{ color: var(--accent); font-weight: 700; text-decoration: none; }}
    .stacked-bar {{ position: relative; height: 8px; border-radius: 999px; background: #e8eef5; overflow: hidden; }}
    .stacked-bar span {{ position: absolute; top: 0; bottom: 0; left: 0; width: var(--value); }}
    .stacked-bar .risk {{ background: #ef9a9a; }}
    .stacked-bar .candidate {{ left: var(--offset); background: var(--accent); opacity: .75; }}
    .dashboard-panel {{ margin: 0 0 24px; padding: 20px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 18px 40px -32px rgba(15, 23, 42, .35); }}
    .action-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 18px; }}
    .action-card {{ min-height: 112px; padding: 15px; border: 1px solid var(--line-soft); border-radius: 8px; background: #fbfdff; }}
    .action-card span {{ display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }}
    .action-card strong {{ display: block; margin-top: 7px; font-family: var(--mono); font-size: 30px; line-height: 1; }}
    .action-card small {{ display: block; margin-top: 10px; color: var(--muted); line-height: 1.35; }}
    .dashboard-grid {{ display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, .85fr); gap: 14px; align-items: start; }}
    .focus-panel, .rule-panel {{ min-width: 0; padding: 16px; border: 1px solid var(--line-soft); border-radius: 8px; background: #ffffff; }}
    .focus-panel.wide {{ grid-row: span 2; }}
    .panel-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: start; margin-bottom: 12px; }}
    .panel-head h3, .rule-panel h3 {{ margin: 0; font-size: 16px; }}
    .panel-head p {{ max-width: 44ch; font-size: 13px; }}
    .focus-scroll {{ overflow-x: auto; border: 1px solid var(--line-soft); border-radius: 8px; }}
    .focus-table {{ min-width: 820px; }}
    .focus-table th, .focus-table td {{ padding: 8px 9px; font-size: 12px; }}
    .empty-panel {{ padding: 20px; border: 1px dashed var(--line); border-radius: 8px; color: var(--muted); background: #fbfdff; }}
    .rule-panel ul {{ margin: 12px 0 0; padding-left: 18px; color: #334155; }}
    .rule-panel li {{ margin: 8px 0; line-height: 1.45; }}
    .month-panel {{ margin: 0 0 24px; padding: 20px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 18px 40px -32px rgba(15, 23, 42, .35); }}
    .section-head {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 16px; }}
    .section-kicker {{ margin: 0 0 4px; color: var(--accent); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }}
    .signals {{ display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }}
    .chip {{ padding: 4px 8px; border-radius: 999px; background: var(--surface-soft); color: #334155; font-size: 12px; font-weight: 700; }}
    .muted {{ color: var(--muted); }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(132px, 1fr)); gap: 8px; margin-bottom: 16px; }}
    .cards div {{ padding: 11px 12px; background: #fbfdff; border: 1px solid var(--line-soft); border-radius: 8px; }}
    .table-scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; background: #fff; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid var(--line-soft); text-align: left; white-space: nowrap; }}
    th {{ position: sticky; top: 0; background: #eef3f8; font-weight: 700; z-index: 1; color: #334155; }}
    td {{ font-family: var(--mono); }}
    td:first-child, th:first-child {{ font-family: "Geist", "Segoe UI", Arial, sans-serif; }}
    tbody tr:hover {{ background: #f8fafc; }}
    tr:last-child td {{ border-bottom: 0; }}
    .metric-cell {{ min-width: 84px; }}
    .metric-cell span {{ display: block; margin-bottom: 5px; }}
    .metric-cell i {{ display: block; height: 5px; width: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--accent) var(--value), #dbe4ee var(--value)); }}
    .empty-value {{ color: var(--muted); }}
    .badge {{ display: inline-block; padding: 3px 8px; border-radius: 999px; font-family: "Geist", "Segoe UI", Arial, sans-serif; font-size: 12px; font-weight: 700; }}
    .badge.good {{ background: #dcfce7; color: #166534; }}
    .badge.bad {{ background: #fee2e2; color: #991b1b; }}
    .badge.warn {{ background: #fef3c7; color: #92400e; }}
    .badge.neutral {{ background: #e0f2fe; color: #075985; }}
    @media (max-width: 1180px) {{ .hero {{ grid-template-columns: 1fr; }} .overview {{ grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }} .cards {{ grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }} .dashboard-grid {{ grid-template-columns: 1fr; }} .focus-panel.wide {{ grid-row: auto; }} }}
    @media (max-width: 980px) {{ .action-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 860px) {{ .month-summary {{ grid-template-columns: 1fr 1fr; }} .month-summary .stacked-bar {{ grid-column: 1 / -1; }} }}
    @media (max-width: 760px) {{ header, main {{ padding-left: 16px; padding-right: 16px; }} .overview {{ grid-template-columns: 1fr 1fr; }} .month-summary {{ grid-template-columns: 1fr; }} .section-head {{ display: block; }} .signals {{ justify-content: flex-start; margin-top: 10px; }} }}
    @media (max-width: 520px) {{ .overview, .action-grid {{ grid-template-columns: 1fr; }} h1 {{ font-size: 30px; }} }}
  </style>
</head>
<body>
  <header>
    <div class="shell">
      <div class="hero">
        <div>
          <p class="section-kicker">FinOps right-size dashboard</p>
          <h1>Zabbix monthly utilization report</h1>
          <p>Generated at {e(generated_at)}</p>
          <p>Source files: <code>{e(summary_csv)}</code> and <code>{e(wide_csv)}</code></p>
        </div>
        <div class="overview">
          {render_overview(overview)}
        </div>
      </div>
      <nav aria-label="Report months">
        {render_month_nav(months)}
      </nav>
      <div class="month-strip">
        {month_summaries}
      </div>
    </div>
  </header>
  <main class="shell">
    {dashboard}
    {"".join(sections)}
  </main>
</body>
</html>
"""


def generate_html_report(summary_csv: str | Path, wide_csv: str | Path, output_path: str | Path) -> int:
    summary_rows = read_csv_rows(summary_csv)
    wide_rows = read_csv_rows(wide_csv)
    by_month = aggregate_wide_rows(wide_rows)
    signals = aggregate_signals(summary_rows)
    html_text = render_html(by_month, signals, summary_csv, wide_csv)
    report_path = Path(output_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(html_text, encoding="utf-8")
    return len(wide_rows)
