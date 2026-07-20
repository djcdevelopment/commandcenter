from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.callers import callerctl


class RegistryAclTests(TestCase):
    def test_acl_failure_is_degraded_and_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "callers.json"
            path.write_text("{}", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=["icacls"], returncode=1, stdout="", stderr="access denied")
            with patch.object(callerctl, "os", wraps=os) as os_mock:
                os_mock.name = "nt"
                with patch.dict(os.environ, {"USERNAME": "tester"}, clear=False):
                    with patch.object(callerctl.subprocess, "run", return_value=completed):
                        status = callerctl._acl_status(path)
        self.assertEqual(status["status"], "degraded")

    def test_acl_success_is_secured(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "callers.json"
            path.write_text("{}", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=["icacls"], returncode=0,
                stdout="callers.json\n  tester:(F)\n", stderr="")
            with patch.object(callerctl, "os", wraps=os) as os_mock:
                os_mock.name = "nt"
                with patch.dict(os.environ, {"USERNAME": "tester"}, clear=False):
                    with patch.object(callerctl.subprocess, "run", return_value=completed):
                        status = callerctl._acl_status(path)
        self.assertEqual(status["status"], "secured")
