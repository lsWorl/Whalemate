# 仅操作模拟安装启动的卸载窗口。通过 Windows UI Automation 点击实际复选框与按钮。
param([Parameter(Mandatory)][string]$Install, [ValidateSet('normal','locked','running','desktop','trash')][string]$Scenario)
$ErrorActionPreference = 'Stop'
$base = [IO.Path]::GetFullPath($env:WHALEMATE_UNINSTALL_TEST_ROOT)
$expectedInstall = if ($Scenario -eq 'desktop') { Join-Path $base 'profile\Desktop' } else { Join-Path $base 'Whalemate' }
if ((Split-Path $base -Leaf) -notmatch '^whalemate-test-[0-9a-f]{32}$' -or
    (Split-Path $base -Parent) -ne [IO.Path]::GetTempPath().TrimEnd('\') -or
    [IO.Path]::GetFullPath($Install) -ne $expectedInstall) { throw '缺少隔离目录' }
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

$starter = Start-Process -FilePath (Join-Path $Install 'UninstallWhalemate.exe') -WorkingDirectory $base -PassThru
if (-not $starter.WaitForExit(60000) -or $starter.ExitCode -ne 0) { throw '卸载程序启动失败' }
$window = Wait-Until {
    foreach ($p in Get-CimInstance Win32_Process -Filter "Name='UninstallWhalemate.exe'") {
        if ($p.CommandLine -and $p.CommandLine.Contains('--origin') -and $p.CommandLine.Contains($Install)) {
            $handle = (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue).MainWindowHandle
            if ($handle -and $handle -ne [IntPtr]::Zero) { return $ae::FromHandle($handle) }
        }
    }
} '等待本次安装的卸载窗口'
$temporaryLauncher = (Get-Process -Id $window.Current.ProcessId).Path
try {
    Wait-Until { Status-Matches '请核对' } '扫描清单' | Out-Null
    if ((By-Name '卸载').Current.IsEnabled) { throw '未勾选确认仍可卸载' }
    $gallery = By-Name '同时删除我的表情图库（里面可能有你自己收集的图片）'
    if ($gallery.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern).Current.ToggleState -ne 'Off') {
        throw '外部图库没有默认保留'
    }
    $trash = By-Name '同时清空办事回收区和旧版快照（里面是她替你删除或修改的文件，删了就找不回）'
    $toggle = $trash.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern)
    if ($toggle.Current.ToggleState -ne 'Off') { throw '回收区没有默认保留' }
    if ($Scenario -eq 'trash') {
        $toggle.Toggle()
        if ($toggle.Current.ToggleState -ne 'On') { throw '回收区开关未选中' }
    }
    if ($Scenario -eq 'running') {
        # 启动同一份打包程序的真实自检，使用临时数据库，结束后自然退出。
        $pet = Start-Process -FilePath (Join-Path $Install 'AICompanion.exe') -ArgumentList '--pet-smoke-test' -WorkingDirectory $base -PassThru
        Click-Confirm
        Wait-Until { Status-Matches '鲸伴还在运行' } '运行中拒绝卸载' | Out-Null
        if (-not (Test-Path -LiteralPath (Join-Path $base 'my-memory\aicompanion.sqlite3'))) { throw '运行中已发生删除' }
        if (-not $pet.WaitForExit(120000)) { throw '临时鲸伴没有正常退出' }
        (By-Name '重新扫描').GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
        Wait-Until { Status-Matches '请核对' } '退出后重新核对清单' | Out-Null
        Write-Output '鲸伴运行中拒绝卸载，退出后重新扫描并确认'
    }
    Click-Confirm
    $status = Wait-Until { Status-Matches '卸载结束' } '等待逐项删除完成'
    Write-Output $status
    if ($status -notmatch '失败 0 项') {
        $pattern = New-Object Windows.Automation.PropertyCondition($ae::IsTextPatternAvailableProperty,$true)
        foreach ($text in $window.FindAll($subtree,$pattern)) {
            $detail = $text.GetCurrentPattern([Windows.Automation.TextPattern]::Pattern).DocumentRange.GetText(-1)
            $detail -split "[\r\n]+" | Where-Object { $_ -like "失败`t*" } | Write-Output
        }
    }
    if ($Scenario -ne 'locked' -and $status -notmatch '失败 0 项') { throw $status }
    if ($Scenario -eq 'locked' -and $status -notmatch '失败 [1-9]\d* 项') { throw '未报告锁定文件' }
} finally {
    $close = By-Name '关闭'
    if ($close) { $close.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke() }
    Wait-Until { -not (Test-Path -LiteralPath $temporaryLauncher) } '临时卸载助手已清理' | Out-Null
}
