<#
.SYNOPSIS
    Tests proxy configs directly from your Iranian ISP using local Xray core.

.DESCRIPTION
    Disconnect your personal VPN (including TUN mode) before running this script.
    It loads candidate feeds (locally or from GitHub), runs batched real-traffic
    Xray verification via D:\PattN-windows-64-desktop\...\xray.exe, saves live
    survivors to local_subs/iran_verified.txt, and optionally syncs them into
    your PattN database.

.EXAMPLE
    .\scripts\check_from_iran.ps1
    Runs 2-round Xray verification on up to 500 prioritized nodes and syncs to PattN DB.

.EXAMPLE
    .\scripts\check_from_iran.ps1 -FromGitHub -MaxNodes 800
#>

[CmdletBinding()]
param (
    [int]$MaxNodes = 500,
    [int]$Rounds = 2,
    [int]$BatchSize = 48,
    [double]$Timeout = 5.0,
    [switch]$FromGitHub,
    [switch]$NoDbSync,
    [switch]$Force,
    [string]$XrayBin = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

Set-Location -Path $RepoRoot

$ArgsList = @(
    "scripts/test_iran_reachability.py",
    "--max-nodes", "$MaxNodes",
    "--rounds", "$Rounds",
    "--batch-size", "$BatchSize",
    "--timeout", "$Timeout"
)

if ($FromGitHub) { $ArgsList += "--from-github" }
if (-not $NoDbSync) { $ArgsList += "--sync-pattn-db" }
if ($Force) { $ArgsList += "--force" }
if ($XrayBin) { $ArgsList += @("--xray-bin", $XrayBin) }

Write-Host "Executing: python $($ArgsList -join ' ')" -ForegroundColor Cyan
& python $ArgsList
exit $LASTEXITCODE
