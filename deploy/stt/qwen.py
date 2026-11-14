"""Pinned local Cantonese ASR worker, owned by the STT service."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import multiprocessing
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "qwen3-asr-hk-0.6b"
REVISION = "9b67dd08958900879ef34939485bb977a308a2f5"
FILES = {
    "Qwen3-ASR-0.6B-Cantonese-HK-Q8_0.gguf": (
        "d94dbc7eddd4c241f699bc4436f1708a84"
        "33c9e113946dd5478d9f1e2bb468b5"
    ),
    "Qwen3-ASR-0.6B-Cantonese-HK.mmproj-Q8_0.gguf": (
        "e9140ebcb5b9f5c666e01884da51914bc"
        "9e204c1c268c142868ebd859eb6448f"
    ),
}


def parse_response(payload: dict) -> str:
    choice = payload["choices"][0]
    raw = choice["message"]["content"]
    if choice.get("finish_reason") != "stop" or not isinstance(raw, str):
        raise ValueError("Qwen incomplete response")
    if raw.count("<asr_text>") != 1:
        raise ValueError("Qwen missing ASR marker")
    text = raw.split("<asr_text>", 1)[1].strip()
    if not text or "<" in text or ">" in text:
        raise ValueError("Qwen malformed speech response")
    return text


def speech_windows(spans: list, samples: int, rate: int, audio=None) -> list[tuple[int, int]]:
    """Merge padded VAD spans; split contiguously at the supported duration."""
    merged: list[tuple[int, int]] = []
    previous = 0
    pauses = []
    for span in spans:
        if (
            not isinstance(span, (list, tuple))
            or len(span) != 2
            or not all(isinstance(v, (int, float)) for v in span)
        ):
            raise ValueError("invalid VAD span")
        start, end = span
        if not 0 <= start < end <= samples * 1000 / rate + 1 or start < previous:
            raise ValueError("invalid VAD bounds")
        if previous and start > previous:
            pauses.append(int((previous + start) * rate / 2000))
        previous = end
        lo, hi = (
            max(0, int(start * rate / 1000) - rate // 5),
            min(samples, int(end * rate / 1000) + rate // 5),
        )
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    if not merged:
        return []
    # Preserve context across short pauses; split at detector-confirmed silence
    # when available near the bound, otherwise partition continuous speech.
    lo, end = merged[0][0], merged[-1][1]
    gaps = pauses
    windows = []
    while end - lo > 30 * rate:
        bound = lo + 30 * rate
        cuts = [point for point in gaps if lo + rate <= point <= bound]
        cut = max(cuts) if cuts else bound
        if not cuts and audio is not None:
            hop = max(1, rate // 10)
            # A detector may span a pause. Partition at the quietest short
            # frame; this chooses a cut only, never a speech/no-speech verdict.
            frames = range(max(lo + rate, bound - 15 * rate), bound - hop + 1, hop)
            energies = [(sum(float(x) ** 2 for x in audio[start:start + hop:max(1, rate // 1000)]),
                         -(start + hop // 2)) for start in frames]
            if energies:
                cut = -min(energies)[1]
        windows.append((lo, cut))
        lo = cut
    windows.append((lo, end))
    return windows



def _vad_main(connection, loader):
    try:
        vad = loader()
        connection.send(None)
        while True:
            path = connection.recv()
            connection.send(vad.generate(input=path))
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- child reports failure by EOF
        connection.close()


class QwenWorker:
    def __init__(self, *, device: str, timeout: float, vad_loader):
        if device not in ("cpu", "vulkan"):
            raise ValueError("Qwen device must be cpu or vulkan")
        self.device, self.timeout, self.vad_loader = device, timeout, vad_loader
        self.lock = threading.Lock()
        self.process = None
        self.vad = None
        self.vad_process = None
        self.closed = False
        self.log = None
        self.url = "http://127.0.0.1:18181"
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @property
    def ready(self):
        process = self.process
        return process is not None and process.poll() is None

    def _remaining(self, deadline, cancel):
        if cancel.is_set() or self.closed:
            raise RuntimeError("Qwen request cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Qwen request deadline")
        return remaining

    def _stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None

    def close(self):
        self.closed = True
        # The request owns its slot until native work returns; no resource is
        # destroyed beneath VAD or an HTTP request still using it.
        with self.lock:
            self._stop()
            self._stop_vad()

    def _stop_vad(self):
        if self.vad_process is not None:
            self.vad_process.terminate()
            self.vad_process.join(timeout=2)
            if self.vad_process.is_alive():
                self.vad_process.kill()
                self.vad_process.join(timeout=2)
            self.vad_process = None
        if self.vad is not None:
            self.vad.close()
            self.vad = None

    def _start_vad(self, deadline, cancel):
        if self.vad is not None:
            return
        context = multiprocessing.get_context("spawn")
        self.vad, child = context.Pipe()
        self.vad_process = context.Process(target=_vad_main, args=(child, self.vad_loader))
        self.vad_process.start()
        child.close()
        ready_deadline = min(deadline, time.monotonic() + 180)
        while not self.vad.poll(min(.1, self._remaining(ready_deadline, cancel))):
            if not self.vad_process.is_alive():
                raise RuntimeError("VAD worker exited before readiness")
        if self.vad.recv() is not None:
            raise RuntimeError("invalid VAD readiness")

    def _detect(self, wav_path, deadline, cancel):
        self._start_vad(deadline, cancel)
        self.vad.send(wav_path)
        while not self.vad.poll(min(.1, self._remaining(deadline, cancel))):
            if not self.vad_process.is_alive():
                raise RuntimeError("VAD worker exited")
        return self.vad.recv()

    def _artifact(self, name, digest, deadline, cancel):
        directory = Path.home() / ".cache/huggingface/nora-qwen" / REVISION
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        if not path.exists():
            url = f"https://huggingface.co/joe2643/Qwen3-ASR-0.6B-Cantonese-HK-GGUF/resolve/{REVISION}/{name}"
            partial = path.with_suffix(".partial")
            try:
                with (
                    urllib.request.urlopen(
                        url, timeout=min(5, self._remaining(deadline, cancel))
                    ) as response,
                    partial.open("wb") as target,
                ):
                    while chunk := response.read(1024 * 1024):
                        self._remaining(deadline, cancel)
                        target.write(chunk)
                partial.replace(path)
            finally:
                partial.unlink(missing_ok=True)
        with path.open("rb") as fh:
            actual = hashlib.file_digest(fh, "sha256").hexdigest()
        if actual != digest:
            path.unlink()
            raise ValueError("Qwen model checksum mismatch")
        return str(path)

    def _load(self, deadline, cancel):
        if self.ready:
            return
        paths = [self._artifact(name, digest, deadline, cancel) for name, digest in FILES.items()]
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = f"/opt/llama/{self.device}"
        if self.device == "vulkan":
            env["VK_DRIVER_FILES"] = "/usr/share/vulkan/icd.d/radeon_icd.json"
            probe = subprocess.run(
                ["/usr/bin/vulkaninfo", "--summary"],
                env=env,
                capture_output=True,
                timeout=min(10, self._remaining(deadline, cancel)),
                check=True,
            )
            if b"Radeon" not in probe.stdout or b"llvmpipe" in probe.stdout:
                raise RuntimeError("Qwen requires a physical Radeon Vulkan device")
        binary = f"/opt/llama/{self.device}/llama-server"
        args = [
            binary,
            "-m",
            paths[0],
            "--mmproj",
            paths[1],
            "--host",
            "127.0.0.1",
            "--port",
            "18181",
            "-c",
            "8192",
            "-np",
            "1",
            "-t",
            "4",
            "-tb",
            "4",
            "-ngl",
            "99" if self.device == "vulkan" else "0",
        ]
        if self.device == "cpu":
            args.extend(["--device", "none", "--no-mmproj-offload"])
        else:
            args.extend(["--device", "Vulkan0", "--mmproj-device", "Vulkan0"])
        self._stop()
        self.log = tempfile.TemporaryFile()  # noqa: SIM115 -- owned until child reaped
        self.process = subprocess.Popen(  # noqa: S603 -- fixed executable and verified paths
            args, env=env, stdout=self.log, stderr=self.log
        )
        ready_deadline = min(deadline, time.monotonic() + 180)
        while True:
            remaining = self._remaining(ready_deadline, cancel)
            if self.process.poll() is not None:
                raise RuntimeError("Qwen worker exited before readiness")
            try:
                with self.http.open(
                    self.url + "/health", timeout=min(1, remaining)
                ) as response:
                    if json.load(response).get("status") == "ok":
                        return
            except (OSError, ValueError):
                cancel.wait(min(0.1, remaining))

    def warm(self):
        with self.lock:
            try:
                deadline, cancel = time.monotonic() + 600, threading.Event()
                self._load(deadline, cancel)
                self._start_vad(deadline, cancel)
            except BaseException:
                self._stop()
                self._stop_vad()
                raise

    def _infer(self, request, deadline, cancel):
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import TimeoutError as FutureTimeout

        executor = ThreadPoolExecutor(max_workers=1)
        def send():
            with self.http.open(
                request, timeout=self._remaining(deadline, cancel)
            ) as response:
                return json.load(response)
        future = executor.submit(send)
        try:
            while True:
                remaining = self._remaining(deadline, cancel)
                try:
                    return future.result(timeout=min(.1, remaining))
                except FutureTimeout:
                    if future.done():
                        return future.result()
        finally:
            if not future.done():
                self._stop()
            executor.shutdown(wait=True, cancel_futures=True)

    def transcribe(self, wav_path, deadline, cancel):
        import soundfile as sf

        while not self.lock.acquire(timeout=min(.1, self._remaining(deadline, cancel))):
            self._remaining(deadline, cancel)
        try:
            self._remaining(deadline, cancel)
            data, rate = sf.read(wav_path, dtype="float32")
            result = self._detect(wav_path, deadline, cancel)
            self._remaining(deadline, cancel)
            if (
                not isinstance(result, list)
                or len(result) != 1
                or not isinstance(result[0].get("value"), list)
            ):
                raise ValueError("invalid VAD response")
            windows = speech_windows(result[0]["value"], len(data), rate, audio=data)
            if not windows:
                return [{"text": ""}]
            self._load(deadline, cancel)
            texts = []
            for start, end in windows:
                audio = io.BytesIO()
                sf.write(audio, data[start:end], rate, format="WAV", subtype="PCM_16")
                body = {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_audio",
                                    "input_audio": {
                                        "data": base64.b64encode(audio.getvalue()).decode(),
                                        "format": "wav",
                                    },
                                }
                            ],
                        }
                    ],
                    "temperature": 0,
                    "seed": 20261114,
                    "max_tokens": 512,
                    "cache_prompt": False,
                }
                request = urllib.request.Request(  # noqa: S310 -- fixed loopback endpoint
                    self.url + "/v1/chat/completions",
                    json.dumps(body).encode(),
                    {"Content-Type": "application/json"},
                )
                texts.append(parse_response(self._infer(request, deadline, cancel)))
                self._remaining(deadline, cancel)
            return [{"text": " ".join(texts)}]
        except BaseException:
            self._stop()
            self._stop_vad()
            raise
        finally:
            self.lock.release()
