Param(
  [string]$Owner = "<YOUR_GH_OWNER>",
  [string]$Repo = "<YOUR_GH_REPO>"
)

Write-Host "Downloading latest release for $Owner/$Repo..."
$api = "https://api.github.com/repos/$Owner/$Repo/releases/latest"
$resp = Invoke-RestMethod -Uri $api -Headers @{ 'User-Agent' = 'ClassOnFace' }
if (-not $resp) { throw "Cannot fetch releases" }
$asset = $resp.assets | Where-Object { $_.name -like '*ClassOnFace-windows.zip' } | Select-Object -First 1
if (-not $asset) { throw "No windows zip asset found in latest release" }

$tmp = Join-Path $env:TEMP "ClassOnFace-download"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$zip = Join-Path $tmp $asset.name
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip

$dest = Join-Path $env:LOCALAPPDATA "ClassOnFace\latest"
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $dest)

$exe = Join-Path $dest "ClassOnFace.exe"
if (-not (Test-Path $exe)) { throw "Exe not found in zip" }

Start-Process -FilePath $exe
Start-Process "http://127.0.0.1:8000/"
