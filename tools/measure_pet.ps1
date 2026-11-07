# 桌宠常驻性能实测：启动预览，等 15 秒稳定后，统计 10 秒内本应用全部进程（Python 与 WebEngine 子进程）的内存和 CPU。
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $root 'output\v2-check'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$out = Join-Path $outDir 'perf.json'
$start = (Get-Date).AddSeconds(-1)
$proc = Start-Process -FilePath (Join-Path $root '.venv\Scripts\python.exe') -ArgumentList 'tools\smoke_pet.py', '--preview' -WorkingDirectory $root -PassThru
Start-Sleep -Seconds 15
function Snapshot { Get-Process -Name python, pythonw, QtWebEngineProcess | Where-Object { $_.StartTime -ge $start } }
$before = @{}; foreach ($p in Snapshot) { $before[$p.Id] = $p.TotalProcessorTime.TotalSeconds }
Start-Sleep -Seconds 10
$cpu = 0.0; $mem = 0; $private = 0; $list = @()
foreach ($p in Snapshot) {
  $used = $p.TotalProcessorTime.TotalSeconds - [double]$before[$p.Id]
  $cpu += $used; $mem += $p.WorkingSet64; $private += $p.PrivateMemorySize64
  $list += [ordered]@{ name = $p.ProcessName; id = $p.Id; working_set_mb = [math]::Round($p.WorkingSet64 / 1MB, 1); private_mb = [math]::Round($p.PrivateMemorySize64 / 1MB, 1); cpu_seconds = [math]::Round($used, 2) }
}
$cores = [Environment]::ProcessorCount
$result = [ordered]@{
  measured_at = (Get-Date).ToString('s'); sample_seconds = 10; logical_cores = $cores
  total_working_set_mb = [math]::Round($mem / 1MB, 1); total_private_mb = [math]::Round($private / 1MB, 1)
  cpu_percent_of_one_core = [math]::Round($cpu / 10 * 100, 1); cpu_percent_of_machine = [math]::Round($cpu / 10 * 100 / $cores, 2)
  processes = $list
}
$result | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $out
Wait-Process -Id $proc.Id
