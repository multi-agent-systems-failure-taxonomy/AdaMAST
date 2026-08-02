param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("claude", "codex")]
    [string]$HostName,
    [Parameter(Mandatory = $true)]
    [string]$EventName
)

$ErrorActionPreference = "Stop"
$RuntimeVersion = "0.2.2"
$PluginDataDir = if ($env:PLUGIN_DATA) {
    $env:PLUGIN_DATA
} elseif ($env:CLAUDE_PLUGIN_DATA) {
    $env:CLAUDE_PLUGIN_DATA
} else {
    Join-Path $HOME ".adamast\plugins\$HostName"
}
$StateDir = Join-Path $PluginDataDir "state"
$UvDir = Join-Path $PluginDataDir "uv"
$env:UV_TOOL_DIR = Join-Path $PluginDataDir "uv-tools"
$env:UV_TOOL_BIN_DIR = Join-Path $PluginDataDir "bin"
$LogFile = Join-Path $StateDir "bootstrap.log"
$LockFile = Join-Path $StateDir "bootstrap.lock"
$PackageSpec = if ($env:ADAMAST_PACKAGE_SPEC) {
    $env:ADAMAST_PACKAGE_SPEC
} else {
    "adamast==$RuntimeVersion"
}

function Write-AdamastLog([string]$Message) {
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $Timestamp = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value "$Timestamp $Message"
}

function Test-AdamastRuntime([string]$Python) {
    if (-not $Python -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        return $false
    }
    $PreviousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $VersionCheck = (
            "import importlib.metadata, sys; " +
            "sys.exit(importlib.metadata.version('adamast') != sys.argv[1])"
        )
        & $Python -c $VersionCheck $RuntimeVersion *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
}

function Get-ManagedPython {
    $Candidates = @(
        (Join-Path $env:UV_TOOL_DIR "adamast\Scripts\python.exe"),
        (Join-Path $env:UV_TOOL_DIR "adamast\bin\python")
    )
    foreach ($Candidate in $Candidates) {
        if (Test-AdamastRuntime $Candidate) {
            return $Candidate
        }
    }
    return $null
}

function Get-AdamastPython {
    if ($env:ADAMAST_PYTHON -and (Test-AdamastRuntime $env:ADAMAST_PYTHON)) {
        return $env:ADAMAST_PYTHON
    }
    $Managed = Get-ManagedPython
    if ($Managed) {
        return $Managed
    }
    return $null
}

function Get-Uv {
    if ($env:ADAMAST_UV -and (Test-Path -LiteralPath $env:ADAMAST_UV)) {
        return $env:ADAMAST_UV
    }
    $Command = Get-Command uv -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }
    foreach ($Candidate in @(
        (Join-Path $UvDir "uv.exe"),
        (Join-Path $UvDir "uv")
    )) {
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            return $Candidate
        }
    }
    return $null
}

function Install-Uv {
    New-Item -ItemType Directory -Force -Path $UvDir, $StateDir | Out-Null
    Write-AdamastLog "installing the private uv bootstrap"
    $PreviousInstallDir = $env:UV_INSTALL_DIR
    try {
        $env:UV_INSTALL_DIR = $UvDir
        Invoke-RestMethod "https://astral.sh/uv/install.ps1" | Invoke-Expression
    } finally {
        $env:UV_INSTALL_DIR = $PreviousInstallDir
    }
    return Get-Uv
}

function Install-AdamastRuntime {
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $Lock = $null
    try {
        for ($Attempt = 0; $Attempt -lt 60 -and -not $Lock; $Attempt++) {
            try {
                $Lock = [System.IO.File]::Open(
                    $LockFile,
                    [System.IO.FileMode]::CreateNew,
                    [System.IO.FileAccess]::Write,
                    [System.IO.FileShare]::None
                )
                $OwnerBytes = [System.Text.Encoding]::UTF8.GetBytes("$PID")
                $Lock.Write($OwnerBytes, 0, $OwnerBytes.Length)
                $Lock.Flush()
            } catch [System.IO.IOException] {
                Start-Sleep -Seconds 1
                $Managed = Get-ManagedPython
                if ($Managed) {
                    return $Managed
                }
                try {
                    $Owner = [int](
                        Get-Content -LiteralPath $LockFile -Raw -ErrorAction Stop
                    )
                    if (-not (Get-Process -Id $Owner -ErrorAction SilentlyContinue)) {
                        Remove-Item -LiteralPath $LockFile -Force `
                            -ErrorAction SilentlyContinue
                    }
                } catch {
                    # An active owner keeps the file exclusively locked. An
                    # unlocked empty/corrupt file is stale and safe to retire.
                    try {
                        $Probe = [System.IO.File]::Open(
                            $LockFile,
                            [System.IO.FileMode]::Open,
                            [System.IO.FileAccess]::ReadWrite,
                            [System.IO.FileShare]::None
                        )
                        $Probe.Dispose()
                        Remove-Item -LiteralPath $LockFile -Force `
                            -ErrorAction SilentlyContinue
                    } catch {
                        # Another bootstrap still owns the file.
                    }
                }
            }
        }
        if (-not $Lock) {
            throw "Timed out waiting for another AdaMAST runtime bootstrap."
        }
        $Uv = Get-Uv
        if (-not $Uv) {
            $Uv = Install-Uv
        }
        if (-not $Uv) {
            throw "uv installation completed without a usable executable."
        }
        Write-AdamastLog "installing AdaMAST runtime $RuntimeVersion"
        $PreviousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $InstallOutput = (
                & $Uv tool install --force --upgrade $PackageSpec 2>&1 |
                    Out-String
            )
            $InstallExitCode = $LASTEXITCODE
            if ($InstallOutput.Trim()) {
                Add-Content -LiteralPath $LogFile -Encoding UTF8 `
                    -Value $InstallOutput.TrimEnd()
            }
        } finally {
            $ErrorActionPreference = $PreviousPreference
        }
        if ($InstallExitCode -ne 0) {
            throw "uv tool install exited with code $InstallExitCode."
        }
        $Managed = Get-ManagedPython
        if (-not $Managed) {
            throw "AdaMAST runtime did not report version $RuntimeVersion."
        }
        Write-AdamastLog "AdaMAST runtime ready"
        return $Managed
    } finally {
        if ($Lock) {
            $Lock.Dispose()
            Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
        }
    }
}

try {
    $Python = Get-AdamastPython
    if (-not $Python) {
        if ($EventName -ne "SessionStart") {
            exit 0
        }
        $Python = Install-AdamastRuntime
    }
    $Payload = [Console]::In.ReadToEnd()
    $PreviousOutputEncoding = $OutputEncoding
    try {
        # Windows PowerShell 5.1 otherwise uses its legacy native-pipeline
        # encoding, which can prepend a BOM or corrupt non-ASCII hook JSON.
        $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        $Payload | & $Python -m adamast.hosts.plugin_dispatcher `
            --host $HostName --event $EventName
        $DispatcherExitCode = $LASTEXITCODE
    } finally {
        $OutputEncoding = $PreviousOutputEncoding
    }
    exit $DispatcherExitCode
} catch {
    try {
        Write-AdamastLog "plugin launch failed: $($_.Exception.Message)"
    } catch {
        # A logging failure must not block the host conversation.
    }
    exit 0
}
