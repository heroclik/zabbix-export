# Zabbix Trend Export for Right-Sizing

This tool exports hourly Zabbix trend data through the JSON-RPC API and creates
a CSV summary for right-sizing analysis. It calculates average, p50, p95, p99,
and peak values per host item.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

Simplest flow:

```powershell
Copy-Item .\zabbix_finops_config.example.json .\zabbix_finops_config.json
# Edit zabbix_finops_config.json and put your Zabbix URL/token/group.

python .\run_zabbix_finops.py --dry-run
python .\run_zabbix_finops.py
```

For one 30-day export instead of monthly batches:

```powershell
python .\run_zabbix_finops.py --mode single
```

Using an API token:

```powershell
$env:ZABBIX_URL = "https://zabbix.example.com"
$env:ZABBIX_TOKEN = "your-api-token"

python .\zabbix_trend_rightsize.py --group "Linux servers" --days 30 --output rightsize.csv
```

Using username/password:

```powershell
python .\zabbix_trend_rightsize.py `
  --url "https://zabbix.example.com" `
  --user Admin `
  --group "Linux servers" `
  --from 2026-04-01 `
  --to 2026-04-30 `
  --output rightsize.csv `
  --raw-output rightsize_hourly.csv
```

The script prompts for the password when `--password` and `ZABBIX_PASSWORD` are
not set.

## Output Columns

- `avg_of_avg`: average of hourly trend averages
- `p95_avg`: 95th percentile of hourly trend averages, useful for right-sizing
- `max_of_max`: highest hourly trend maximum in the selected period
- `signal`: simple threshold label such as `cpu_low`, `cpu_high`,
  `memory_low`, or `memory_high`

Default thresholds:

- CPU low: p95 below 20%
- CPU high: p95 above 80%
- Memory low: p95 used below 40%
- Memory high: p95 used above 85%

Adjust them with `--cpu-low-pct`, `--cpu-high-pct`, `--mem-low-pct`, and
`--mem-high-pct`.

By default, low/high signals require at least 24 hourly trend samples. Rows with
fewer samples are marked `insufficient_data`. Adjust this with `--min-samples`.

## Metric Matching

Default rules match common Zabbix agent and agent2 item keys:

- `system.cpu.util`
- `system.cpu.util[...,iowait]`
- `system.cpu.util[...,steal]`
- `system.cpu.load`
- `vm.memory.utilization`
- `vm.memory.size[pused]`
- `vm.memory.size[pavailable]`
- `vm.memory.size[total]`
- `system.swap.size[...,free|pfree|pused|total]`
- `vfs.fs.size[...,pused]`
- `vfs.fs.size[...,pfree]`
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

The summary also includes `host_ip`, `host_dns`, interface type/port, and a
`resource` column for mount points, disks, and network interfaces.

If your templates use different keys or names, copy
`metric_rules.example.json`, edit the regex rules, then run:

```powershell
python .\zabbix_trend_rightsize.py --url "https://zabbix.example.com" --token "..." --rules .\my_rules.json
```

Use `--include-unmatched` once to inspect other numeric items that may be worth
adding to the rules.

## Batch Export for 6 Months

Use `run_finops_export_batches.py` when exporting many VMs. It can read hosts
from Zabbix or from a text file, split hosts into batches, split the period into
calendar months, run the exporter for each batch, and combine summaries.

Read hosts from Zabbix:

```powershell
$env:ZABBIX_URL = "https://zabbix.example.com"
$env:ZABBIX_TOKEN = "your-api-token"

python .\run_finops_export_batches.py `
  --months 6 `
  --host-batch-size 10 `
  --output-dir .\exports_finops `
  --combined-output .\finops_combined_summary.csv `
  --combined-wide-output .\finops_combined_wide.csv `
  --raw `
  --resume
```

Read selected hosts from `hosts.txt`:

```powershell
Copy-Item .\hosts.example.txt .\hosts.txt
# Edit hosts.txt and put one Zabbix host name per line.

python .\run_finops_export_batches.py `
  --hosts-file .\hosts.txt `
  --months 6 `
  --host-batch-size 10 `
  --combined-output .\finops_combined_summary.csv `
  --combined-wide-output .\finops_combined_wide.csv
```

Use `--dry-run` first to review the planned monthly host batches. By default,
the last 6 months include the current partial month. Add `--completed-months`
to export only completed calendar months.

The combined wide CSV has one row per VM per hourly trend timestamp:

```text
vm_name,ip,vcpu,memory_total_gb,disk_total_gb,timestamp,cpu_min,cpu_max,cpu_avg,memory_min,memory_max,memory_avg,disk_min,disk_max,disk_avg,network_in,network_out
```
