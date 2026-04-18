# SPDX-License-Identifier: BSD-3-Clause

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from rct.static_files import build_static_file_response


class StaticFileResponseTests(unittest.TestCase):
    def test_serves_index_for_root(self):
        with TemporaryDirectory() as temp_dir:
            static_root = Path(temp_dir)
            (static_root / "index.html").write_text("<h1>RCT</h1>", encoding="utf-8")

            response = build_static_file_response("/", static_root)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.body, b"<h1>RCT</h1>")
            self.assertIn(("Content-Type", "text/html; charset=utf-8"), response.headers)

    def test_serves_other_static_files(self):
        with TemporaryDirectory() as temp_dir:
            static_root = Path(temp_dir)
            (static_root / "app.css").write_text("body {}", encoding="utf-8")

            response = build_static_file_response("/app.css?v=1", static_root)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.body, b"body {}")
            self.assertIn(("Content-Type", "text/css; charset=utf-8"), response.headers)

    def test_returns_404_for_missing_file(self):
        with TemporaryDirectory() as temp_dir:
            response = build_static_file_response("/missing.js", Path(temp_dir))

            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.body, b"Not Found\n")

    def test_blocks_path_traversal(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            static_root = temp_path / "frontend"
            static_root.mkdir()
            (temp_path / "secret.txt").write_text("secret", encoding="utf-8")

            response = build_static_file_response("/../secret.txt", static_root)

            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
