param([string]$Version = (python (Join-Path $PSScriptRoot '..\tools\read_project_version.py')).Trim())
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$venv = Join-Path $root '.venv_check_pypi'
python -m venv $venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create the environment.' }
$py = Join-Path $venv 'Scripts\python.exe'
& $py -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }
& $py -m pip install "quaxdim==$Version"
if ($LASTEXITCODE -ne 0) { throw 'The version is not on PyPI yet, or the installation failed.' }
& $py -c "import quaxdim; from importlib.metadata import version; assert quaxdim.__version__ == version('quaxdim'); print('QuaxDim installed:', quaxdim.__version__)"
if ($LASTEXITCODE -ne 0) { throw 'The installation failed the import check.' }
Write-Host 'PyPI validation passed. The check environment is in .venv_check_pypi'
