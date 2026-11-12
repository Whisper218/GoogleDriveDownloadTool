$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot

# Prefer a user-installed Python 3.10+ found on PATH.
$candidates = @('py', 'python', 'python3')
$pythonExe = $null
foreach ($candidate in $candidates) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command) {
        try {
            $resolved = & $command.Source -c 'import sys; assert sys.version_info >= (3, 10); print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $resolved) { $pythonExe = [string]($resolved | Select-Object -Last 1); break }
        } catch { }
    }
}
if (-not $pythonExe) { throw 'Install Python 3.10+ from https://www.python.org/downloads/windows/ and run install.bat again.' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $pythonExe -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create local Python environment.' }
}
& '.\.venv\Scripts\python.exe' -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed. Check network access.' }

if (-not (Test-Path -LiteralPath 'tools\rclone.exe')) {
    New-Item -ItemType Directory -Path 'tools' -Force | Out-Null
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } elseif ([Environment]::Is64BitOperatingSystem) { 'amd64' } else { '386' }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    # Resolve one version first, so the archive and checksum never straddle a latest-release change.
    $versionText = (Invoke-WebRequest -UseBasicParsing -Uri 'https://downloads.rclone.org/version.txt').Content
    if ($versionText -notmatch 'v\d+\.\d+\.\d+') { throw 'Invalid rclone version response.' }
    $version = $Matches[0]
    $archiveName = "rclone-$version-windows-$arch.zip"
    $url = "https://downloads.rclone.org/$version"
    $archive = Join-Path $PSScriptRoot "tools\$archiveName"
    Invoke-WebRequest -UseBasicParsing -Uri "$url/$archiveName" -OutFile $archive
    $checksumFile = Join-Path $PSScriptRoot "tools\SHA256SUMS-$version"
    Invoke-WebRequest -UseBasicParsing -Uri "$url/SHA256SUMS" -OutFile $checksumFile
    $checksums = Get-Content -LiteralPath $checksumFile -Raw -Encoding ASCII
    $pattern = '(?im)^([a-f0-9]{64})\s+\*?' + [regex]::Escape($archiveName) + '\s*$'
    if ($checksums -notmatch $pattern) { throw 'Archive checksum missing from official release.' }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $Matches[1]) {
        throw 'rclone archive checksum verification failed; archive has been retained for inspection.'
    }
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $PSScriptRoot 'tools') -Force
    $downloadedExe = Join-Path $PSScriptRoot "tools\rclone-$version-windows-$arch\rclone.exe"
    Copy-Item -LiteralPath $downloadedExe -Destination (Join-Path $PSScriptRoot 'tools\rclone.exe')
    # Downloaded archives are intentionally retained. No recursive deletion is used here.
}
& '.\tools\rclone.exe' version
if ($LASTEXITCODE -ne 0) { throw 'rclone executable check failed.' }
& '.\.venv\Scripts\python.exe' download_drive.py --plan
if ($LASTEXITCODE -ne 0) { throw 'Config validation failed. Check config.json or config.example.json.' }
