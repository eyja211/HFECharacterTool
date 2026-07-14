# HFE Character Customization Tool

[中文](README.md) | **English** | [日本語](README.ja.md)

A Windows GUI application for creating and exporting custom HFE / Hero Fighter
characters. It is intended to let players who are unfamiliar with FFDec,
HFWorkshop, SPT/LMI data, and SWF patching create a character through a guided
interface: choose a template, configure textures and item frames, validate the
result, and export it.

This repository contains only the tool source code, tests, and public
documentation. It does not include original game files, third-party binaries,
local player projects, or exported game files.

## Key Features

- Create and manage custom character projects.
- Choose the target HFE EXE/SWF first, then load usable character templates and
  item data from that target build.
- Edit character ID, name, Chinese name, description, HP, MP, defense, and
  other core properties.
- Configure item spawning by character action and frame.
- Choose texture sources for character parts.
- Validate before export and block clearly incompatible configurations.
- Add characters to targets that have already been modified.
- Generate randomized character attributes, texture combinations, and item-frame
  configurations.

## Interface Preview

### Character Editing

![Character editing page](docs/images/edit-character.png)

### Textures and Skills

![Textures and skills page](docs/images/items-and-textures.png)

### Validation and Export

![Validation and export page](docs/images/validation-export.png)

## Repository Layout

```text
src/hfe_character_tool/   Tool source code
tests/                    Automated tests
docs/                     User documentation
scripts/                  Local packaging scripts
vendor/README.md          External dependency layout notes
pyproject.toml            Python project configuration
```

## Not Included

The following files are intentionally excluded and must be prepared locally:

- Original or modified HFE / Hero Fighter game files.
- FFDec, HFWorkshop, SA.exe, playerglobal.swc, and other third-party tools.
- A Java/JDK runtime.
- Local character projects under `projects/`.
- Exported EXE/SWF files, caches, and logs under `output/`.

## External Dependency Layout

Before exporting a game build, prepare dependencies in the repository root
using the following layout:

```text
vendor/
  FFDec/
    ffdec.jar
  HFWorkshop/
    HFWorkshop.exe
  projector/
    SA.exe
  playerGlobal/
    playerglobal.swc
  original_game/
    HFE v1.0.2.exe
```

Java/JDK can be installed on the system PATH or placed in one of these nearby
directories:

```text
runtime/jdk/
runtime/jdk-*/
jdk/
jdk-*/
```

See `docs/dependencies.md` for more detail.

## Development Environment

Python 3.8 or newer is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run tests and checks:

```powershell
python -m pytest -q
python -m mypy src tests
python -m ruff check src tests
```

Run directly from source:

```powershell
$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"
python -m hfe_character_tool
```

## Build a Windows EXE Locally

Install the packaging dependency first:

```powershell
python -m pip install -e ".[package]"
```

Run the packaging script:

```powershell
.\scripts\build_windows.ps1
```

The build output is written to `output/tool_dist/`. The `output/` directory is
ignored by Git by default.

## Before Publishing

- Do not commit `vendor/`, `runtime/`, `projects/`, or `output/`.
- Do not commit exported game EXE/SWF files.
- Do not commit third-party tools, original game files, or personal local
  configuration.
- Run at least `pytest`, `mypy`, and `ruff` before publishing.

## License

Add a license file that matches the intended distribution terms before a public
release.
