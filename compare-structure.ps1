param(
  # Root folder of your current project
  [string]$Root = (Get-Location).Path,
  # Report output path (will be created/overwritten in $Root)
  [string]$ReportPath = "STRUCTURE_COMPARE.md"
)

# =========================
# Helpers
# =========================
function Normalize-RelPath {
  param([string]$RootPath, [string]$FullPath)
  $rel = $FullPath.Substring($RootPath.Length).TrimStart('\','/')
  return ($rel -replace '\\','/')
}

function Get-CurrentPaths {
  param([string]$RootPath, [string[]]$IgnorePatterns)

  if (-not (Test-Path $RootPath)) {
    throw "Root path not found: $RootPath"
  }

  $rootResolved = (Resolve-Path $RootPath).Path

  $dirs = Get-ChildItem -LiteralPath $rootResolved -Recurse -Force -Directory `
    | ForEach-Object { Normalize-RelPath -RootPath $rootResolved -FullPath $_.FullName }
  $files = Get-ChildItem -LiteralPath $rootResolved -Recurse -Force -File `
    | ForEach-Object { Normalize-RelPath -RootPath $rootResolved -FullPath $_.FullName }

  $filter = {
    param($p)
    foreach ($pat in $IgnorePatterns) {
      if ($p -match $pat) { return $false }
    }
    return $true
  }

  $dirs  = $dirs  | Where-Object { & $filter $_ } | Sort-Object -Unique
  $files = $files | Where-Object { & $filter $_ } | Sort-Object -Unique

  [PSCustomObject]@{ Dirs = $dirs; Files = $files }
}

function New-Set {
  param([string[]]$Items)
  $set = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
  foreach ($i in $Items) { [void]$set.Add($i) }
  return $set
}

# =========================
# Ignore rules
# =========================
$IgnorePatterns = @(
  '^\.git($|/)', '^\.github($|/)',
  '(^|/)__pycache__($|/)', '(^|/)\.mypy_cache($|/)', '(^|/)\.ruff_cache($|/)', '(^|/)\.pytest_cache($|/)',
  '(^|/)\.venv($|/)', '(^|/)venv($|/)',
  '(^|/)\.idea($|/)', '(^|/)\.vscode($|/)',
  '(^|/)node_modules($|/)',
  '(^|/)volumes($|/)', '(^|/)logs($|/)'
)

# =========================
# Expected baseline (edit if needed)
# =========================
$ExpectedDirs = @(
  'docs',
  'infra/oracle-db',
  'backend',
  'backend/config',
  'backend/config/prompts',
  'backend/app',
  'backend/app/models',
  'backend/app/routers',
  'backend/core',
  'backend/core/ports',
  'backend/core/services',
  'backend/core/factories',
  'backend/providers',
  'backend/providers/oci',
  'backend/providers/openai',
  'backend/providers/local',
  'backend/providers/common',
  'backend/ingestion',
  'backend/ingestion/loaders',
  'backend/ingestion/splitters',
  'backend/ingestion/pipelines',
  'backend/queue',
  'backend/queue/adapters',
  'backend/repos',
  'backend/worker',
  'backend/tests',
  'backend/tests/unit',
  'backend/tests/integration'
)

$ExpectedFiles = @(
  'README.md',
  '.gitignore',
  'compose.yml',

  'docs/API.md',
  'docs/ARCHITECTURE.md',
  'docs/ADR-0001-no-queue-for-mvp.md',
  'docs/DB-DDL.md',

  'infra/oracle-db/docker-compose.yml',
  'infra/oracle-db/provision_db.sh',
  'infra/oracle-db/.env_<customer>.example',
  'infra/oracle-db/README.md',

  'backend/Dockerfile',
  'backend/requirements.txt',
  'backend/.env.example',

  'backend/config/app.yaml',
  'backend/config/providers.yaml',

  'backend/app/main.py',
  'backend/app/deps.py',
  'backend/app/models/chat.py',
  'backend/app/models/ingest.py',
  'backend/app/routers/health.py',
  'backend/app/routers/chat.py',
  'backend/app/routers/ingest.py',
  'backend/app/routers/jobs.py',

  'backend/core/ports/chat_model.py',
  'backend/core/ports/embeddings.py',
  'backend/core/ports/vector_store.py',
  'backend/core/ports/reranker.py',
  'backend/core/ports/dispatcher.py',

  'backend/core/services/retrieval_service.py',
  'backend/core/services/ingest_service.py',
  'backend/core/services/eval_service.py',
  'backend/core/factories/provider_registry.py',

  'backend/providers/oci/chat_model.py',
  'backend/providers/oci/embeddings.py',
  'backend/providers/oci/vectorstore.py',
  'backend/providers/openai/chat_model.py',
  'backend/providers/openai/embeddings.py',
  'backend/providers/local/vectorstore.py',
  'backend/providers/local/reranker.py',
  'backend/providers/common/errors.py',

  'backend/ingestion/loaders/README.md',
  'backend/ingestion/splitters/README.md',
  'backend/ingestion/pipelines/README.md',

  'backend/queue/dispatcher.py',
  'backend/queue/adapters/local_threadpool.py',
  'backend/queue/adapters/rabbitmq.py',

  'backend/repos/jobs_repo.py',

  'backend/worker/run.py',
  'backend/worker/README.md',

  'backend/tests/unit/test_health.py'
)

# =========================
# Scan current project
# =========================
$rootResolved = (Resolve-Path $Root).Path
$current = Get-CurrentPaths -RootPath $rootResolved -IgnorePatterns $IgnorePatterns
$currDirs  = $current.Dirs
$currFiles = $current.Files

$setCurrDirs  = New-Set $currDirs
$setCurrFiles = New-Set $currFiles
$setExpDirs   = New-Set $ExpectedDirs
$setExpFiles  = New-Set $ExpectedFiles

$MissingDirs  = $ExpectedDirs | Where-Object { -not $setCurrDirs.Contains($_) }
$MissingFiles = $ExpectedFiles | Where-Object { -not $setCurrFiles.Contains($_) }
$ExtraDirs    = $currDirs  | Where-Object { -not $setExpDirs.Contains($_) }
$ExtraFiles   = $currFiles | Where-Object { -not $setExpFiles.Contains($_) }

# =========================
# Write report
# =========================
$sb = New-Object System.Text.StringBuilder
$null = $sb.AppendLine("# Structure Comparison Report")
$null = $sb.AppendLine("")
$null = $sb.AppendLine("Root: " + $rootResolved)
$null = $sb.AppendLine("Date: " + (Get-Date -Format 'yyyy-MM-dd HH:mm'))
$null = $sb.AppendLine("")
$null = $sb.AppendLine("## Summary")
$null = $sb.AppendLine("")
$null = $sb.AppendLine("| Metric | Count |")
$null = $sb.AppendLine("|---|---:|")
$null = $sb.AppendLine("| Current directories | " + $currDirs.Count + " |")
$null = $sb.AppendLine("| Current files | " + $currFiles.Count + " |")
$null = $sb.AppendLine("| Missing expected directories | " + $MissingDirs.Count + " |")
$null = $sb.AppendLine("| Missing expected files | " + $MissingFiles.Count + " |")
$null = $sb.AppendLine("| Extra directories | " + $ExtraDirs.Count + " |")
$null = $sb.AppendLine("| Extra files | " + $ExtraFiles.Count + " |")
$null = $sb.AppendLine("")
$null = $sb.AppendLine("## Missing directories")
$null = $sb.AppendLine("")
if ($MissingDirs.Count -eq 0) { $null = $sb.AppendLine("_None_") } else {
  foreach ($d in ($MissingDirs | Sort-Object)) { $null = $sb.AppendLine("- `" + $d + "`") }
}
$null = $sb.AppendLine("")
$null = $sb.AppendLine("## Missing files")
$null = $sb.AppendLine("")
if ($MissingFiles.Count -eq 0) { $null = $sb.AppendLine("_None_") } else {
  foreach ($f in ($MissingFiles | Sort-Object)) { $null = $sb.AppendLine("- `" + $f + "`") }
}
$null = $sb.AppendLine("")
$null = $sb.AppendLine("## Extra directories (present but not expected)")
$null = $sb.AppendLine("")
if ($ExtraDirs.Count -eq 0) { $null = $sb.AppendLine("_None_") } else {
  foreach ($d in ($ExtraDirs | Sort-Object)) { $null = $sb.AppendLine("- `" + $d + "`") }
}
$null = $sb.AppendLine("")
$null = $sb.AppendLine("## Extra files (present but not expected)")
$null = $sb.AppendLine("")
if ($ExtraFiles.Count -eq 0) { $null = $sb.AppendLine("_None_") } else {
  foreach ($f in ($ExtraFiles | Sort-Object)) { $null = $sb.AppendLine("- `" + $f + "`") }
}
$null = $sb.AppendLine("")
$null = $sb.AppendLine("## Current paths (directories and files)")
$null = $sb.AppendLine("")
$null = $sb.AppendLine("````")
foreach ($d in $currDirs)  { $null = $sb.AppendLine($d) }
foreach ($f in $currFiles) { $null = $sb.AppendLine($f) }
$null = $sb.AppendLine("````")

$reportFull = Join-Path $rootResolved $ReportPath
Set-Content -Path $reportFull -Value $sb.ToString() -NoNewline -Encoding UTF8
Write-Host ("Report written to: " + $reportFull)
