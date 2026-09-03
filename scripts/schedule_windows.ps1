<#
.SYNOPSIS
    Schedules Strategy Lab automated tasks in Windows Task Scheduler (R-OPS-3).

.DESCRIPTION
    Registers 4 scheduled tasks under the \TradingLab\ folder:
    1. TradingLab-Collect-Morning (Daily at 07:30)
    2. TradingLab-Collect-Evening (Daily at 19:30)
    3. TradingLab-Backup-Weekly   (Weekly on Sunday at 08:00)
    4. TradingLab-Status-Daily    (Daily at 20:00 with Toast Notification on alert)

.PARAMETER PythonExe
    Path to the Python executable in the virtual environment.

.PARAMETER StrategyLabDir
    Path to the strategy-lab root directory.

.PARAMETER Uninstall
    Removes all scheduled tasks under \TradingLab\.
#>

[CmdletBinding()]
param(
    [string]$PythonExe,
    [string]$StrategyLabDir,
    [switch]$Uninstall
)

$TaskPath = "\TradingLab\"

if ($Uninstall) {
    Write-Host "Removing all scheduled tasks in $TaskPath..." -ForegroundColor Yellow
    $tasks = Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue
    foreach ($task in $tasks) {
        Unregister-ScheduledTask -TaskName $task.TaskName -Confirm:$false
        Write-Host "Unregistered $($task.TaskName)" -ForegroundColor Green
    }
    Write-Host "Tasks removed."
    return
}

# Resolve paths
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot = Split-Path -Parent $ScriptDir

if (-not $StrategyLabDir) {
    $StrategyLabDir = Join-Path $RepoRoot "strategy-lab"
}

if (-not $PythonExe) {
    $candidates = @(
        (Join-Path $StrategyLabDir ".venv\Scripts\python.exe"),
        (Join-Path $RepoRoot ".venv\Scripts\python.exe")
    )
    foreach ($cand in $candidates) {
        if (Test-Path $cand) {
            $PythonExe = $cand
            break
        }
    }
}

if (-not (Test-Path $PythonExe)) {
    Write-Error "Python executable not found. Please provide -PythonExe path."
    exit 1
}

Write-Host "Configuring Windows Task Scheduler for Strategy Lab..." -ForegroundColor Cyan
Write-Host "Repository root : $RepoRoot"
Write-Host "Strategy Lab dir: $StrategyLabDir"
Write-Host "Python exe      : $PythonExe"
Write-Host ""

# Create status toast notification script wrapper
$StatusScriptPath = Join-Path $ScriptDir "run_status_toast.ps1"
$StatusScriptContent = @"
param([string]`$PythonPath, [string]`$WorkingDir)
Set-Location `$WorkingDir
`$output = & `$PythonPath -m strategy_lab.cli status 2>&1 | Out-String
try {
    `$json = `$output | ConvertFrom-Json
    if (`$json.status -ne "ok") {
        # Trigger Windows Toast Notification
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > `$null
        `$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
        `$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(`$template)
        `$textNodes = `$xml.GetElementsByTagName("text")
        `$textNodes.Item(0).AppendChild(`$xml.CreateTextNode("Trading Lab Alert")) > `$null
        `$textNodes.Item(1).AppendChild(`$xml.CreateTextNode("Strategy Lab status alert: " + `$json.status)) > `$null
        `$toast = [Windows.UI.Notifications.ToastNotification]::new(`$xml)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("TradingLab").Show(`$toast)
    }
} catch {
    Write-Warning "Status check parsing error: `$_"
}
"@
Set-Content -Path $StatusScriptPath -Value $StatusScriptContent -Encoding UTF8

# Task 1: Collect Morning (07:30 local)
$Task1Name = "TradingLab-Collect-Morning"
$Action1 = New-ScheduledTaskAction -Execute $PythonExe -Argument "-m strategy_lab.cli collect" -WorkingDirectory $StrategyLabDir
$Trigger1 = New-ScheduledTaskTrigger -Daily -At "07:30"
$Settings1 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $Task1Name -TaskPath $TaskPath -Action $Action1 -Trigger $Trigger1 -Settings $Settings1 -Description "Strategy Lab daily morning market data collection" -Force | Out-Null
Write-Host "Registered: $Task1Name (Daily 07:30)" -ForegroundColor Green

# Task 2: Collect Evening (19:30 local)
$Task2Name = "TradingLab-Collect-Evening"
$Action2 = New-ScheduledTaskAction -Execute $PythonExe -Argument "-m strategy_lab.cli collect" -WorkingDirectory $StrategyLabDir
$Trigger2 = New-ScheduledTaskTrigger -Daily -At "19:30"
$Settings2 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $Task2Name -TaskPath $TaskPath -Action $Action2 -Trigger $Trigger2 -Settings $Settings2 -Description "Strategy Lab daily evening market data collection" -Force | Out-Null
Write-Host "Registered: $Task2Name (Daily 19:30)" -ForegroundColor Green

# Task 3: Backup Weekly (Sunday 08:00 local)
$Task3Name = "TradingLab-Backup-Weekly"
$Action3 = New-ScheduledTaskAction -Execute $PythonExe -Argument "-m strategy_lab.cli backup" -WorkingDirectory $StrategyLabDir
$Trigger3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "08:00"
$Settings3 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $Task3Name -TaskPath $TaskPath -Action $Action3 -Trigger $Trigger3 -Settings $Settings3 -Description "Strategy Lab weekly historical database backup" -Force | Out-Null
Write-Host "Registered: $Task3Name (Sunday 08:00)" -ForegroundColor Green

# Task 4: Status Daily with Toast Notification (20:00 local)
$Task4Name = "TradingLab-Status-Daily"
$Action4 = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$StatusScriptPath`" -PythonPath `"$PythonExe`" -WorkingDir `"$StrategyLabDir`"" -WorkingDirectory $StrategyLabDir
$Trigger4 = New-ScheduledTaskTrigger -Daily -At "20:00"
$Settings4 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $Task4Name -TaskPath $TaskPath -Action $Action4 -Trigger $Trigger4 -Settings $Settings4 -Description "Strategy Lab daily health status check with alert notification" -Force | Out-Null
Write-Host "Registered: $Task4Name (Daily 20:00 with Toast Notification)" -ForegroundColor Green

Write-Host ""
Write-Host "Verification:" -ForegroundColor Cyan
Get-ScheduledTask -TaskPath $TaskPath | Format-Table TaskName, State
