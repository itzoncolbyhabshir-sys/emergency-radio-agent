$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient

$outputPath = Join-Path $PSScriptRoot "zadig-controls.txt"
$process = Get-Process | Where-Object { $_.ProcessName -like "zadig*" } | Select-Object -First 1
if (-not $process) {
    "Zadig process not found" | Set-Content -LiteralPath $outputPath -Encoding UTF8
    exit 1
}

$root = [System.Windows.Automation.AutomationElement]::RootElement
$condition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
    $process.Id
)
$window = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $condition)
if (-not $window) {
    "Zadig window not found for PID $($process.Id)" | Set-Content -LiteralPath $outputPath -Encoding UTF8
    exit 2
}

$rows = @("WINDOW | $($window.Current.Name) | PID=$($process.Id)")
$elements = $window.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition
)
foreach ($element in $elements) {
    $current = $element.Current
    if ($current.Name -or $current.AutomationId) {
        $rows += "{0} | {1} | {2} | enabled={3}" -f `
            $current.ControlType.ProgrammaticName,
            $current.Name,
            $current.AutomationId,
            $current.IsEnabled
    }
}
$rows | Set-Content -LiteralPath $outputPath -Encoding UTF8
