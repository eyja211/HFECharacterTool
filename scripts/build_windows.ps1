$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Dist = Join-Path $Root "output\tool_dist"
$Work = Join-Path $Root "output\tool_build"
$Spec = Join-Path $Root "output\tool_spec"

New-Item -ItemType Directory -Path $Dist, $Work, $Spec -Force | Out-Null

python -m PyInstaller `
  --noconfirm `
  --onefile `
  --windowed `
  --name "HFE角色定制工具" `
  --distpath "$Dist" `
  --workpath "$Work" `
  --specpath "$Spec" `
  --paths (Join-Path $Root "src") `
  --add-data "$(Join-Path $Root 'vendor');vendor" `
  (Join-Path $Root "src\hfe_character_tool\__main__.py")

Write-Output "Build output: $Dist"
