$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class ZadigNative {
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    public static extern IntPtr GetDlgItem(IntPtr hDlg, int nIDDlgItem);
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, StringBuilder lParam);
}
"@

$WM_COMMAND = 0x0111
$CB_GETCOUNT = 0x0146
$CB_GETCURSEL = 0x0147
$CB_GETLBTEXT = 0x0148
$IDM_LISTALL = 40004
$IDC_DEVICELIST = 1001
$outputPath = Join-Path $PSScriptRoot "zadig-devices.txt"

$process = Get-Process | Where-Object { $_.ProcessName -like "zadig*" } | Select-Object -First 1
if (-not $process) { throw "Zadig process not found" }

$root = [System.Windows.Automation.AutomationElement]::RootElement
$condition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
    $process.Id
)
$window = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $condition)
$closeCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty,
    "Close"
)
$close = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $closeCondition)
if ($close) {
    $invoke = $close.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $invoke.Invoke()
    Start-Sleep -Milliseconds 800
}

$process.Refresh()
$main = [IntPtr]$process.MainWindowHandle
$combo = [ZadigNative]::GetDlgItem($main, $IDC_DEVICELIST)
if ($combo -eq [IntPtr]::Zero) { throw "Zadig device list control not found" }

function Get-DeviceCount {
    [int][ZadigNative]::SendMessage($combo, $CB_GETCOUNT, [IntPtr]::Zero, [IntPtr]::Zero)
}

$before = Get-DeviceCount
[void][ZadigNative]::SendMessage($main, $WM_COMMAND, [IntPtr]$IDM_LISTALL, [IntPtr]::Zero)
Start-Sleep -Milliseconds 1500
$after = Get-DeviceCount
if ($after -lt $before) {
    [void][ZadigNative]::SendMessage($main, $WM_COMMAND, [IntPtr]$IDM_LISTALL, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 1500
}

$count = Get-DeviceCount
$selected = [int][ZadigNative]::SendMessage($combo, $CB_GETCURSEL, [IntPtr]::Zero, [IntPtr]::Zero)
$rows = @("count=$count selected=$selected")
for ($index = 0; $index -lt $count; $index++) {
    $text = New-Object System.Text.StringBuilder 512
    [void][ZadigNative]::SendMessage($combo, $CB_GETLBTEXT, [IntPtr]$index, $text)
    $rows += "$index`t$($text.ToString())"
}
$rows | Set-Content -LiteralPath $outputPath -Encoding UTF8
