# Keeps Windows from idle-sleeping while a long job runs. Changes no system setting:
# the request ends when this script ends (or when the stop file appears / the time limit passes).
# Usage: powershell -File scripts/keep_awake.ps1 <stop-file> [max-hours]
param(
    [Parameter(Mandatory = $true)][string]$StopFile,
    [double]$MaxHours = 10
)
Add-Type -Namespace Win32 -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);'
$ES_CONTINUOUS = [uint32]2147483648
$ES_SYSTEM_REQUIRED = [uint32]1
$deadline = (Get-Date).AddHours($MaxHours)
while (-not (Test-Path $StopFile) -and (Get-Date) -lt $deadline) {
    [void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
    Start-Sleep -Seconds 30
}
[void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS)   # release
