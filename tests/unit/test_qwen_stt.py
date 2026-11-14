"""Local worker protocol, segmentation, slot and deployment boundaries."""

import importlib.util
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("qwen_backend", ROOT / "deploy/stt/qwen.py")
qwen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qwen)


def test_protocol_requires_complete_speech():
    def reply(text, reason="stop"):
        return {"choices": [{"finish_reason": reason, "message": {"content": text}}]}

    assert qwen.parse_response(reply("Cantonese<asr_text>香港 RV There Yet")) == "香港 RV There Yet"
    for response in [
        reply("香港"),
        reply("Cantonese<asr_text>"),
        reply("<asr_text>x", "length"),
        reply("<asr_text>x<asr_text>y"),
    ]:
        with pytest.raises(ValueError):
            qwen.parse_response(response)


def test_windows_cover_boundaries_once_and_validate_vad():
    assert qwen.speech_windows([], 16000, 16000) == []
    windows = qwen.speech_windows([[0, 65000]], 65 * 16000, 16000)
    assert windows == [(0, 480000), (480000, 960000), (960000, 1040000)]
    assert qwen.speech_windows([[200, 900], [1000, 1500]], 32000, 16000) == [(0, 27200)]
    for spans in [[[1, 0]], [[0, 3000]], [["bad", 2]], [[0, 500], [200, 600]]]:
        with pytest.raises(ValueError):
            qwen.speech_windows(spans, 32000, 16000)


def test_no_speech_skips_decoder_and_vad_failure_is_error(monkeypatch):
    import sys

    monkeypatch.setitem(
        sys.modules, "soundfile", SimpleNamespace(read=lambda *a, **k: ([0] * 16000, 16000))
    )
    vad = SimpleNamespace(generate=lambda **kw: [{"value": []}])
    worker = qwen.QwenWorker(device="cpu", timeout=1, vad_loader=lambda: vad)
    monkeypatch.setattr(worker, "_detect", lambda *args: vad.generate())
    monkeypatch.setattr(worker, "_load", lambda *args: pytest.fail("decoder invoked on silence"))
    assert worker.transcribe("generated.wav", time.monotonic() + 1, threading.Event()) == [
        {"text": ""}
    ]
    vad.generate = lambda **kw: []
    with pytest.raises(ValueError, match="VAD"):
        worker.transcribe("generated.wav", time.monotonic() + 1, threading.Event())
    assert worker.lock.acquire(blocking=False)
    worker.lock.release()


def test_cancelled_queue_releases_slot_without_loading(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace())
    worker = qwen.QwenWorker(
        device="cpu", timeout=1, vad_loader=lambda: pytest.fail("loaded after cancellation")
    )
    cancel = threading.Event()
    worker.lock.acquire()
    errors = []

    def request():
        try:
            worker.transcribe("generated.wav", time.monotonic() + 1, cancel)
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=request)
    thread.start()
    cancel.set()
    worker.lock.release()
    thread.join(2)
    assert not thread.is_alive() and errors == ["Qwen request cancelled"]
    assert worker.lock.acquire(blocking=False)
    worker.lock.release()


def test_config_device_and_independent_roles(tmp_path):
    from nora.config import SpeechSttConfig, SpeechWakeConfig

    assert SpeechSttConfig().model == "noise-detection"
    assert SpeechWakeConfig().stt_model == "sensevoice"
    assert SpeechSttConfig(qwen={"device": "vulkan"}).qwen.device == "vulkan"
    with pytest.raises(ValueError):
        SpeechSttConfig(qwen={"device": "cuda"})
    spec = importlib.util.spec_from_file_location("stt_config", ROOT / "deploy/stt/config_toml.py")
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    for role in ["stt", "wake"]:
        path = tmp_path / "config.toml"
        key = "model" if role == "stt" else "stt_model"
        path.write_text(f'[speech.{role}]\n{key}="{qwen.MODEL}"\n')
        assert config.qwen_settings(path) == ("cpu", 30, True)


def test_device_overlay_cpu_needs_no_render_node(tmp_path):
    from scripts.komodo_deploy import stt_device_overlay

    config = tmp_path / "config.toml"
    config.write_text('[speech.stt.qwen]\ndevice="cpu"\n')
    assert stt_device_overlay(config, tmp_path, tmp_path / "absent") == []
    config.write_text(
        '[speech.stt]\nmodel="qwen3-asr-hk-0.6b"\n[speech.stt.qwen]\ndevice="vulkan"\n'
    )
    with pytest.raises(FileNotFoundError):
        stt_device_overlay(config, tmp_path, tmp_path / "absent")
    with pytest.raises(ValueError, match="character device"):
        stt_device_overlay(config, tmp_path, config)
    import json
    import os

    args = stt_device_overlay(config, tmp_path, Path("/dev/null"))
    overlay = json.loads(Path(args[1]).read_text())["services"]["stt"]
    assert overlay == {
        "devices": ["/dev/null:/dev/null:rw"],
        "group_add": [str(os.stat("/dev/null").st_gid)],
    }


def test_shutdown_reaps_owned_child_and_refuses_new_work():
    import subprocess
    import sys

    worker = qwen.QwenWorker(device="cpu", timeout=1, vad_loader=None)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    worker.process = process
    assert worker.ready
    worker.close()
    assert process.poll() is not None and not worker.ready
    with pytest.raises(RuntimeError, match="cancelled"):
        worker._remaining(time.monotonic() + 1, threading.Event())


