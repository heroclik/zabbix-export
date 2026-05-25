# Zabbix FinOps Export

Export hourly Zabbix trend data for VM right-sizing and FinOps analysis.

The recommended entry point is `run_zabbix_finops.py`. It reads a JSON config
file, splits large exports into monthly host batches, and writes CSV files that
are easy to use in Excel, Power BI, or further analysis scripts.

## What It Exports

The main output is a wide CSV with one row per VM per hourly timestamp:

```text
vm_name,ip,vcpu,memory_total_gb,disk_total_gb,timestamp,cpu_min,cpu_max,cpu_avg,memory_min,memory_max,memory_avg,disk_min,disk_max,disk_avg,network_in,network_out
```

It also creates a summary CSV with per-metric statistics such as `p50_avg`,
`p95_avg`, `p99_avg`, `max_of_max`, `samples`, and `signal`.

Capacity fields are read from Zabbix items when available:

- `vcpu`: `system.cpu.num`
- `memory_total_gb`: `vm.memory.size[total]`
- `disk_total_gb`: `vfs.fs.size[...,total]` or `vfs.fs.dependent.size[...,total]`

## Files

| File | Purpose |
| --- | --- |
| `run_zabbix_finops.py` | Recommended config-driven runner |
| `run_finops_export_batches.py` | Batch runner for many VMs and monthly ranges |
| `zabbix_trend_rightsize.py` | Core Zabbix API trend exporter |
| `zabbix_finops_config.example.json` | Example config file |
| `hosts.example.txt` | Example selected-host list |
| `metric_rules.example.json` | Example custom metric matching rules |
| `usage_guide.html` | Thai HTML usage guide with examples |

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Quick Start

Create a local config file:

```powershell
Copy-Item .\zabbix_finops_config.example.json .\zabbix_finops_config.json
notepad .\zabbix_finops_config.json
```

Edit the config:

```json
{
  "zabbix_url": "https://zabbix.example.com",
  "zabbix_token": "your-api-token",
  "host_group": "Linux servers",
  "hosts_file": "",
  "months": 6,
  "days": 30,
  "host_batch_size": 10,
  "output_dir": "exports_finops",
  "summary_output": "finops_summary.csv",
  "wide_output": "finops_wide.csv",
  "raw": false,
  "resume": true,
  "completed_months": false,
  "timeout": 120,
  "min_samples": 500
}
```

Preview the planned jobs:

```powershell
python .\run_zabbix_finops.py --dry-run
```

Run the export:

```powershell
python .\run_zabbix_finops.py
```

Default outputs:

- `finops_wide.csv`: one row per VM per hourly timestamp
- `finops_summary.csv`: one row per host/item metric summary
- `exports_finops/`: per-month, per-batch intermediate files

## Export Selected Hosts

Create a host list:

```powershell
Copy-Item .\hosts.example.txt .\hosts.txt
notepad .\hosts.txt
```

Example `hosts.txt`:

```text
app-prod-01
app-prod-02
db-prod-01
```

Then set this in `zabbix_finops_config.json`:

```json
"hosts_file": "hosts.txt",
"host_group": ""
```

Run:

```powershell
python .\run_zabbix_finops.py --dry-run
python .\run_zabbix_finops.py
```

## Export One 30-Day Range

Use single mode when you do not need monthly batching:

```powershell
python .\run_zabbix_finops.py --mode single
```

`--mode single` uses `days`, `summary_output`, and `wide_output` from the config.

## Important Config Options

| Option | Meaning |
| --- | --- |
| `zabbix_url` | Zabbix web URL or API endpoint |
| `zabbix_token` | Zabbix API token |
| `host_group` | Host group to export when `hosts_file` is empty |
| `hosts_file` | Optional file with one Zabbix host name per line |
| `months` | Calendar months to export in monthly mode |
| `days` | Lookback days for single mode |
| `host_batch_size` | Number of hosts per export job |
| `completed_months` | Exclude the current partial month when true |
| `min_samples` | Minimum hourly trend samples before low/high signals are trusted |
| `raw` | Also export raw hourly metric rows per batch |
| `resume` | Skip already-created non-empty batch files |

## Manual Advanced Usage

Run the core exporter directly for one group and one range:

```powershell
$env:ZABBIX_URL = "https://zabbix.example.com"
$env:ZABBIX_TOKEN = "your-api-token"

python .\zabbix_trend_rightsize.py `
  --group "Linux servers" `
  --days 30 `
  --output rightsize_summary.csv `
  --wide-output rightsize_wide.csv
```

Run the batch runner directly:

```powershell
python .\run_finops_export_batches.py `
  --months 6 `
  --host-batch-size 10 `
  --output-dir .\exports_finops `
  --combined-output .\finops_summary.csv `
  --combined-wide-output .\finops_wide.csv `
  --resume
```

## Metric Matching

Default rules match common Zabbix agent and agent2 item keys, including:

- `system.cpu.util`
- `system.cpu.util[...,iowait]`
- `system.cpu.util[...,steal]`
- `system.cpu.load`
- `vm.memory.utilization`
- `vm.memory.size[pused]`
- `vm.memory.size[pavailable]`
- `vm.memory.size[total]`
- `system.swap.size[...,free|pfree|pused|total]`
- `vfs.fs.size[...,pused|pfree]`
- `vfs.fs.dependent.size[...,free|used|total|pused]`
- `vfs.fs.dependent.inode[...,pfree|pused]`
- `vfs.dev.read.rate[...]`
- `vfs.dev.write.rate[...]`
- `vfs.dev.read.await[...]`
- `vfs.dev.write.await[...]`
- `vfs.dev.queue_size[...]`
- `vfs.dev.util[...]`
- `net.if.in[...]`
- `net.if.out[...]`
- `net.if.in[...,errors|dropped]`
- `net.if.out[...,errors|dropped]`

If your Zabbix templates use different item keys, copy
`metric_rules.example.json`, edit the regex rules, and pass it to the core
exporter with `--rules`.

## Notes

- Do not commit `zabbix_finops_config.json`, `hosts.txt`, exported CSVs, or API
  tokens. They are ignored by `.gitignore`.
- If `ip` is empty in the output, the Zabbix host probably has no host
  interface configured. Add an interface in Zabbix or map IPs from an external
  inventory source.
- Rows with too few hourly samples are marked `insufficient_data` in the
  summary output. For six-month right-sizing, use enough retained trend data to
  cover normal workload cycles.

