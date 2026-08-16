param(
    [string]$Token = $env:TWINE_PASSWORD
)

if (-not $Token) {
    Write-Error "Set the TWINE_PASSWORD environment variable to your PyPI API token or pass -Token '<token>'"
    exit 1
}

$env:TWINE_USERNAME = "__token__"
$env:TWINE_PASSWORD = $Token

Write-Host "Cleaning old builds..."
if (Test-Path -Path dist) { Remove-Item -Recurse -Force dist }

Write-Host "Building distributions..."
python -m build

Write-Host "Checking artifacts with twine..."
python -m twine check dist/*

# Read version from pyproject.toml to upload only matching artifacts
$py = Get-Content -Raw ../pyproject.toml | Select-String -Pattern 'version\s*=\s*"([^"]+)"' | ForEach-Object { $_.Matches[0].Groups[1].Value }
if (-not $py) { Write-Error "Could not find version in pyproject.toml"; exit 1 }
$version = $py
Write-Host "Version detected: $version"

$files = Get-ChildItem -Path dist | Where-Object { $_.Name -like "*-$version*" } | ForEach-Object { $_.FullName }
if (-not $files) { Write-Error "No dist files found for version $version"; exit 1 }

Write-Host "Uploading to PyPI (only files for version $version)..."
python -m twine upload --verbose --skip-existing $files

Write-Host "Done."
