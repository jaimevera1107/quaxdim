param(
    [string]$Owner = 'jaimevera1107',
    [string]$Repo = 'quaxdim'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $ProjectRoot
try {
    foreach ($cmd in @('git', 'gh', 'python')) {
        if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
            throw "No se encontró $cmd. Consulta INICIO_AQUI.md para instalarlo."
        }
    }
    gh auth status
    if ($LASTEXITCODE -ne 0) { throw 'Ejecuta: gh auth login' }
    python tools/check_release.py
    if ($LASTEXITCODE -ne 0) { throw 'La validación de publicación falló.' }
    if (-not (Test-Path '.git')) {
        git init -b main
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo inicializar Git.' }
    }
    $branch = (git branch --show-current).Trim()
    if ($branch -ne 'main') { throw "La rama actual es '$branch', no 'main'. Revisar antes de continuar." }
    git add .
    if ($LASTEXITCODE -ne 0) { throw 'git add falló.' }
    $changes = @(git status --porcelain)
    if ($changes.Count -gt 0) {
        git commit -m 'chore: prepare QuaxDim publication with OIDC release pipeline'
        if ($LASTEXITCODE -ne 0) { throw 'git commit falló. Configura git user.name y user.email.' }
    }
    $fullRepo = "$Owner/$Repo"
    gh repo view $fullRepo --json nameWithOwner 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Creando repositorio PUBLICO $fullRepo..."
        gh repo create $fullRepo --public --source . --remote origin --push
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear/subir el repositorio.' }
    } else {
        $origin = git remote get-url origin 2>$null
        if ($LASTEXITCODE -ne 0) {
            git remote add origin "https://github.com/$fullRepo.git"
        } elseif ($origin -notmatch [regex]::Escape("$Owner/$Repo")) {
            throw "El origen apunta a '$origin' y no a '$fullRepo'. No se cambiará automáticamente."
        }
        git push -u origin main
        if ($LASTEXITCODE -ne 0) { throw 'El push falló. No se efectuará un force-push.' }
    }
    Write-Host ''
    Write-Host "Repositorio: https://github.com/$fullRepo"
    Write-Host 'Creando entornos GitHub para publicadores OIDC...'
    gh api -X PUT "repos/$fullRepo/environments/pypi" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno pypi.' }
    gh api -X PUT "repos/$fullRepo/environments/testpypi" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno testpypi.' }
    Write-Host 'Entornos creados. Importante: configura Required reviewers en Settings > Environments > pypi.'
    Write-Host 'Ahora registra el Trusted Publisher en https://pypi.org/manage/account/publishing/'
    Write-Host 'No crees la GitHub Release hasta que el CI y el Trusted Publisher estén listos.'
}
finally { Pop-Location }
