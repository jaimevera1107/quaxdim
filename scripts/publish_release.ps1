param([string]$Owner = 'jaimevera1107', [string]$Repo = 'quaxdim')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $ProjectRoot
try {
    python tools/check_release.py
    if ($LASTEXITCODE -ne 0) { throw 'El chequeo de release falló.' }
    $version = (python tools/read_project_version.py).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'No pude leer la versión.' }
    $tag = "v$version"
    if ((git status --porcelain).Count -gt 0) { throw 'Tienes cambios sin confirmar. Haz commit y push primero.' }
    git fetch origin main --tags
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo consultar el remoto.' }
    $head = (git rev-parse HEAD).Trim()
    $remoteHead = (git rev-parse origin/main).Trim()
    if ($head -ne $remoteHead) { throw 'HEAD y origin/main no coinciden. Publica o actualiza main antes de liberar.' }
    git rev-parse "refs/tags/$tag" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { throw "Ya existe el tag $tag. No se reutilizan versiones." }
    gh auth status
    if ($LASTEXITCODE -ne 0) { throw 'Necesitas iniciar sesión en GitHub CLI con gh auth login.' }
    $runs = gh run list --repo "$Owner/$Repo" --workflow ci.yml --branch main --limit 20 --json headSha,conclusion,createdAt | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo leer el CI.' }
    $verified = @($runs | Where-Object { $_.headSha -eq $head -and $_.conclusion -eq 'success' })
    if ($verified.Count -eq 0) { throw "No hay ejecución CI aprobada del commit $head. Revisa GitHub Actions." }
    Write-Host "Versión: $version; commit validado: $head"
    Write-Host 'Confirma ANTES: el Trusted Publisher en PyPI, el environment pypi y su revisión obligatoria.'
    $confirmation = Read-Host "Escribe PUBLICAR para crear la release $tag que activa la subida a PyPI"
    if ($confirmation -cne 'PUBLICAR') { Write-Host 'Cancelado, sin publicar.'; exit 0 }
    git tag -a $tag -m "QuaxDim $version"
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el tag.' }
    git push origin $tag
    if ($LASTEXITCODE -ne 0) { throw 'Tag local creado, pero no subido; revisa antes de repetir.' }
    $releaseOptions = @()
    if ($version -match '(?:a|b|rc)[0-9]+') { $releaseOptions += '--prerelease' }
    gh release create $tag --repo "$Owner/$Repo" --verify-tag --title "QuaxDim $version" --generate-notes @releaseOptions
    if ($LASTEXITCODE -ne 0) { throw 'El tag ya está remoto, pero no pude crear la GitHub Release.' }
    Write-Host "Publicación iniciada: https://github.com/$Owner/$Repo/actions/workflows/release.yml"
    Write-Host 'La carga a PyPI solo se hace después de las pruebas y aprobación del entorno.'
}
finally { Pop-Location }
