from __future__ import annotations

import sys
from pathlib import Path

from hfe_character_tool.tools import (
    ToolConfig,
    _find_java_tool,
    check_tool_availability,
    extract_projector_swf,
    run_ffdec,
    run_hfworkshop,
    run_tool,
    summarize_tool_result,
)


def test_check_tool_availability_reports_each_dependency(tmp_path: Path) -> None:
    ffdec = tmp_path / "ffdec.exe"
    ffdec.write_bytes(b"x")
    java = tmp_path / "runtime" / "jdk" / "bin" / _java_exe_name("java")
    java.parent.mkdir(parents=True)
    java.write_bytes(b"x")
    config = ToolConfig(
        ffdec=ffdec,
        hfworkshop=tmp_path / "missing_hfw.exe",
        projector=tmp_path / "missing_sa.exe",
        playerglobal=tmp_path / "missing_playerglobal.swc",
        java=java,
        javac=tmp_path / "runtime" / "jdk" / "bin" / _java_exe_name("javac"),
    )

    checks = check_tool_availability(config)

    assert [check.name for check in checks] == [
        "FFDec",
        "HFWorkshop",
        "SA.exe",
        "playerglobal.swc",
        "java",
        "javac",
    ]
    assert checks[0].available
    assert checks[4].available
    assert not checks[5].available
    assert "缺少依赖" in checks[1].summary


def test_find_java_tool_prefers_bundled_runtime(tmp_path: Path) -> None:
    bundled = tmp_path / "runtime" / "jdk" / "bin" / _java_exe_name("javac")
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"x")

    assert _find_java_tool(tmp_path, "javac") == bundled


def test_find_java_tool_accepts_versioned_runtime_dir(tmp_path: Path) -> None:
    bundled = tmp_path / "runtime" / "jdk-21.0.9+10" / "bin" / _java_exe_name("java")
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"x")

    assert _find_java_tool(tmp_path, "java") == bundled


def test_run_tool_preserves_success_and_failure_output(tmp_path: Path) -> None:
    success = run_tool((sys.executable, "-c", "print('ok')"), tmp_path)
    failure = run_tool(
        (
            sys.executable,
            "-c",
            "import sys; print('bad', file=sys.stderr); sys.exit(7)",
        ),
        tmp_path,
    )

    assert success.success
    assert success.stdout.strip() == "ok"
    assert not failure.success
    assert failure.returncode == 7
    assert "bad" in failure.stderr
    assert summarize_tool_result("fake", failure) == "fake 执行失败，返回码 7。"


def test_run_tool_decodes_non_utf8_process_output(tmp_path: Path) -> None:
    result = run_tool(
        (
            sys.executable,
            "-c",
            "import sys; "
            "sys.stdout.buffer.write(b'\\xb4\\xed\\xce\\xf3'); "
            "sys.stderr.buffer.write(b'\\xb1\\xe0\\xd2\\xeb'); "
            "sys.exit(7)",
        ),
        tmp_path,
    )

    assert not result.success
    assert result.returncode == 7
    assert result.stdout
    assert result.stderr
    assert "\ufffd" not in result.stdout
    assert "\ufffd" not in result.stderr


def test_run_tool_missing_command_returns_diagnostic(tmp_path: Path) -> None:
    result = run_tool((str(tmp_path / "missing-command.exe"),), tmp_path)

    assert not result.success
    assert result.returncode == -1
    assert result.stderr


def test_ffdec_and_hfworkshop_wrappers_keep_output(tmp_path: Path) -> None:
    config = ToolConfig(
        ffdec=Path(sys.executable),
        hfworkshop=Path(sys.executable),
        projector=tmp_path / "SA.exe",
        playerglobal=tmp_path / "playerglobal.swc",
    )

    ffdec_result = run_ffdec(config, ("-c", "print('ffdec-wrapper')"), tmp_path)
    hfworkshop_result = run_hfworkshop(config, ("-c", "print('hfw-wrapper')"), tmp_path)

    assert ffdec_result.success
    assert ffdec_result.stdout.strip() == "ffdec-wrapper"
    assert hfworkshop_result.success
    assert hfworkshop_result.stdout.strip() == "hfw-wrapper"


def test_extract_projector_swf_reads_marker_and_length(tmp_path: Path) -> None:
    exe = tmp_path / "game.exe"
    swf = tmp_path / "out" / "game.swf"
    swf_bytes = b"FWS" + b"\x00" * 5
    exe.write_bytes(
        b"PROJECTOR" + swf_bytes + bytes.fromhex("56 34 12 FA") + (8).to_bytes(4, "little")
    )

    extract_projector_swf(exe, swf)

    assert swf.read_bytes() == swf_bytes


def test_extract_projector_swf_rejects_missing_marker(tmp_path: Path) -> None:
    exe = tmp_path / "game.exe"
    swf = tmp_path / "game.swf"
    exe.write_bytes(b"not-a-projector")

    import pytest

    with pytest.raises(ValueError, match="marker"):
        extract_projector_swf(exe, swf)


def _java_exe_name(tool: str) -> str:
    return f"{tool}.exe" if sys.platform == "win32" else tool
