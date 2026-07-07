# HFE Character Tool

HFE Character Tool is a Windows GUI helper for creating and exporting custom
Hero Fighter/HFE characters through a GUI. It focuses on target-aware character
templates, texture selection, item-frame edits, validation, and export packaging.

This repository is the public tool-only source package. It does not include
generated game builds, local projects, external tool binaries, game binaries, or
private workspace notes.

## Features

- Create and manage custom character projects.
- Select a target HFE EXE/SWF before choosing role templates and item options.
- Read available role templates, texture parts, actions, frames, and item data
  from the selected target where possible.
- Edit HP, MP, defense, names, texture choices, and item spawns on selected
  action frames.
- Validate project data before export.
- Export modified EXE/SWF builds into an ignored local output folder.
- Randomize character stats, textures, and frame item spawns for quick experiments.

## Repository Layout

```text
src/hfe_character_tool/   Python package and GUI source
tests/                    pytest test suite
docs/                     user-facing Markdown docs
scripts/                  local build helper scripts
vendor/README.md          expected external dependency layout
底图.jpg                  GUI background image
图标.jpg                  GUI icon source image
```

## External Dependencies

The repository intentionally does not ship third-party binaries or game files.
Prepare them locally before exporting real game builds:

```text
vendor/FFDec/ffdec.jar
vendor/HFWorkshop/HFWorkshop.exe
vendor/projector/SA.exe
vendor/playerGlobal/playerglobal.swc
vendor/original_game/HFE v1.0.2.exe
```

Java/JDK is also required for FFDec patching. You can either install Java on the
system PATH, or place a JDK under one of these local folders:

```text
runtime/jdk/
runtime/jdk-*/
jdk/
jdk-*/
```

See `docs/dependencies.md` for details.

## Development Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run checks:

```powershell
python -m pytest -q
python -m mypy src tests
python -m ruff check src tests
```

Run from source:

```powershell
$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"
python -m hfe_character_tool
```

## Windows Packaging

Install packaging dependencies:

```powershell
python -m pip install -e ".[package]"
```

Build a local one-file Windows GUI executable:

```powershell
.\scripts\build_windows.ps1
```

The build output is written to `output/tool_dist/`, which is ignored by Git.

## Publishing Notes

Before creating a GitHub release:

- Keep `vendor/`, `runtime/`, `projects/`, and `output/` out of Git.
- Do not publish locally exported game EXEs/SWFs.
- Do not publish personal workspace notes or generated test projects.
- Run the full test, type-check, lint, and packaged-GUI smoke checks.

## License

Choose and add a license before making this repository public.
