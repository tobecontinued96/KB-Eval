from __future__ import annotations

import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib import error as url_error
from unittest.mock import patch

from kb_eval.mineru_api import _build_ssl_context, download_file, request_upload_url, safe_data_id, upload_file


class RequestUploadUrlTests(unittest.TestCase):
    def test_accepts_official_string_file_url_response(self) -> None:
        response = {
            "code": 0,
            "data": {
                "batch_id": "batch-123",
                "file_urls": ["https://uploads.example/document.pdf"],
            },
            "msg": "ok",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "manual.pdf"
            with patch("kb_eval.mineru_api.api_request_json", return_value=response):
                result = request_upload_url(pdf_path, token="test-token", model_version="vlm")

        self.assertEqual(result["batch_id"], "batch-123")
        self.assertEqual(result["upload_url"], "https://uploads.example/document.pdf")
        self.assertEqual(result["file_id"], "")

    def test_keeps_compatibility_with_object_file_url_response(self) -> None:
        response = {
            "code": 0,
            "data": {
                "batch_id": "batch-456",
                "file_urls": [
                    {
                        "file_id": "file-1",
                        "upload_url": "https://uploads.example/legacy.pdf",
                    },
                ],
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "manual.pdf"
            with patch("kb_eval.mineru_api.api_request_json", return_value=response):
                result = request_upload_url(pdf_path, token="test-token", model_version="vlm")

        self.assertEqual(result["file_id"], "file-1")
        self.assertEqual(result["upload_url"], "https://uploads.example/legacy.pdf")


class SafeDataIdTests(unittest.TestCase):
    def test_normalizes_non_ascii_and_spaces(self) -> None:
        self.assertEqual(safe_data_id("思科 Catalyst 1200 用户手册"), "Catalyst_1200")


class UploadFileTests(unittest.TestCase):
    def test_put_upload_does_not_send_content_type(self) -> None:
        captured: dict[str, object] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_PUT(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                captured["headers"] = dict(self.headers.items())
                captured["body"] = self.rfile.read(length)
                self.send_response(200)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                pdf_path = Path(temp_dir) / "manual.pdf"
                pdf_path.write_bytes(b"pdf-content")
                upload_file(pdf_path, f"http://127.0.0.1:{server.server_port}/upload?signature=test")
        finally:
            thread.join(timeout=5)
            server.server_close()

        headers = captured["headers"]
        self.assertIsInstance(headers, dict)
        self.assertNotIn("Content-Type", headers)
        self.assertEqual(captured["body"], b"pdf-content")


class DownloadSslContextTests(unittest.TestCase):
    def test_ignores_unexpected_eof_when_runtime_supports_it(self) -> None:
        if not hasattr(ssl, "OP_IGNORE_UNEXPECTED_EOF"):
            self.skipTest("OpenSSL runtime does not expose OP_IGNORE_UNEXPECTED_EOF")

        context = _build_ssl_context()

        self.assertTrue(context.options & ssl.OP_IGNORE_UNEXPECTED_EOF)


class DownloadFileTests(unittest.TestCase):
    def test_falls_back_to_curl_after_urllib_tls_eof(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            output_index = command.index("--output") + 1
            Path(command[output_index]).write_bytes(b"zip-content")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "result.zip"
            tls_eof = ssl.SSLEOFError("TLS/SSL connection has been closed (EOF) (_ssl.c:1010)")
            with (
                patch("kb_eval.mineru_api.request.urlopen", side_effect=url_error.URLError(tls_eof)) as urlopen,
                patch("kb_eval.mineru_api.time.sleep"),
                patch("shutil.which", return_value="curl.exe"),
                patch("subprocess.run", side_effect=fake_run),
            ):
                download_file("https://cdn-mineru.example/result.zip", target)

            self.assertEqual(target.read_bytes(), b"zip-content")

        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(len(commands), 1)
        self.assertIn("--retry-all-errors", commands[0])


if __name__ == "__main__":
    unittest.main()
