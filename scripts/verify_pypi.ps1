param([string]$Version = (python (Join-Path $PSScriptRoot '..\tools\read_project_version.py')).Trim())
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$venv = Join-Path $root '.venv_check_pypi'
python -m venv $venv
if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno.' }
$py = Join-Path $venv 'Scripts\python.exe'
& $py -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip update falló.' }
& $py -m pip install "quaxdim==$Version"
if ($LASTEXITCODE -ne 0) { throw 'La versión aún no está disponible en PyPI o la instalación falló.' }
& $py -c "import quaxdim; from importlib.metadata import version; assert quaxdim.__version__ == version('quaxdim'); print('QuaxDim instalado:', quaxdim.__version__)"
if ($LASTEXITCODE -ne 0) { throw 'La instalación no superó la comprobación de importación.' }
Write-Host 'Validación PyPI aprobada. El entorno de prueba está en .venv_check_pypi'

