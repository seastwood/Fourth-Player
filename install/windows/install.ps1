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
#
# Which pythonw is a real question rather than a lookup. `Get-Command
# pythonw.exe` returns whatever is first on PATH, and on this machine that was
# Inkscape's bundled interpreter -- a perfectly good Python with none of the
# things this needs. So: ask the py launcher for its own interpreters, take the
# windowless one beside each, and keep the first that can actually import the
# package. A test beats a guess.
function Find-Pythonw {
    $tried = @()
    $py = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
    if ($py) {
        foreach ($tag in @("-3.13", "-3.12", "-3.11", "-3")) {
            try { $exe = & $py $tag -c "import sys; print(sys.executable)" 2>$null }
            catch { continue }
            if ($LASTEXITCODE -ne 0 -or -not $exe) { continue }
            $tried += (Join-Path (Split-Path $exe -Parent) "pythonw.exe")
        }
    }
    $tried += (Get-Command pythonw.exe -ErrorAction SilentlyContinue |
               ForEach-Object { $_.Source })
    foreach ($candidate in ($tried | Where-Object { $_ -and (Test-Path $_) } |
                            Select-Object -Unique)) {
        # The console build beside it, because pythonw writes nothing back --
        # asking the silent one whether an import worked tells you nothing.
        $console = Join-Path (Split-Path $candidate -Parent) "python.exe"
        if (-not (Test-Path $console)) { continue }
        & $console -c "import fourthplayer" 2>$null
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    return $null
}

Push-Location $Repo
try { $python = Find-Pythonw } finally { Pop-Location }
if (-not $python) {
    throw ("No Python here can import fourthplayer. Install its dependencies " +
           "first: py -m pip install PyGObject==3.50.0 websockets'<'11 " +
           "cryptography qrcode pillow vgamepad pystray")
}
Write-Host "interpreter: $python"
Write-Host "repository : $Repo"

# --- the task --------------------------------------------------------------
$action = New-ScheduledTaskAction -Execute $python `
    -Argument "-m fourthplayer.tray" -WorkingDirectory $Repo
# The computer, not USERDOMAIN: on a workgroup machine that reads "WORKGROUP",
# which is not an account authority and Register-ScheduledTask refuses it with
# "No mapping between account names and security IDs was done".
$who = "$env:COMPUTERNAME\$env:USERNAME"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $who
# Interactive, so it lands in the session with the desktop in it. Highest
# because the host wants it for ViGEm; the tray itself does not.
$principal = New-ScheduledTaskPrincipal -UserId $who `
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
