from __future__ import annotations

import locale
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from hfe_character_tool.runtime import resource_root


@dataclass(frozen=True)
class ToolConfig:
    ffdec: Path
    hfworkshop: Path
    projector: Path
    playerglobal: Path
    original_game: Path | None = None
    java: Path | None = None
    javac: Path | None = None


@dataclass(frozen=True)
class ToolResult:
    success: bool
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ToolCheck:
    name: str
    path: Path
    available: bool
    summary: str


def discover_tools(workspace: Path) -> ToolConfig:
    root = resource_root(workspace)
    return ToolConfig(
        ffdec=root / "vendor" / "FFDec" / "ffdec.jar",
        hfworkshop=root / "vendor" / "HFWorkshop" / "HFWorkshop.exe",
        projector=root / "vendor" / "projector" / "SA.exe",
        playerglobal=root / "vendor" / "playerGlobal" / "playerglobal.swc",
        original_game=root / "vendor" / "original_game" / "HFE v1.0.2.exe",
        java=_find_java_tool(workspace, "java"),
        javac=_find_java_tool(workspace, "javac"),
    )


def check_tools(config: ToolConfig) -> dict[str, str]:
    missing: dict[str, str] = {}
    for name, path in (
        ("FFDec", config.ffdec),
        ("HFWorkshop", config.hfworkshop),
        ("SA.exe", config.projector),
        ("playerglobal.swc", config.playerglobal),
    ):
        if not path.is_file():
            missing[name] = f"缺少依赖：{path}"
    if config.java is None:
        missing["java"] = "缺少依赖：未找到 Java 运行时。"
    if config.javac is None:
        missing["javac"] = "缺少依赖：未找到 Java 编译器 javac。"
    return missing


def check_tool_availability(config: ToolConfig) -> tuple[ToolCheck, ...]:
    checks: list[ToolCheck] = []
    for name, path in _tool_paths(config):
        available = path.is_file()
        summary = f"{name} 可用：{path}" if available else f"缺少依赖：{path}"
        checks.append(ToolCheck(name, path, available, summary))
    return tuple(checks)


def dependency_summary(missing: dict[str, str]) -> str:
    if not missing:
        return "外部工具路径检查通过。"
    return "；".join(missing.values())


def summarize_tool_result(tool_name: str, result: ToolResult) -> str:
    if result.success:
        return f"{tool_name} 执行成功。"
    return f"{tool_name} 执行失败，返回码 {result.returncode}。"


def run_tool(command: tuple[str, ...], cwd: Path) -> ToolResult:
    command = _resolve_command(command, cwd)
    try:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, check=False)
    except OSError as exc:
        return ToolResult(success=False, returncode=-1, stdout="", stderr=str(exc))
    return ToolResult(
        success=completed.returncode == 0,
        returncode=completed.returncode,
        stdout=_decode_tool_output(completed.stdout),
        stderr=_decode_tool_output(completed.stderr),
    )


def _decode_tool_output(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    preferred = locale.getpreferredencoding(False)
    encodings = ("utf-8", preferred, "mbcs", "cp936", "cp950")
    tried: set[str] = set()
    for encoding in encodings:
        normalized = encoding.lower()
        if normalized in tried:
            continue
        tried.add(normalized)
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def run_ffdec(config: ToolConfig, args: tuple[str, ...], cwd: Path) -> ToolResult:
    command = _ffdec_command(config.ffdec, args)
    return run_tool(command, cwd)


def run_hfworkshop(config: ToolConfig, args: tuple[str, ...], cwd: Path) -> ToolResult:
    return run_tool((str(config.hfworkshop), *args), cwd)


def wrap_with_projector(projector_exe: Path, swf_path: Path, output_exe: Path) -> None:
    marker = bytes.fromhex("56 34 12 FA")
    swf_bytes = swf_path.read_bytes()
    projector_bytes = projector_exe.read_bytes()
    output_exe.parent.mkdir(exist_ok=True)
    output_exe.write_bytes(
        projector_bytes + swf_bytes + marker + len(swf_bytes).to_bytes(4, "little")
    )


def extract_projector_swf(exe_path: Path, swf_path: Path) -> None:
    marker = bytes.fromhex("56 34 12 FA")
    data = exe_path.read_bytes()
    if len(data) < 8:
        raise ValueError("EXE 文件太小，无法包含 SWF 长度信息。")
    length = int.from_bytes(data[-4:], "little")
    marker_start = len(data) - 8
    swf_start = marker_start - length
    if swf_start < 0 or data[marker_start : marker_start + 4] != marker:
        raise ValueError("未找到 HFE projector SWF marker。")
    swf_bytes = data[swf_start:marker_start]
    if not swf_bytes.startswith((b"FWS", b"CWS", b"ZWS")):
        raise ValueError("EXE 末尾数据不是可识别的 SWF。")
    swf_path.parent.mkdir(exist_ok=True)
    swf_path.write_bytes(swf_bytes)


def _tool_paths(config: ToolConfig) -> tuple[tuple[str, Path], ...]:
    return (
        ("FFDec", config.ffdec),
        ("HFWorkshop", config.hfworkshop),
        ("SA.exe", config.projector),
        ("playerglobal.swc", config.playerglobal),
        ("java", config.java or Path("java")),
        ("javac", config.javac or Path("javac")),
    )


def _ffdec_command(ffdec_path: Path, args: tuple[str, ...]) -> tuple[str, ...]:
    if ffdec_path.suffix.lower() == ".jar":
        return ("java", "-jar", str(ffdec_path), *args)
    return (str(ffdec_path), *args)


def _resolve_command(command: tuple[str, ...], cwd: Path) -> tuple[str, ...]:
    if not command:
        return command
    executable = Path(command[0])
    name = executable.name.lower()
    if executable.parent != Path(".") or name not in {"java", "java.exe", "javac", "javac.exe"}:
        return command
    tool = "javac" if name.startswith("javac") else "java"
    resolved = _find_java_tool(cwd, tool)
    if resolved is None:
        return command
    return (str(resolved), *command[1:])


def _find_java_tool(anchor: Path, tool: str) -> Path | None:
    executable = f"{tool}.exe" if sys.platform == "win32" else tool
    for root in _java_search_roots(anchor):
        for candidate_root in _java_runtime_dirs(root):
            candidate = candidate_root / "bin" / executable
            if candidate.is_file():
                return candidate
    found = shutil.which(executable) or shutil.which(tool)
    return Path(found) if found else None


def _java_search_roots(anchor: Path) -> tuple[Path, ...]:
    roots: list[Path] = [Path(sys.executable).resolve().parent]
    try:
        resolved = anchor.resolve()
    except OSError:
        resolved = anchor
    roots.append(resolved)
    roots.extend(resolved.parents)
    roots.append(resource_root(anchor))
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return tuple(unique)


def _java_runtime_dirs(root: Path) -> tuple[Path, ...]:
    candidates = [root / "runtime" / "jdk", root / "jdk"]
    for parent in (root / "runtime", root):
        try:
            candidates.extend(sorted(parent.glob("jdk-*")))
        except OSError:
            continue
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return tuple(unique)
