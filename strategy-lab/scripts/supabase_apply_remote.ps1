param(
    [string]$SupabaseCli = "",
    [switch]$SkipFunctions,
    [switch]$SkipSecrets,
    [switch]$SkipStorage
)

$ErrorActionPreference = "Stop"

function Optional-Env([string]$Name) {
    return [Environment]::GetEnvironmentVariable($Name)
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

$workdir = "apps/hub"
$dbUrl = Optional-Env "SUPABASE_STAGING_DB_URL"
$projectRef = Optional-Env "SUPABASE_PROJECT_REF"
$accessToken = Optional-Env "SUPABASE_ACCESS_TOKEN"
$prodRef = Optional-Env "SUPABASE_PROD_REF"

$linkedRefFile = Join-Path (Get-Location).Path "$workdir/supabase/.temp/project-ref"
if ([string]::IsNullOrWhiteSpace($projectRef) -and (Test-Path -LiteralPath $linkedRefFile)) {
    $projectRef = (Get-Content -LiteralPath $linkedRefFile -Raw).Trim()
}

if ([string]::IsNullOrWhiteSpace($projectRef)) {
    Write-Error "SUPABASE_PROJECT_REF is required unless apps/hub is already linked"
}

if (-not [string]::IsNullOrWhiteSpace($dbUrl) -and $dbUrl -notmatch '^postgres(?:ql)?://') {
    Write-Error "SUPABASE_STAGING_DB_URL must be a percent-encoded PostgreSQL connection string, not the HTTPS API URL"
}

if ([string]::IsNullOrWhiteSpace($dbUrl) -and -not (Test-Path -LiteralPath $linkedRefFile)) {
    Write-Error "Link apps/hub with 'supabase link' or provide SUPABASE_STAGING_DB_URL"
}

if ($prodRef -and ($dbUrl.Contains($prodRef) -or $projectRef -eq $prodRef)) {
    Write-Error "Refusing to apply Strategy Lab Hub changes against the production ref"
}

if (-not [string]::IsNullOrWhiteSpace($accessToken)) {
    $env:SUPABASE_ACCESS_TOKEN = $accessToken
}

$pgNetCheck = "do `$`$ begin if not exists (select 1 from pg_available_extensions where name = 'pg_net') then raise exception 'PG_NET_UNAVAILABLE'; end if; end `$`$;"
$dbQueryBaseArgs = @("db", "query", "--workdir", $workdir, "--agent", "no")
if ([string]::IsNullOrWhiteSpace($dbUrl)) {
    $dbQueryBaseArgs += "--linked"
} else {
    $dbQueryBaseArgs += @("--db-url", $dbUrl)
}

Write-Host "Checking pg_net availability..."
$pgNetArgs = $dbQueryBaseArgs + @($pgNetCheck)
& $SupabaseCli @pgNetArgs | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "pg_net is not available. Stop here and use collect --archive fallback before applying 0004."
}

Write-Host "Applying database migrations..."
$dbPushArgs = @("db", "push", "--workdir", $workdir, "--skip-vault", "--agent", "no")
if (-not [string]::IsNullOrWhiteSpace($dbUrl)) {
    $dbPushArgs += @("--db-url", $dbUrl)
}
& $SupabaseCli @dbPushArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "supabase db push failed"
}

if (-not $SkipStorage) {
    Write-Host "Ensuring Storage buckets exist..."
    $bucketSql = @"
insert into storage.buckets (id, name, public)
values ('manifests', 'manifests', true)
on conflict (id) do update set public = excluded.public;

insert into storage.buckets (id, name, public)
values ('parquet', 'parquet', false)
on conflict (id) do update set public = excluded.public;
"@
    $bucketArgs = $dbQueryBaseArgs + @($bucketSql)
    & $SupabaseCli @bucketArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Storage bucket creation failed"
    }
}

if (-not $SkipSecrets) {
    $secretLines = @()
    foreach ($name in @(
            "HUB_ENV",
            "MANIFEST_PUBKEY_A",
            "MANIFEST_PUBKEY_B",
            "MANIFEST_TEST_PUBKEY",
            "HUB_JWT_SECRET",
            "ARCHIVE_CONTROL_TOKEN",
            "SUPABASE_SERVICE_ROLE_KEY",
            "R2_ENDPOINT",
            "R2_BUCKET",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_REGION"
        )) {
        $value = Optional-Env $name
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            $escaped = $value.Replace("\", "\\").Replace("`r", "").Replace("`n", "")
            $secretLines += "$name=$escaped"
        }
    }
    if ($secretLines.Count -gt 0) {
        Write-Host "Applying Edge Function secrets from environment..."
        $stateDir = Join-Path (Get-Location).Path "state"
        New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
        $secretFile = Join-Path $stateDir ("supabase-secrets-" + [guid]::NewGuid().ToString("N") + ".env")
        try {
            [System.IO.File]::WriteAllLines($secretFile, $secretLines)
            & $SupabaseCli secrets set --project-ref $projectRef --env-file $secretFile --agent no | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Error "supabase secrets set failed"
            }
        } finally {
            if (Test-Path -LiteralPath $secretFile) {
                Remove-Item -LiteralPath $secretFile -Force
            }
        }
    } else {
        Write-Host "No Edge Function secrets found in environment; skipping secrets."
    }
}

if (-not $SkipFunctions) {
    Write-Host "Deploying JWT-protected Edge Functions..."
    & $SupabaseCli functions deploy --workdir $workdir --project-ref $projectRef --use-api --agent no archive mirror publish
    if ($LASTEXITCODE -ne 0) {
        Write-Error "JWT-protected Edge Function deployment failed"
    }

    Write-Host "Deploying custom-auth Edge Functions..."
    & $SupabaseCli functions deploy --workdir $workdir --project-ref $projectRef --use-api --no-verify-jwt --agent no client_token outcomes manifest_current
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Custom-auth Edge Function deployment failed"
    }
}

Write-Host "Strategy Lab Supabase remote apply completed."
