# Dependencies

The tool source is open in this package, but real HFE export requires local
third-party tools and game files. These files are not included in the repository.

## Required Layout

Place dependencies under the repository root:

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

The GUI and export pipeline discover these paths automatically.

## Java/JDK

FFDec patching needs Java and `javac`. Either install a JDK on PATH, or place a
JDK in one of these local folders:

```text
runtime/jdk/
runtime/jdk-*/
jdk/
jdk-*/
```

## What Not To Commit

Keep these folders local and ignored:

```text
vendor/
runtime/
projects/
output/
```

They may contain third-party binaries, local character projects, or generated
game files.
