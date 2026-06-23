$ErrorActionPreference = "Stop"
trap {
    ($_ | Out-String) | Set-Content -LiteralPath (Join-Path $PSScriptRoot "zadig-install-error.txt") -Encoding UTF8
    exit 1
}
Add-Type -AssemblyName UIAutomationClient

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class RtlZadigNative {
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    public static extern IntPtr GetDlgItem(IntPtr hDlg, int nIDDlgItem);
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, StringBuilder lParam);
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int maxCount);
}
"@

$CB_GETCOUNT = 0x0146
$CB_GETLBTEXT = 0x0148
$CB_SETCURSEL = 0x014E
$CBN_SELCHANGE = 1
$WM_COMMAND = 0x0111
$BM_CLICK = 0x00F5
$IDC_DEVICELIST = 1001
$IDC_VID = 1002
$IDC_PID = 1003
$IDC_INSTALL = 1009
$IDC_TARGET = 1011
$outputPath = Join-Path $PSScriptRoot "zadig-install-result.txt"

function Get-ControlText([IntPtr]$handle) {
    $text = New-Object System.Text.StringBuilder 512
    [void][RtlZadigNative]::GetWindowText($handle, $text, $text.Capacity)
    $text.ToString()
}

function Get-AutomationName([int]$automationId) {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $processCondition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
        $process.Id
    )
    $window = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $processCondition)
    $idCondition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
        $automationId.ToString()
    )
    $element = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $idCondition)
    if ($element) { return $element.Current.Name }
    return ""
}

$process = Get-Process | Where-Object { $_.ProcessName -like "zadig*" } | Select-Object -First 1
if (-not $process) { throw "Zadig process not found" }
$process.Refresh()
$main = [IntPtr]$process.MainWindowHandle
$combo = [RtlZadigNative]::GetDlgItem($main, $IDC_DEVICELIST)
$count = [int][RtlZadigNative]::SendMessage($combo, $CB_GETCOUNT, [IntPtr]::Zero, [IntPtr]::Zero)

$targetIndex = -1
$targetName = ""
for ($index = 0; $index -lt $count; $index++) {
    $text = New-Object System.Text.StringBuilder 512
    [void][RtlZadigNative]::SendMessage($combo, $CB_GETLBTEXT, [IntPtr]$index, $text)
    if ($text.ToString() -match '^Bulk-In, Interface \(Interface 0\)$') {
        $targetIndex = $index
        $targetName = $text.ToString()
        break
    }
}
if ($targetIndex -lt 0) { throw "RTL-SDR Interface 0 not found in Zadig" }

[void][RtlZadigNative]::SendMessage($combo, $CB_SETCURSEL, [IntPtr]$targetIndex, [IntPtr]::Zero)
$selectionChanged = [IntPtr]($IDC_DEVICELIST -bor ($CBN_SELCHANGE -shl 16))
[void][RtlZadigNative]::SendMessage($main, $WM_COMMAND, $selectionChanged, $combo)
Start-Sleep -Seconds 2

$automationRoot = [System.Windows.Automation.AutomationElement]::RootElement
$processCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
    $process.Id
)
$automationWindow = $automationRoot.FindFirst([System.Windows.Automation.TreeScope]::Children, $processCondition)
$automationElements = $automationWindow.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition
)
$automationNames = @($automationElements | ForEach-Object { $_.Current.Name })
$vid = if ($automationNames -contains "0BDA") { "0BDA" } else { "" }
$devicePid = if ($automationNames -contains "2838") { "2838" } else { "" }
$driverTarget = @($automationNames | Where-Object { $_ -match '^WinUSB \(v' }) | Select-Object -First 1
$install = [RtlZadigNative]::GetDlgItem($main, $IDC_INSTALL)

$preflight = "device=$targetName index=$targetIndex vid=$vid pid=$devicePid target=$driverTarget"
$preflight | Set-Content -LiteralPath $outputPath -Encoding UTF8
if ($vid -ne "0BDA" -or $devicePid -ne "2838") {
    throw "Safety check failed: expected 0BDA:2838, got ${vid}:${devicePid}"
}
if ($driverTarget -notmatch "WinUSB") {
    throw "Safety check failed: target driver is not WinUSB: $driverTarget"
}

[void][RtlZadigNative]::SendMessage($install, $BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero)

$success = $false
for ($attempt = 0; $attempt -lt 90; $attempt++) {
    Start-Sleep -Seconds 1
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
        $process.Id
    )
    $window = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $condition)
    if ($window) {
        $nameCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::NameProperty,
            "The driver was installed successfully."
        )
        if ($window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $nameCondition)) {
            $success = $true
            break
        }
    }
}

if (-not $success) { throw "Timed out waiting for Zadig driver installation" }
Add-Content -LiteralPath $outputPath -Encoding UTF8 -Value "status=success"
