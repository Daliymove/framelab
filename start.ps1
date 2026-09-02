param(
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [switch]$RebuildFrontend,
    [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"
$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $appDir ".venv\Scripts\python.exe"
$requirements = Join-Path $appDir "requirements.txt"
$webDir = Join-Path $appDir "web"
$frontendIndex = Join-Path $webDir "dist\index.html"

function Resolve-BasePython {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return (Get-Command python).Source
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return (Get-Command py).Source
    }
    throw "Python 3 not found. Install Python 3.11 or newer."
}

function Test-PythonDependencies {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $venvPython -c "import fastapi, uvicorn, sqlalchemy, httpx, PIL, multipart, dotenv" 2>$null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Install-PythonDependencies {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $venvPython -m pip install -r $requirements
        $installExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($installExitCode -ne 0) {
        throw "Python dependency installation failed with exit code $installExitCode."
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    $basePython = Resolve-BasePython
    Write-Host "Creating local Python environment..."
    & $basePython -m venv (Join-Path $appDir ".venv")
}

if (-not (Test-PythonDependencies)) {
    Write-Host "Installing Python dependencies..."
    Install-PythonDependencies
}

$sourceFiles = Get-ChildItem -Path (Join-Path $webDir "src"), (Join-Path $webDir "index.html"), (Join-Path $webDir "package.json") -Recurse -File
$latestSource = $sourceFiles | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$needsBuild = $RebuildFrontend -or -not (Test-Path -LiteralPath $frontendIndex)
if (-not $needsBuild -and $latestSource) {
    $needsBuild = $latestSource.LastWriteTime -gt (Get-Item -LiteralPath $frontendIndex).LastWriteTime
}

if ($needsBuild) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        if (Test-Path -LiteralPath $frontendIndex) {
            Write-Warning "Frontend sources changed, but npm is unavailable. Using the existing build."
            $needsBuild = $false
        }
        else {
            throw "Node.js and npm are required to build the React interface."
        }
    }
}

if ($needsBuild) {
    if (-not (Test-Path -LiteralPath (Join-Path $webDir "node_modules"))) {
        Write-Host "Installing frontend dependencies..."
        & npm install --prefix $webDir
    }
    Write-Host "Building frontend..."
    & npm run build --prefix $webDir
}

$env:FRAMELAB_PORT = [string]$Port
if ($DataDir) {
    $env:FRAMELAB_DATA_DIR = [System.IO.Path]::GetFullPath($DataDir)
}

$workerArgs = @("-m", "framelab.worker")
$previousWorkerParentPid = $env:FRAMELAB_WORKER_PARENT_PID
$env:FRAMELAB_WORKER_PARENT_PID = [string]$PID
$worker = Start-Process -FilePath $venvPython -ArgumentList $workerArgs -WorkingDirectory $appDir -PassThru -WindowStyle Hidden
if ($null -eq $previousWorkerParentPid) {
    Remove-Item Env:FRAMELAB_WORKER_PARENT_PID -ErrorAction SilentlyContinue
}
else {
    $env:FRAMELAB_WORKER_PARENT_PID = $previousWorkerParentPid
}

try {
    $serverArgs = @((Join-Path $appDir "server.py"), "--port", [string]$Port)
    if (-not $NoBrowser) {
        $serverArgs += "--open-browser"
    }
    & $venvPython @serverArgs
}
finally {
    if ($worker -and -not $worker.HasExited) {
        Stop-Process -Id $worker.Id -Force
    }
}
