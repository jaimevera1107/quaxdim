param(
    [ValidateSet('csv_stream','duckdb','polars','spark','todos')]
    [string]$Motor='todos',
    [int[]]$Filas=@(1000000,5000000,10000000)
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Ejecuta primero Ejecutar_Pruebas_Completas.cmd para crear .venv' }
$Destination = Join-Path $Root ('reportes\escala_v08_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$Engines = if ($Motor -eq 'todos') { @('csv_stream','duckdb','polars','spark') } else { @($Motor) }
foreach ($n in $Filas) {
    if ($n -lt 1 -or $n -gt 100000000) { throw 'Filas debe estar en el rango 1..100000000' }
    foreach ($name in $Engines) {
        $log = Join-Path $Destination "${name}_${n}.log"
        $json = Join-Path $Destination "${name}_${n}.json"
        Write-Host "Medicion: $name / $n registros" -ForegroundColor Cyan
        $oldPref = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $Python (Join-Path $Root 'qa\benchmark_v08.py') --engine $name --rows $n --output $json *> $log
            $rc = $LASTEXITCODE
        } finally { $ErrorActionPreference = $oldPref }
        if ($rc -ne 0) { Write-Host "Fallo: $log" -ForegroundColor Yellow }
    }
}
Write-Host "Informes: $Destination" -ForegroundColor Green
Write-Host 'Los resultados de Spark local no validan escalabilidad distribuida.'
