param(
    [ValidateSet('rapido','completo','estres')]
    [string]$Modo = 'completo',
    [int]$Filas = 0,
    [string]$Archivo = '',
    [ValidateSet('auto','csv_stream','duckdb','polars')]
    [string]$MotorArchivo = 'auto',
    [switch]$SinInstalar,
    [switch]$ExigirMotores,
    [string]$PythonBase = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Venv = Join-Path $Root '.venv'
$Vpy = Join-Path $Venv 'Scripts\python.exe'

function Create-Venv {
    if ($PythonBase -ne '') {
        Write-Host "Creando entorno con: $PythonBase"
        & $PythonBase -m venv "$Venv"
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno' }
        return
    }
    $Candidates = @(
        @{Cmd='py'; Prefix=@('-3.11')},
        @{Cmd='py'; Prefix=@('-3.12')},
        @{Cmd='py'; Prefix=@('-3.10')},
        @{Cmd='python'; Prefix=@()}
    )
    foreach ($c in $Candidates) {
        Write-Host "Probando Python: $($c.Cmd) $($c.Prefix -join ' ')"
        try {
            $params = @($c.Prefix) + @('-m','venv',$Venv)
            $commandName = $c.Cmd
            & $commandName @params
            if (($LASTEXITCODE -eq 0) -and (Test-Path $Vpy)) { return }
        } catch {
            Write-Host "No disponible: $($c.Cmd)" -ForegroundColor Yellow
        }
    }
    throw 'No se encontro Python compatible. Recomendado Python 3.11 de 64 bits.'
}

if (-not (Test-Path $Vpy)) { Create-Venv }
Write-Host "Python aislado: $Vpy" -ForegroundColor Cyan
& $Vpy --version
if ($LASTEXITCODE -ne 0) { throw 'Python del entorno no funciona' }

if (-not $SinInstalar) {
    Write-Host "`nInstalando dependencias base y de pruebas..." -ForegroundColor Cyan
    & $Vpy -m pip install --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { throw 'Fallo la preparacion de pip/setuptools/wheel' }
    & $Vpy -m pip install -e '.[test]' psutil
    if ($LASTEXITCODE -ne 0) { throw 'Fallo instalacion base. Revisa conectividad y version de Python.' }

    if ($Modo -ne 'rapido') {
        $Extras = @('duckdb','polars','parquet','spark','excel','regex')
        foreach ($extra in $Extras) {
            Write-Host "`nInstalando motor opcional [$extra]..." -ForegroundColor Cyan
            & $Vpy -m pip install -e ".[${extra}]"
            if ($LASTEXITCODE -ne 0) {
                Write-Host "No se instalo [$extra]. Se registrara como NO PROBADO, no como aprobado." -ForegroundColor Yellow
            }
        }
    }
}

$Arguments = @((Join-Path $Root 'qa\run_suite.py'), '--mode', $Modo)
if ($Filas -gt 0) { $Arguments += @('--rows',"$Filas") }
if ($Archivo -ne '') {
    if (-not (Test-Path -LiteralPath $Archivo -PathType Leaf)) { throw "Archivo no encontrado: $Archivo" }
    $Arguments += @('--input', (Resolve-Path -LiteralPath $Archivo).Path, '--engine', $MotorArchivo)
}
if ($ExigirMotores) { $Arguments += '--strict-backends' }

Write-Host "`nEjecutando bateria de pruebas ($Modo)..." -ForegroundColor Cyan
& $Vpy @Arguments
$Code = $LASTEXITCODE
if ($Code -ne 0) { Write-Host 'Se encontraron fallos; consulta reportes/.' -ForegroundColor Red }
else { Write-Host 'Pruebas finalizadas. Consulta reportes/ (NO PROBADO no equivale a aprobado).' -ForegroundColor Green }
exit $Code
