param(
    [string]$DbPath = "data/analysis_workspace.sqlite",
    [string]$CanonDbPath = "data/canon_workspace.sqlite",
    [string]$RunId = "",
    [int]$Limit = 0,
    [int]$ProgressEvery = 25,
    [int]$Zoom = 18
)

$ErrorActionPreference = "Stop"
$scriptPath = Join-Path $PSScriptRoot "fill_missing_parcel_visual_features.py"
$argsList = @(
    $scriptPath,
    "--db-path", $DbPath,
    "--canon-db-path", $CanonDbPath,
    "--limit", "$Limit",
    "--progress-every", "$ProgressEvery",
    "--zoom", "$Zoom"
)

if ($RunId.Trim().Length -gt 0) {
    $argsList += @("--run-id", $RunId)
}

python @argsList
