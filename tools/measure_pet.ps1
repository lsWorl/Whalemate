# 测量使用临时数据库；本地模式只有合成记忆已完成真实推理后才开始稳定期。
param([string]$LocalEmbeddingDir)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $root 'output\v2-check'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$ready = Join-Path $outDir ("perf-ready-" + [Guid]::NewGuid().ToString('N') + ".txt")
$out = Join-Path $outDir 'perf.json'
$arguments = @('tools\smoke_pet.py', '--preview', '--measure-ready', ('"' + $ready + '"'))
if ($LocalEmbeddingDir) {
  $arguments += @('--local-embedding-dir', ('"' + (Resolve-Path -LiteralPath $LocalEmbeddingDir).Path + '"'))
  $out = Join-Path $outDir 'perf-local.json'
}
$proc = Start-Process -FilePath (Join-Path $root '.venv\Scripts\python.exe') -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru
$deadline = (Get-Date).AddSeconds(55)
while (!(Test-Path -LiteralPath $ready)) {
  if ($proc.HasExited -or (Get-Date) -ge $deadline) { throw '预览或本地模型未就绪，未记录内存' }
  Start-Sleep -Milliseconds 250
}
Remove-Item -LiteralPath $ready
Start-Sleep -Seconds 15
function Snapshot {
  $processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='QtWebEngineProcess.exe'")
  $family = @($proc.Id)
  do {
    $next = @($processes | Where-Object { $_.ParentProcessId -in $family -and $_.ProcessId -notin $family })
    $family += @($next | ForEach-Object { $_.ProcessId })
  } while ($next.Count -gt 0)
  Get-Process -Id $family -ErrorAction SilentlyContinue
}
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
$proc.WaitForExit()
if ($proc.ExitCode -ne 0) { throw "预览退出码 $($proc.ExitCode)" }
exit 0
