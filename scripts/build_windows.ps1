$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Dist = Join-Path $Root "output\tool_dist"
$Work = Join-Path $Root "output\tool_build"
$Spec = Join-Path $Root "output\tool_spec"
$Icon = Join-Path $Root "output\tool_icon.ico"

New-Item -ItemType Directory -Path $Dist, $Work, $Spec, (Split-Path $Icon) -Force | Out-Null

python -c "from pathlib import Path; from PIL import Image; root=Path(r'$Root'); icon=Path(r'$Icon'); img=Image.open(root/'图标.jpg').convert('RGBA'); img.save(icon, sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"

python -m PyInstaller `
  --noconfirm `
  --onefile `
  --windowed `
  --name "HFE角色定制工具" `
  --icon "$Icon" `
  --distpath "$Dist" `
  --workpath "$Work" `
  --specpath "$Spec" `
  --paths (Join-Path $Root "src") `
  --add-data "$(Join-Path $Root 'vendor');vendor" `
  --add-data "$(Join-Path $Root '底图.jpg');." `
  --add-data "$(Join-Path $Root '图标.jpg');." `
  (Join-Path $Root "src\hfe_character_tool\__main__.py")

Write-Output "Build output: $Dist"
