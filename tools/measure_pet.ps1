# 统计本次启动的应用进程树；Cycle 在同一进程测空闲、活动及回待机，不做强制 GC/工作集裁剪。
param([switch]$Motion, [switch]$Cycle, [ValidateRange(1,5)][int]$Cycles=1, [int]$WarmupSeconds=15, [int]$ActiveSeconds=60,
      [int]$SampleSeconds=10, [int]$RecoverySeconds=20, [string]$Label='')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $root 'output\v2-check'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$mode = if ($Cycle) { 'cycle' } elseif ($Motion) { 'motion' } else { 'idle' }
$prefix = "perf-$mode" + $(if ($Label) { "-$Label" } else { '' })
$settleSeconds = $WarmupSeconds + $(if ($Motion) { $ActiveSeconds } else { 0 })
$exitSeconds = $settleSeconds + $SampleSeconds + 3
$motionStart = $WarmupSeconds
if ($Cycle) {
    $motionStart = $WarmupSeconds + $SampleSeconds + 5
    $motionDuration = $ActiveSeconds + $SampleSeconds + 5
    $motionRest = $RecoverySeconds + $SampleSeconds + 5
    $exitSeconds = $motionStart + $Cycles*($motionDuration+$motionRest) + 3
}
$argsList = @('tools\smoke_pet.py','--preview','--preview-seconds',"$exitSeconds")
$presenceFile = Join-Path $outDir "$prefix-presence.json"
$stateFile = Join-Path $outDir "$prefix-presence.state.json"
$presencePath = '"' + $presenceFile + '"'
$argsList += @('--performance-log',$presencePath)
if ($Motion -or $Cycle) { $argsList += @('--motion-preview','--motion-start',"$motionStart") }
if ($Cycle) { $argsList += @('--motion-seconds',"$motionDuration",'--motion-rounds',"$Cycles",'--motion-rest',"$motionRest") }
$launchedAt = Get-Date
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
function Wait-Phase([string]$Phase,[int]$Round=1) {
    $deadline = (Get-Date).AddSeconds($exitSeconds+15)
    do {
        if ($proc.HasExited) { throw '桌宠提前退出，无法继续三阶段测量' }
        if ((Test-Path -LiteralPath $stateFile) -and (Get-Item -LiteralPath $stateFile).LastWriteTime -gt $launchedAt) {
            try { $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json; if ($state.phase -eq $Phase -and $state.round -eq $Round) { return } } catch {}
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    throw "等待测量阶段超时：$Phase"
}
function Measure-Phase([string]$Phase, [int]$Settle) {
for ($i=0; $i -lt $Settle; $i++) { Start-Sleep -Seconds 1 }
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
$phaseResult=[ordered]@{
    measured_at=(Get-Date).ToString('s');phase=$Phase;sample_seconds=$SampleSeconds;settle_seconds=$Settle
    logical_cores=[Environment]::ProcessorCount;total_working_set_mb=[math]::Round($mem/1MB,1);total_private_mb=[math]::Round($private/1MB,1)
    cpu_percent_of_one_core=[math]::Round($cpu/$SampleSeconds*100,1);cpu_percent_of_machine=[math]::Round($cpu/$SampleSeconds*100/[Environment]::ProcessorCount,2)
    process_scope='本次启动的 Python 与 QtWebEngine 进程树（工作集逐进程相加，未去重共享页）';processes=$list
}
$phaseResult | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $outDir "$prefix-$Phase.json")
Write-Host ("{0}: WS={1} MiB, private={2} MiB" -f $Phase,$phaseResult.total_working_set_mb,$phaseResult.total_private_mb)
return $phaseResult
}
if ($Cycle) {
    $idle = Measure-Phase 'idle' $WarmupSeconds
    $rounds=@()
    for($round=1;$round -le $Cycles;$round++) {
        $suffix=if($Cycles -gt 1){"-$round"}else{''}
        Wait-Phase 'active' $round; $active = Measure-Phase "active$suffix" $ActiveSeconds
        Wait-Phase 'recovered' $round; $recovered = Measure-Phase "recovered$suffix" $RecoverySeconds
        $rounds+=[ordered]@{round=$round;active=$active;recovered=$recovered}
    }
    $result=[ordered]@{measured_at=(Get-Date).ToString('s');mode=$mode;label=$Label;units='MiB';same_process=$true;
        active_seconds_before_sample=$ActiveSeconds;recovery_seconds_before_sample=$RecoverySeconds;
        idle=$idle;active=$active;recovered=$recovered;cycles=$rounds;
        retained_working_set_mb=[math]::Round($recovered.total_working_set_mb-$idle.total_working_set_mb,1);
        retained_private_mb=[math]::Round($recovered.total_private_mb-$idle.total_private_mb,1)}
} else { $result = Measure-Phase $mode $settleSeconds }
$result | ConvertTo-Json -Depth 7 | Set-Content -Encoding UTF8 (Join-Path $outDir "$prefix.json")
$result | ConvertTo-Json -Depth 7 | Set-Content -Encoding UTF8 (Join-Path $outDir 'perf.json')
$result | ConvertTo-Json -Depth 7
Wait-Process -Id $proc.Id -ErrorAction SilentlyContinue
