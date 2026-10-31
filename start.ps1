param(
    [string]$SshHost = [Environment]::GetEnvironmentVariable('ASTROGATE_IMG_SSH_HOST', 'User'),
    [int]$TunnelPort = 18189,
    [int]$RemotePort = 43922,
    [int]$Port = 8188
)

$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('RemotePort')) {
    $configuredPort = [Environment]::GetEnvironmentVariable('ASTROGATE_IMG_SSH_REMOTE_PORT', 'User')
    if ($configuredPort) { $RemotePort = [int]$configuredPort }
}
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$tunnelProcess = $null
$previousApiBase = $env:ASTROGATE_IMG_API_BASE
if (-not $env:ASTROGATE_TEXT_API_KEY) {
    $env:ASTROGATE_TEXT_API_KEY = [Environment]::GetEnvironmentVariable('ASTROGATE_TEXT_API_KEY', 'User')
}

try {
    if ($SshHost) {
        if ($SshHost -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_.@:-]*$') {
            throw 'Invalid SSH host.'
        }
        $listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $TunnelPort -State Listen -ErrorAction SilentlyContinue
        if ($listener) {
            $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
            if ($owner.Name -ne 'ssh.exe' -or $owner.CommandLine -notlike "*127.0.0.1:${TunnelPort}:127.0.0.1:${RemotePort}*" -or $owner.CommandLine -notlike "*$SshHost*") {
                throw "Port $TunnelPort is occupied. Choose another -TunnelPort."
            }
        } else {
            $tunnelLog = Join-Path $env:TEMP "comfyui-astrogate-ssh-$TunnelPort.log"
            $tunnelProcess = Start-Process -FilePath 'ssh.exe' -WindowStyle Hidden -PassThru -RedirectStandardError $tunnelLog -ArgumentList @(
                '-N', '-T', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes',
                '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
                '-L', "127.0.0.1:${TunnelPort}:127.0.0.1:${RemotePort}", $SshHost
            )
            $ready = $false
            for ($attempt = 0; $attempt -lt 50; $attempt++) {
                if ($tunnelProcess.HasExited) { throw "SSH tunnel failed. See $tunnelLog" }
                if (Get-NetTCPConnection -LocalPort $TunnelPort -State Listen -OwningProcess $tunnelProcess.Id -ErrorAction SilentlyContinue) {
                    $ready = $true
                    break
                }
                Start-Sleep -Milliseconds 200
            }
            if (-not $ready) { throw "SSH tunnel did not start. See $tunnelLog" }
        }
        $env:ASTROGATE_IMG_API_BASE = "http://127.0.0.1:$TunnelPort/internal/astrogate/v1"
    }
    Push-Location $projectRoot
    try {
        & $pythonPath main.py --listen 127.0.0.1 --port $Port --disable-auto-launch
        if ($LASTEXITCODE -ne 0) { throw "ComfyUI exited with code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
} finally {
    $env:ASTROGATE_IMG_API_BASE = $previousApiBase
    if ($null -ne $tunnelProcess -and -not $tunnelProcess.HasExited) {
        Stop-Process -Id $tunnelProcess.Id
    }
}
