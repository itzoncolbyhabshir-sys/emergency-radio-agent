$ErrorActionPreference = "Stop"
trap {
    ($_ | Out-String) | Set-Content -LiteralPath (Join-Path $PSScriptRoot "zadig-invoke-error.txt") -Encoding UTF8
    exit 1
}
Add-Type -AssemblyName UIAutomationClient
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class ZadigClickNative {
    [StructLayout(LayoutKind.Sequential)]
    public struct POINT { public int X; public int Y; }
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);
}
"@

$outputPath = Join-Path $PSScriptRoot "zadig-invoke-result.txt"
$process = Get-Process | Where-Object { $_.ProcessName -like "zadig*" } | Select-Object -First 1
if (-not $process) { throw "Zadig process not found" }
$root = [System.Windows.Automation.AutomationElement]::RootElement
$processCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
    $process.Id
)
$window = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $processCondition)
$elements = $window.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition
)
$names = @($elements | ForEach-Object { $_.Current.Name })
foreach ($required in @("Bulk-In, Interface (Interface 0)", "0BDA", "2838")) {
    if ($names -notcontains $required) { throw "Safety check failed: missing $required" }
}
if (-not ($names | Where-Object { $_ -match '^WinUSB \(v' })) {
    throw "Safety check failed: WinUSB target not selected"
}

$install = $elements | Where-Object { $_.Current.Name -eq "Install Driver" } | Select-Object -First 1
if (-not $install) { throw "Install Driver control not found" }
$patterns = @($install.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName })
"patterns=$($patterns -join ',')" | Set-Content -LiteralPath $outputPath -Encoding UTF8
if ($patterns -contains "InvokePatternIdentifiers.Pattern") {
    $invoke = $install.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $invoke.Invoke()
} else {
    $rectangle = $install.Current.BoundingRectangle
    if ($rectangle.Width -le 0 -or $rectangle.Height -le 0) {
        throw "Install Driver control has no clickable bounds"
    }
    $original = New-Object ZadigClickNative+POINT
    [void][ZadigClickNative]::GetCursorPos([ref]$original)
    [void][ZadigClickNative]::SetForegroundWindow([IntPtr]$process.MainWindowHandle)
    Start-Sleep -Milliseconds 300
    $x = [int]($rectangle.Left + $rectangle.Width / 2)
    $y = [int]($rectangle.Top + $rectangle.Height / 2)
    [void][ZadigClickNative]::SetCursorPos($x, $y)
    [ZadigClickNative]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    [ZadigClickNative]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 200
    [void][ZadigClickNative]::SetCursorPos($original.X, $original.Y)
}

$success = $false
for ($attempt = 0; $attempt -lt 120; $attempt++) {
    Start-Sleep -Seconds 1
    $window = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $processCondition)
    $current = $window.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    $currentNames = @($current | ForEach-Object { $_.Current.Name })
    if ($currentNames -contains "The driver was installed successfully.") {
        $success = $true
        break
    }
    if ($currentNames | Where-Object { $_ -match 'failed|error' }) {
        throw "Zadig reported a driver installation error"
    }
}
if (-not $success) { throw "Timed out waiting for Zadig success message" }
Add-Content -LiteralPath $outputPath -Encoding UTF8 -Value "status=success"
