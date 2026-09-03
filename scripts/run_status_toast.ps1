param([string]$PythonPath, [string]$WorkingDir)
Set-Location $WorkingDir
$output = & $PythonPath -m strategy_lab.cli status 2>&1 | Out-String
try {
    $json = $output | ConvertFrom-Json
    if ($json.status -ne "ok") {
        # Trigger Windows Toast Notification
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
        $template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
        $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)
        $textNodes = $xml.GetElementsByTagName("text")
        $textNodes.Item(0).AppendChild($xml.CreateTextNode("Trading Lab Alert")) > $null
        $textNodes.Item(1).AppendChild($xml.CreateTextNode("Strategy Lab status alert: " + $json.status)) > $null
        $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("TradingLab").Show($toast)
    }
} catch {
    Write-Warning "Status check parsing error: $_"
}
