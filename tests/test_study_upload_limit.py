import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEST_DIR = Path(tempfile.mkdtemp(prefix="tangent-upload-limit-"))
os.environ.setdefault("TANGENT_DB", f"sqlite:///{(_TEST_DIR / 'test.db').as_posix()}")

from app.main import app
from app.study import MAX_PDF_BYTES


class StudyUploadLimitTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, chunks, declared_size=None, path="/api/study/documents"):
        headers = [(b"content-type", b"multipart/form-data; boundary=test-boundary")]
        if declared_size is not None:
            headers.append((b"content-length", str(declared_size).encode()))
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": b"", "root_path": "", "headers": headers,
            "client": ("127.0.0.1", 12345), "server": ("testserver", 80),
        }
        consumed = 0
        responses = []

        async def receive():
            nonlocal consumed
            chunk = chunks[consumed]
            consumed += 1
            return {"type": "http.request", "body": chunk, "more_body": consumed < len(chunks)}

        async def send(event):
            responses.append(event)

        await app(scope, receive, send)
        status = next(event["status"] for event in responses if event["type"] == "http.response.start")
        body = b"".join(event.get("body", b"") for event in responses)
        return status, json.loads(body), consumed

    async def test_declared_oversize_is_rejected_without_receiving_or_parsing_body(self):
        with patch("app.routers.study.extract_pdf") as extract:
            for path in ("/api/study/documents", "/api/study/documents/"):
                with self.subTest(path=path):
                    status, body, consumed = await self.request([], MAX_PDF_BYTES + 64 * 1024 + 1, path)
                    self.assertEqual(status, 413)
                    self.assertIn("upload request", body["detail"])
                    self.assertEqual(consumed, 0)
            extract.assert_not_called()

    async def test_chunked_oversize_is_rejected_even_without_honest_length_or_auth(self):
        header = (
            b'--test-boundary\r\nContent-Disposition: form-data; name="file"; filename="notes.pdf"\r\n'
            b'Content-Type: application/pdf\r\n\r\n'
        )
        chunks = [header] + [b"x" * (1024 * 1024)] * 11 + [b"\r\n--test-boundary--\r\n"]
        spooled_files = []

        def spool(*args, **kwargs):
            file = tempfile.SpooledTemporaryFile(*args, **kwargs)
            spooled_files.append(file)
            return file

        with patch("app.routers.study.extract_pdf") as extract:
            with patch("starlette.formparsers.SpooledTemporaryFile", side_effect=spool):
                for declared_size in (None, 1):
                    with self.subTest(declared_size=declared_size):
                        status, body, consumed = await self.request(chunks, declared_size)
                        self.assertEqual(status, 413)
                        self.assertIn("upload request", body["detail"])
                        self.assertLess(consumed, len(chunks))
                        self.assertTrue(spooled_files[-1].closed)
            extract.assert_not_called()


if __name__ == "__main__":
    unittest.main()
