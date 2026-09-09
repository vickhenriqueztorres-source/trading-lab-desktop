# R-COL-1 / I-8 / I-14: launch the Lab's interactive native-keyring setup.
# No credentials in arguments, files, shell history or Desktop Bot state.
$ErrorActionPreference = 'Stop'
$labDirectory = Split-Path -Parent $PSScriptRoot
$labPython = Join-Path $labDirectory '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $labPython)) {
    throw 'O ambiente Python independente do Strategy Lab não está instalado.'
}
Push-Location -LiteralPath $labDirectory
try {
    & $labPython -m strategy_lab.cli credentials set
    if ($LASTEXITCODE -ne 0) {
        throw 'Cadastro não concluído. Execute em terminal interativo com entrada oculta.'
    }
    & $labPython -m strategy_lab.cli credentials status
} finally {
    Pop-Location
}
