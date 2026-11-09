<#
.SYNOPSIS
    Manual trigger utility for PattN Proxy Aggregator pipeline (GitHub Actions or Local Run).

.DESCRIPTION
    Triggers the GitHub Actions 'update-subs.yml' workflow using the GitHub CLI (`gh`),
    or executes the aggregation pipeline locally with optional private BPB integration
    and local PattN GUI database sync.

.PARAMETER Local
    Run the aggregation pipeline locally instead of triggering GitHub Actions.

.PARAMETER NoHealthcheck
    Disable candidate node socket healthcheck.

.PARAMETER NoPattnVariants
    Disable generating specialized PattN bypass variants (F&F, IPv6-OOB, SNI-Spoof).

.PARAMETER MaxTestNodes
    Maximum number of prioritized nodes to test during healthcheck (default: 1000).

.PARAMETER SyncPattnDb
    When -Local is specified, also sync subscription URLs into PattN guiNDB.db.

.PARAMETER SkipPrivateGist
    Skip publishing private BPB subscriptions to GitHub Secret Gist.

.EXAMPLE
    .\scripts\trigger_update.ps1
    Triggers GitHub Actions update-subs.yml workflow with default parameters.

.EXAMPLE
    .\scripts\trigger_update.ps1 -Local -SyncPattnDb
    Runs the pipeline locally including private sources, and updates PattN GUI database.
#>

[CmdletBinding()]
param (
    [switch]$Local,
    [switch]$NoHealthcheck,
    [switch]$NoPattnVariants,
    [int]$MaxTestNodes = 1000,
    [switch]$SyncPattnDb,
    [switch]$DisableRedundant,
    [switch]$SkipPrivateGist
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

Set-Location -Path $RepoRoot

if ($Local) {
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host " [PattN] Running Pipeline Locally" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan

    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $PythonExe) {
        Write-Error "Python was not found in PATH. Please install Python 3.10+."
        exit 1
    }

    $MainArgs = @("-m", "src.main", "--include-private", "--max-test-nodes", "$MaxTestNodes")
    if ($NoHealthcheck) {
        $MainArgs += "--no-healthcheck"
    }
    if ($NoPattnVariants) {
        $MainArgs += "--no-pattn-variants"
    }

    Write-Host "Executing: python $($MainArgs -join ' ')" -ForegroundColor Yellow
    & python $MainArgs

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Pipeline run failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }

    if ($SyncPattnDb) {
        Write-Host "`nSyncing subscriptions to local PattN database..." -ForegroundColor Cyan
        $SyncArgs = @("scripts/sync_to_pattn.py", "--use-local-private")
        if ($DisableRedundant) {
            $SyncArgs += "--disable-redundant"
        }
        & python $SyncArgs
    }

    Write-Host "`n[PattN] Local run completed successfully." -ForegroundColor Green
}
else {
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host " [PattN] Triggering GitHub Actions Workflow" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan

    $GhCli = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $GhCli) {
        Write-Warning "GitHub CLI ('gh') is not installed or not in PATH."
        Write-Host "You can run locally with '-Local', or install GitHub CLI: winget install GitHub.cli" -ForegroundColor Yellow
        exit 1
    }

    $RunHealthcheck = if ($NoHealthcheck) { "false" } else { "true" }
    $GenVariants = if ($NoPattnVariants) { "false" } else { "true" }
    $SyncGist = if ($SkipPrivateGist) { "false" } else { "true" }

    $GhArgs = @(
        "workflow", "run", "update-subs.yml",
        "-f", "run_healthcheck=$RunHealthcheck",
        "-f", "generate_pattn_variants=$GenVariants",
        "-f", "max_test_nodes=$MaxTestNodes",
        "-f", "sync_private_gist=$SyncGist"
    )

    Write-Host "Dispatching workflow: gh $($GhArgs -join ' ')" -ForegroundColor Yellow
    & gh $GhArgs

    if ($LASTEXITCODE -eq 0) {
        Write-Host "Workflow triggered successfully!" -ForegroundColor Green
        Start-Sleep -Seconds 2
        Write-Host "`nRecent workflow runs:" -ForegroundColor Cyan
        & gh run list --workflow=update-subs.yml --limit 3
    }
    else {
        Write-Error "Failed to dispatch GitHub Actions workflow. Exit code: $LASTEXITCODE"
    }
}
