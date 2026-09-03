param(
    [string]$SupabaseCli = ""
)

$ErrorActionPreference = "Stop"

if (-not $env:SUPABASE_STAGING_DB_URL) {
    Write-Error "SUPABASE_STAGING_DB_URL is required"
}

if ($env:SUPABASE_PROD_REF -and $env:SUPABASE_STAGING_DB_URL.Contains($env:SUPABASE_PROD_REF)) {
    Write-Error "Refusing to run migrations against production ref"
}

if (-not $SupabaseCli) {
    $local = Join-Path (Get-Location).Path "state/tools/supabase-cli-v2.116.0/supabase.exe"
    if (Test-Path -LiteralPath $local) {
        $SupabaseCli = $local
    } else {
        $command = Get-Command supabase -ErrorAction SilentlyContinue
        if (-not $command) {
            Write-Error "Supabase CLI is required"
        }
        $SupabaseCli = $command.Source
    }
}

$pgNetCheck = "do `$`$ begin if not exists (select 1 from pg_available_extensions where name = 'pg_net') then raise exception 'PG_NET_UNAVAILABLE'; end if; end `$`$;"

& $SupabaseCli db query --workdir apps/hub --db-url $env:SUPABASE_STAGING_DB_URL $pgNetCheck | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "pg_net is not available. Use collect --archive as the fallback design before applying 0004."
}

& $SupabaseCli db push --workdir apps/hub --db-url $env:SUPABASE_STAGING_DB_URL --skip-vault
if ($LASTEXITCODE -ne 0) {
    Write-Error "supabase db push failed"
}
