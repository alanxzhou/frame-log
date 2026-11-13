# Run once, from PowerShell opened as administrator, in this server\ folder:
#   powershell -ExecutionPolicy Bypass -File .\install-startup-task.ps1
#
# Creates the "Frame Log sync server" task: it runs start-server.ps1 as you, at boot, whether or
# not you're logged in, with no window. "S4U" means Windows doesn't store your password for it;
# the task can still reach the internet and your files, which is all the server needs.
# Remove it with: Unregister-ScheduledTask -TaskName "Frame Log sync server"
$root = $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\start-server.ps1`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Frame Log sync server" -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "Runs the Frame Log sync server (start-server.ps1) at boot." -Force |
    Select-Object TaskName, State
Start-ScheduledTask -TaskName "Frame Log sync server"
Write-Host "Started. Log: $root\data\server.log"
