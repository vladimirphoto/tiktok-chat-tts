import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from ttchat import __version__, cli, selftest


class CliTests(unittest.TestCase):
    def test_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--version"]), 0)
        self.assertIn(__version__, out.getvalue())


class ReportTests(unittest.TestCase):
    def test_required_failure_fails_report(self):
        r = selftest.Report()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(r.run("ок", lambda: "норм"))
            self.assertFalse(r.run("плохо", lambda: 1 / 0))
        self.assertFalse(r.passed)
        self.assertIn("ZeroDivisionError", r.steps[1]["detail"])

    def test_optional_failure_is_only_a_warning(self):
        r = selftest.Report()
        with contextlib.redirect_stdout(io.StringIO()):
            r.run("ок", lambda: "норм")
            r.run("необязательно", lambda: 1 / 0, required=False)
        self.assertTrue(r.passed)

    def test_json_shape(self):
        r = selftest.Report()
        with contextlib.redirect_stdout(io.StringIO()):
            r.run("шаг", lambda: "деталь")
        data = r.to_json()
        for key in ("version", "python", "platform", "frozen", "passed", "steps"):
            self.assertIn(key, data)
        json.dumps(data, ensure_ascii=False)

    def test_local_checks_pass_without_network(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIn("настроек", selftest._check_settings())
            self.assertIn("фраза", selftest._check_text())

    def test_selftest_writes_report_even_when_network_is_down(self):
        """Без интернета самопроверка не падает, а честно сообщает об ошибках в отчёте."""
        original = selftest.VOICES
        selftest.VOICES = {"ru-RU-SvetlanaNeural": "x"}
        try:
            with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
                path = Path(d) / "r.json"
                code = selftest.run_selftest(str(path))
                data = json.loads(path.read_text(encoding="utf-8"))
        finally:
            selftest.VOICES = original
        self.assertIn(code, (0, 1))
        self.assertEqual(code == 0, data["passed"])
        self.assertTrue(any(s["name"].startswith("Синтез") for s in data["steps"]))


if __name__ == "__main__":
    unittest.main()
