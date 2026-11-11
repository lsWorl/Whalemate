# 统计本次启动的应用进程树；Motion 模式先连续拖动/走跑60秒，再采样10秒。
param([switch]$Motion, [int]$WarmupSeconds=15, [int]$ActiveSeconds=60, [int]$SampleSeconds=10)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $root 'output\v2-check'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$mode = if ($Motion) { 'motion' } else { 'idle' }
$settleSeconds = $WarmupSeconds + $(if ($Motion) { $ActiveSeconds } else { 0 })
$exitSeconds = $settleSeconds + $SampleSeconds + 3
$argsList = @('tools\smoke_pet.py','--preview','--preview-seconds',"$exitSeconds")
$presencePath = '"' + (Join-Path $outDir "perf-$mode-presence.json") + '"'
$argsList += @('--performance-log',$presencePath)
if ($Motion) { $argsList += @('--motion-preview','--motion-start',"$WarmupSeconds") }
$proc = Start-Process -FilePath (Join-Path $root '.venv\Scripts\python.exe') -ArgumentList $argsList -WorkingDirectory $root -WindowStyle Hidden -PassThru
function Snapshot {
    $all = @(Get-Process -Name python,pythonw,QtWebEngineProcess -ErrorAction SilentlyContinue)
    $owned = [System.Collections.Generic.HashSet[int]]::new();[void]$owned.Add($proc.Id)
    do {
        $changed = $false
        foreach ($item in $all) {
            try { $parentProcess = $item.Parent; if ($parentProcess -and $owned.Contains($parentProcess.Id)) { if ($owned.Add($item.Id)) { $changed=$true } } } catch {}
        }
    } while ($changed)
    @($all | Where-Object { $owned.Contains($_.Id) })
}
for ($i=0; $i -lt $settleSeconds; $i++) { Start-Sleep -Seconds 1 }
$first = @(Snapshot)
if (-not $first.Count) { throw '桌宠进程未运行，无法测量' }
$before = @{};foreach ($item in $first) { $before[$item.Id]=$item.TotalProcessorTime.TotalSeconds }
for ($i=0; $i -lt $SampleSeconds; $i++) { Start-Sleep -Seconds 1 }
$cpu=0.;$mem=0.;$private=0.;$list=@()
foreach ($item in Snapshot) {
    $used=$item.TotalProcessorTime.TotalSeconds-[double]$before[$item.Id]
    $cpu+=$used;$mem+=$item.WorkingSet64;$private+=$item.PrivateMemorySize64
    $list += [ordered]@{ name=$item.ProcessName; id=$item.Id; working_set_mb=[math]::Round($item.WorkingSet64/1MB,1);private_mb=[math]::Round($item.PrivateMemorySize64/1MB,1);cpu_seconds=[math]::Round($used,2) }
}
$result=[ordered]@{
    measured_at=(Get-Date).ToString('s');mode=$mode;sample_seconds=$SampleSeconds;warmup_seconds=$WarmupSeconds;continuous_motion_before_sample=$(if($Motion){$ActiveSeconds}else{0})
    logical_cores=[Environment]::ProcessorCount;total_working_set_mb=[math]::Round($mem/1MB,1);total_private_mb=[math]::Round($private/1MB,1)
    cpu_percent_of_one_core=[math]::Round($cpu/$SampleSeconds*100,1);cpu_percent_of_machine=[math]::Round($cpu/$SampleSeconds*100/[Environment]::ProcessorCount,2)
    process_scope='本次启动的 Python 与 QtWebEngine 进程树';processes=$list
}
$result | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $outDir "perf-$mode.json")
$result | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $outDir 'perf.json')
$result | ConvertTo-Json -Depth 5
Wait-Process -Id $proc.Id -ErrorAction SilentlyContinue
