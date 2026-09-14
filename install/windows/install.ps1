<#
    Fourth Player on Windows.

    Registers the tray icon to start with this user's desktop session. The
    tray is what starts and supervises the host, so this is the only thing
    that needs installing -- and it has to be the *interactive* session,
    because d3d11screencapturesrc has no desktop to capture from session 0.

    Run it in an administrator PowerShell, from the repository:

        powershell -ExecutionPolicy Bypass -File install\windows\install.ps1
#>
[CmdletBinding()]
param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "Fourth Player"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if ($Uninstall) {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
                         Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
    Get-NetFirewallRule -DisplayName "Fourth Player*" -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    Write-Host "Removed."
    exit 0
}

# --- the interpreter -------------------------------------------------------
#
# pythonw.exe, not python.exe, and this is the whole reason this file exists.
# Started through python.exe -- or worse, through cmd.exe -- the tray gets a
# console window. It looks like nothing in particular, somebody closes it, and
# Windows sends the console-close signal to everything attached to it: the
# tray and the host it supervises both die, with exit code 0xC000013A. That
# happened, and the person it happened to had no way of knowing what the
# window was.
$python = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $python) {
    $py = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
    if ($py) {
        # Ask the launcher where its newest interpreter is, then take the
        # windowless one beside it.
        $exe = & $py -3 -c "import sys; print(sys.executable)"
        $candidate = Join-Path (Split-Path $exe -Parent) "pythonw.exe"
        if (Test-Path $candidate) { $python = $candidate }
    }
}
if (-not $python) {
    throw "pythonw.exe not found. Install Python from python.org or the Store."
}
Write-Host "interpreter: $python"
Write-Host "repository : $Repo"

# --- the task --------------------------------------------------------------
$action = New-ScheduledTaskAction -Execute $python `
    -Argument "-m fourthplayer.tray" -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
# Interactive, so it lands in the session with the desktop in it. Highest
# because the host wants it for ViGEm; the tray itself does not.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "registered: it will start when you log in"

# --- the firewall ----------------------------------------------------------
#
# Only the guest page's port. The setup page is on the loopback and wants
# nothing here.
if (-not (Get-NetFirewallRule -DisplayName "Fourth Player" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Fourth Player" -Direction Inbound `
        -Protocol TCP -LocalPort 8443 -Action Allow -Profile Any | Out-Null
    Write-Host "firewall: allowed TCP 8443 for the guest page"
}
if (-not (Get-NetFirewallRule -DisplayName "Fourth Player media" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Fourth Player media" -Direction Inbound `
        -Protocol UDP -Action Allow -Profile Any -Program $python | Out-Null
    Write-Host "firewall: allowed UDP for the media it sends"
}

# Windows creates a Block rule for an application when somebody dismisses the
# "allow this app?" prompt, and a block beats an allow -- so a dismissed
# dialog quietly makes every rule above useless. Say so rather than leaving
# somebody to find it.
$blocked = Get-NetFirewallRule -Direction Inbound -Action Block -Enabled True |
    ForEach-Object {
        $app = $_ | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue
        if ($app -and $app.Program -and $app.Program -like "*python*") { $_ }
    }
if ($blocked) {
    Write-Warning ("There are Block rules for python that will override the " +
                   "allows above. Remove them in Windows Defender Firewall, " +
                   "or re-run this after doing so.")
}

Start-ScheduledTask -TaskName $TaskName
Write-Host ""
Write-Host "Started. Look for the controller icon by the clock."
Write-Host "Right-click it and choose 'Set up this machine' to make an account."
