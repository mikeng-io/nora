"""Run inside the STT image: python -m unittest discover -s /checks."""

import asyncio
import io
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from fastapi import UploadFile


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_text_bypasses_opencc_legacy_still_converts(self):
        raw = "香港 RV There Yet 喺度"
        for model in [server.QWEN_MODEL, "sensevoice", "noise-detection"]:
            with (
                patch.object(server, "_run_stt_blocking", return_value=([{"text": raw}], 1)),
                patch.object(server._cc, "convert", wraps=server._cc.convert) as convert,
            ):
                response = await server.transcribe(
                    file=UploadFile(filename="generated.wav", file=io.BytesIO(b"generated")),
                    model=model,
                    language="yue",
                    response_format="verbose_json",
                )
                result = json.loads(response.body)
                self.assertEqual(result["segments"], [])
                if model == server.QWEN_MODEL:
                    self.assertEqual(result["text"], raw)
                    convert.assert_not_called()
                else:
                    convert.assert_called_once_with(raw)

    async def test_cancel_keeps_files_until_worker_relinquishes_them(self):
        started, release = threading.Event(), threading.Event()
        paths = []
        signals = []

        def work(**kwargs):
            paths.extend([Path(kwargs["tmp_path"]), Path(kwargs["wav_path"])])
            paths[1].write_bytes(b"generated")
            signals.append(kwargs["cancel"])
            started.set()
            release.wait(3)
            self.assertTrue(all(path.exists() for path in paths))
            return [{"text": ""}], 0

        with patch.object(server, "_run_stt_blocking", side_effect=work):
            task = asyncio.create_task(
                server.transcribe(
                    file=UploadFile(filename="generated.wav", file=io.BytesIO(b"generated")),
                    model=server.QWEN_MODEL,
                    language="yue",
                    response_format="json",
                )
            )
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            task.cancel()
            await asyncio.sleep(0)
            self.assertTrue(signals[0].is_set())
            task.cancel()
            await asyncio.sleep(0)
            self.assertTrue(all(path.exists() for path in paths))
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(all(not path.exists() for path in paths))

    async def test_invalid_model_and_backend_failure_are_errors(self):
        with self.assertRaises(server.HTTPException) as error:
            await server.transcribe(model="unknown")
        self.assertEqual(error.exception.status_code, 400)
        with (
            patch.object(server, "_run_stt_blocking", side_effect=ValueError("invalid VAD")),
            self.assertRaises(server.HTTPException) as error,
        ):
            await server.transcribe(
                file=UploadFile(filename="generated.wav", file=io.BytesIO(b"generated")),
                model=server.QWEN_MODEL,
                language="yue",
                response_format="json",
            )
        self.assertEqual(error.exception.status_code, 500)