def test_vad_timeout_reaps_detector_and_releases_slot(monkeypatch):
    import sys

    monkeypatch.setitem(
        sys.modules, "soundfile", SimpleNamespace(read=lambda *a, **k: ([0], 16000))
    )
    worker = qwen.QwenWorker(device="cpu", timeout=1, vad_loader=None)
    monkeypatch.setattr(worker, "_detect", lambda *args: (_ for _ in ()).throw(TimeoutError("VAD")))
    with pytest.raises(TimeoutError):
        worker.transcribe("generated.wav", time.monotonic() + 1, threading.Event())
    assert not worker.ready and worker.lock.acquire(blocking=False)
    worker.lock.release()


@pytest.mark.parametrize("fault", ["project", "image", "memory", "config-mount"])
def test_final_compose_rejects_changed_invariants(tmp_path, fault):
    from scripts.komodo_deploy import validate_final_compose

    resolved = {
        "name": "nora",
        "services": {
            "nora": {
                "image": "pinned",
                "volumes": [
                    {"target": "/app/config.toml", "source": str(tmp_path / "config.toml")}
                ],
            },
            "stt": {"image": "stt-pinned", "mem_limit": 8 * 1024**3, "memswap_limit": 8 * 1024**3},
        },
    }
    images = {"nora": "pinned", "stt": "stt-pinned"}
    validate_final_compose(resolved, images, tmp_path, "nora")
    if fault == "project":
        resolved["name"] = "foreign"
    elif fault == "image":
        resolved["services"]["stt"]["image"] = "untrusted"
    elif fault == "memory":
        resolved["services"]["stt"]["memswap_limit"] = -1
    else:
        resolved["services"]["nora"]["volumes"] = []
    with pytest.raises(ValueError):
        validate_final_compose(resolved, images, tmp_path, "nora")


def test_native_compose_renders_optional_device_and_numeric_group(tmp_path):
    import json
    import shutil
    import subprocess

    from scripts.komodo_deploy import stt_device_overlay, validate_final_compose

    if not shutil.which('docker'):
        pytest.skip('Docker Compose CLI unavailable')
    config = tmp_path / 'config.toml'
    config.write_text('[speech.wake]\nstt_model="qwen3-asr-hk-0.6b"\n'
                      '[speech.stt.qwen]\ndevice="vulkan"\n')
    base = tmp_path / 'compose.json'
    base.write_text(json.dumps({'name': 'nora', 'services': {
        'nora': {'image': 'nora:pinned', 'volumes': [f'{config}:/app/config.toml:ro']},
        'stt': {'image': 'stt:pinned', 'mem_limit': '8g', 'memswap_limit': '8g'}}}))
    overlay = stt_device_overlay(config, tmp_path, Path('/dev/null'))
    result = subprocess.run(  # noqa: S603 -- fixed local Compose command, generated inputs
        ['docker', 'compose', '-f', str(base), *overlay,
                             'config', '--format', 'json'], capture_output=True, check=True)
    resolved = json.loads(result.stdout)
    validate_final_compose(resolved, {'nora': 'nora:pinned', 'stt': 'stt:pinned'}, tmp_path, 'nora')
    assert resolved['services']['stt']['group_add']
    assert resolved['services']['stt']['devices'][0]['source'] == '/dev/null'


def test_simultaneous_warm_loads_one_worker_with_cpu_offload_disabled(monkeypatch):
    worker = qwen.QwenWorker(device='cpu', timeout=1, vad_loader=None)
    monkeypatch.setattr(worker, '_artifact', lambda *args: 'verified-model.gguf')
    monkeypatch.setattr(worker, '_start_vad', lambda *args: None)
    calls = []
    process = SimpleNamespace(poll=lambda: None)
    def start(args, **kwargs):
        calls.append(args)
        time.sleep(.05)
        return process
    monkeypatch.setattr(qwen.subprocess, 'Popen', start)
    class Health:
        def __enter__(self):
            import io
            return io.BytesIO(b'{"status":"ok"}')
        def __exit__(self, *args):
            pass
    def health_response(url, **kwargs):
        assert url == worker.url + '/health'
        return Health()
    monkeypatch.setattr(worker.http, 'open', health_response)
    threads = [threading.Thread(target=worker.warm) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    assert len(calls) == 1
    assert calls[0][calls[0].index('-ngl') + 1] == '0'
    assert '--no-mmproj-offload' in calls[0]
    assert calls[0][calls[0].index('--device') + 1] == 'none'
    worker.process = None
    worker.log.close()


def test_long_speech_prefers_real_pause_to_splitting_words():
    assert qwen.speech_windows([[0, 21000], [22000, 43000]], 43 * 16000, 16000) == [
        (0, 344000), (344000, 688000)]


def test_detector_spanning_a_pause_still_cuts_in_the_pause():
    rate = 1000
    audio = [0.5] * 21000 + [0.] * 1000 + [0.5] * 21000
    windows = qwen.speech_windows([[0, 43000]], len(audio), rate, audio=audio)
    assert len(windows) == 2
    cut = windows[0][1]
    assert 21000 < cut < 22000
    assert windows == [(0, cut), (cut, len(audio))]
    assert all(end - start <= 30 * rate for start, end in windows)


def test_local_audio_transport_does_not_use_environment_proxies(monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://example.invalid:1234')
    worker = qwen.QwenWorker(device='cpu', timeout=1, vad_loader=None)
    assert not any(getattr(handler, 'proxies', {}) for handler in worker.http.handlers)
