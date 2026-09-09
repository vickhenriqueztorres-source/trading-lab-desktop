# R-COL-1 / I-8 / I-14: run one real read-only collection through the linked
# Supabase pooler without persisting the database password or placing it in argv.
[CmdletBinding()]
param(
    [string[]]$Assets = @('EURUSD-OTC'),
    [Nullable[long]]$FromTs = $null,
    [switch]$PayoutOnly
)

$ErrorActionPreference = 'Stop'
$labDirectory = Split-Path -Parent $PSScriptRoot
$labPython = Join-Path $labDirectory '.venv/Scripts/python.exe'
$poolerFile = Join-Path $labDirectory 'apps/hub/supabase/.temp/pooler-url'
$resultFile = Join-Path $labDirectory 'state/last-collection-result.json'

if (-not (Test-Path -LiteralPath $labPython)) {
    throw 'O ambiente Python independente do Strategy Lab não está instalado.'
}
if (-not (Test-Path -LiteralPath $poolerFile)) {
    throw 'O projeto Supabase ainda não está vinculado neste Strategy Lab.'
}

$poolerUri = [Uri](Get-Content -Raw -LiteralPath $poolerFile).Trim()
if ($poolerUri.Scheme -ne 'postgresql' -or -not $poolerUri.Host -or -not $poolerUri.UserInfo) {
    throw 'A configuração local do pooler Supabase é inválida.'
}
if ($poolerUri.UserInfo.Contains(':')) {
    throw 'A configuração do pooler não pode conter senha persistida.'
}

$securePassword = Read-Host 'Senha do banco Supabase (entrada oculta)' -AsSecureString
$passwordPointer = [IntPtr]::Zero
$plainPassword = $null
Push-Location -LiteralPath $labDirectory
try {
    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    if ([string]::IsNullOrWhiteSpace($plainPassword)) {
        throw 'A senha do banco não foi informada.'
    }
    $encodedUser = [Uri]::EscapeDataString([Uri]::UnescapeDataString($poolerUri.UserInfo))
    $encodedPassword = [Uri]::EscapeDataString($plainPassword)
    $env:SUPABASE_DB_URL = '{0}://{1}:{2}@{3}:{4}{5}' -f @(
        $poolerUri.Scheme,
        $encodedUser,
        $encodedPassword,
        $poolerUri.Host,
        $poolerUri.Port,
        $poolerUri.PathAndQuery
    )

    & $labPython -m strategy_lab.cli collection-preflight
    if ($LASTEXITCODE -ne 0) {
        throw 'O preflight local da coleta não foi aprovado.'
    }

    $collectArgs = @('-m', 'strategy_lab.cli', 'collect', '--assets') + $Assets
    if ($null -ne $FromTs) {
        $collectArgs += @('--from', [string]$FromTs.Value)
    }
    if ($PayoutOnly) {
        $collectArgs += '--payout-only'
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $resultFile) | Out-Null
    & $labPython @collectArgs | Tee-Object -FilePath $resultFile
    if ($LASTEXITCODE -ne 0) {
        throw 'A coleta foi abortada; nenhuma escrita parcial foi confirmada.'
    }
} finally {
    Remove-Item Env:SUPABASE_DB_URL -ErrorAction SilentlyContinue
    $plainPassword = $null
    $securePassword = $null
    if ($passwordPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    }
    Pop-Location
}
