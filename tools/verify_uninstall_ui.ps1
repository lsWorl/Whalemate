# 仅操作模拟安装启动的卸载窗口。通过 Windows UI Automation 点击实际复选框与按钮。
param([Parameter(Mandatory)][string]$Install, [ValidateSet('normal','locked','running')][string]$Scenario)
$ErrorActionPreference = 'Stop'
$base = [IO.Path]::GetFullPath($env:WHALEMATE_UNINSTALL_TEST_ROOT)
if ((Split-Path $base -Leaf) -notmatch '^whalemate-test-[0-9a-f]{32}$' -or
    (Split-Path $base -Parent) -ne [IO.Path]::GetTempPath().TrimEnd('\') -or
    [IO.Path]::GetFullPath($Install) -ne (Join-Path $base '鲸伴')) { throw '缺少隔离目录' }
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$ae = [Windows.Automation.AutomationElement]
$subtree = [Windows.Automation.TreeScope]::Descendants

function Wait-Until($probe, [string]$description, [int]$seconds = 75) {
    $until = [DateTime]::UtcNow.AddSeconds($seconds)
    do {
        $found = & $probe
        if ($found) { return $found }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $until)
    throw "超时：$description"
}
function By-Name([string]$name) {
    $condition = New-Object Windows.Automation.PropertyCondition($ae::NameProperty,$name)
    $window.FindFirst($subtree, $condition)
}
function Click-Confirm {
    $tick = By-Name '我知道删除后无法恢复'
    $pattern = $tick.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern)
    if ($pattern.Current.ToggleState -ne [Windows.Automation.ToggleState]::On) { $pattern.Toggle() }
    $button = By-Name '卸载'
    $button.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
}
function Status-Matches([string]$pattern) {
    $labels = New-Object Windows.Automation.PropertyCondition($ae::ControlTypeProperty,[Windows.Automation.ControlType]::Text)
    foreach ($label in $window.FindAll($subtree,$labels)) {
        if ($label.Current.Name -match $pattern) { return $label.Current.Name }
    }
}

$starter = Start-Process -FilePath (Join-Path $Install '卸载鲸伴.exe') -WorkingDirectory $base -PassThru
if (-not $starter.WaitForExit(60000) -or $starter.ExitCode -ne 0) { throw '卸载程序启动失败' }
$window = Wait-Until {
    foreach ($p in Get-CimInstance Win32_Process -Filter "Name='卸载鲸伴.exe'") {
        if ($p.CommandLine -and $p.CommandLine.Contains('--origin') -and $p.CommandLine.Contains($Install)) {
            $handle = (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue).MainWindowHandle
            if ($handle -and $handle -ne [IntPtr]::Zero) { return $ae::FromHandle($handle) }
        }
    }
} '等待本次安装的卸载窗口'
try {
    Wait-Until { Status-Matches '请核对' } '扫描清单' | Out-Null
    if ((By-Name '卸载').Current.IsEnabled) { throw '未勾选确认仍可卸载' }
    $gallery = By-Name '同时删除我的表情图库（里面可能有你自己收集的图片）'
    if ($gallery.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern).Current.ToggleState -ne 'Off') {
        throw '外部图库没有默认保留'
    }
    if ($Scenario -eq 'running') {
        # 启动同一份打包程序的真实自检，使用临时数据库，结束后自然退出。
        $pet = Start-Process -FilePath (Join-Path $Install 'AICompanion.exe') -ArgumentList '--pet-smoke-test' -WorkingDirectory $base -PassThru
        Click-Confirm
        Wait-Until { Status-Matches '鲸伴还在运行' } '运行中拒绝卸载' | Out-Null
        if (-not (Test-Path -LiteralPath (Join-Path $base 'my-memory\aicompanion.sqlite3'))) { throw '运行中已发生删除' }
        if (-not $pet.WaitForExit(120000)) { throw '临时鲸伴没有正常退出' }
        Write-Output '鲸伴运行中拒绝卸载，退出后重试'
    }
    Click-Confirm
    $status = Wait-Until { Status-Matches '卸载结束' } '等待逐项删除完成'
    Write-Output $status
    if ($Scenario -ne 'locked' -and $status -notmatch '失败 0 项') { throw $status }
    if ($Scenario -eq 'locked' -and $status -notmatch '失败 [1-9]\d* 项') { throw '未报告锁定文件' }
} finally {
    $close = By-Name '关闭'
    if ($close) { $close.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke() }
}
