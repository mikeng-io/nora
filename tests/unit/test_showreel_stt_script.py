"""Exercise the host STT prefix without SSH, Docker or an STT request."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "showreel/pipeline/nora_stt.sh"


@pytest.mark.parametrize("child_rc", [0, 23])
def test_stt_host_exec_preserves_binary_stdin_and_status(tmp_path: Path, child_rc: int) -> None:
    runtime = tmp_path / "workspace/nora"
    (runtime / "scripts").mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/secret_delivery.py", runtime / "scripts/secret_delivery.py")
    (runtime / "secret.toml").write_text('[credentials]\nPOSTGRES_PASSWORD="synthetic-stt"\n')
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python3").symlink_to(sys.executable)
    ssh = bindir / "ssh"
    ssh.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "assert len(sys.argv) == 3 and sys.argv[1] == 'norty'\n"
        "os.execv('/bin/bash', ['bash', '-c', sys.argv[2]])\n"
    )
    docker = bindir / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1:3] != ['compose', 'exec']:\n"
        "    sys.exit(2)\n"
        "if not os.environ.get('POSTGRES_PASSWORD'):\n"
        "    print('required interpolation missing', file=sys.stderr)\n"
        "    sys.exit(14)\n"
        "assert os.environ['POSTGRES_PASSWORD'] == 'synthetic-stt'\n"
        "assert Path.cwd() == Path.home() / 'workspace/nora'\n"
        "assert sys.argv[3:7] == ['-T', 'nora', '/app/.venv/bin/python', '-c']\n"
        "assert len(sys.argv) == 8\n"
        "request = sys.argv[7]\n"
        "compile(request, '<stt-request>', 'exec')\n"
        "assert 'http://stt:8000/v1/audio/transcriptions' in request\n"
        "assert 'data = sys.stdin.buffer.read()' in request\n"
        "assert 'noise-detection' in request and 'yue' in request\n"
        "Path(os.environ['CAPTURE']).write_bytes(sys.stdin.buffer.read())\n"
        "print('synthetic transcript')\n"
        "sys.exit(int(os.environ['CHILD_RC']))\n"
    )
    ssh.chmod(0o755)
    docker.chmod(0o755)
    audio = b"ID3\x00\xff\xfe\r\nsynthetic MP3\x00\x80"
    capture = tmp_path / "captured.mp3"
    result = subprocess.run(  # noqa: S603 -- fixed local script, fake SSH/Docker
        ["/bin/bash", str(SCRIPT)],
        input=audio,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": str(bindir), "CAPTURE": str(capture),
             "CHILD_RC": str(child_rc)},
        check=False,
    )
    assert result.returncode == child_rc, result.stderr
    assert result.stdout == b"synthetic transcript\n"
    assert result.stderr == b""
    assert capture.read_bytes() == audio
    assert sorted(p.name for p in runtime.iterdir()) == ["scripts", "secret.toml"]
