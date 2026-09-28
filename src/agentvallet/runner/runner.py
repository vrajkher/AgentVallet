"""Execute a skill's entrypoint in an isolated subprocess.

Isolation (best-effort, dependency-free):
- the package is copied into a fresh temporary working directory,
- only an allowlisted environment is passed (no API keys or tokens leak into skills),
- stdin/stdout JSON contract with a hard timeout,
- on POSIX, CPU/memory rlimits and a new session so the whole process group is killed on timeout.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import jsonschema

from ..models import ExecutionResult
from ..spec import SkillPackage

ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP")
MAX_OUTPUT_BYTES = 10 * 1024 * 1024


def _limits(mem_mb: int, cpu_s: int):  # pragma: no cover - runs in child process
    def apply() -> None:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
            mem = mem_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        except (ImportError, ValueError, OSError):
            pass

    return apply


class SkillRunner:
    def __init__(self, default_timeout: float = 60.0, memory_mb: int = 1024, python: str | None = None):
        self.default_timeout = default_timeout
        self.memory_mb = memory_mb
        self.python = python or sys.executable

    def _env(self, workdir: Path) -> dict[str, str]:
        env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
        env.update(
            {
                "HOME": str(workdir),
                "PYTHONHASHSEED": "0",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "AV_SANDBOX": "1",
            }
        )
        return env

    def check_input(self, pkg: SkillPackage, data: Any) -> str | None:
        try:
            jsonschema.validate(data, pkg.input_schema())
            return None
        except jsonschema.ValidationError as exc:
            return f"input does not match inputs.schema.json: {exc.message}"

    def check_output(self, pkg: SkillPackage, data: Any) -> str | None:
        try:
            jsonschema.validate(data, pkg.output_schema())
            return None
        except jsonschema.ValidationError as exc:
            return f"output does not match outputs.schema.json: {exc.message}"

    def _exec(self, pkg: SkillPackage, script: Path, payload: Any, timeout: float) -> ExecutionResult:
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="av-run-") as tmp:
            workdir = Path(tmp) / "pkg"
            shutil.copytree(pkg.path, workdir, ignore=shutil.ignore_patterns("__pycache__"))
            rel = script.relative_to(pkg.path)
            kwargs: dict[str, Any] = {}
            if os.name == "posix":
                kwargs["start_new_session"] = True
                kwargs["preexec_fn"] = _limits(self.memory_mb, int(timeout) + 5)
            proc = subprocess.Popen(
                [self.python, "-I", "-B", str(workdir / rel)],
                cwd=workdir,
                env=self._env(workdir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                **kwargs,
            )
            try:
                stdout, stderr = proc.communicate(json.dumps(payload), timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)
                else:  # pragma: no cover
                    proc.kill()
                proc.communicate()
                return ExecutionResult(
                    ok=False,
                    error=f"timed out after {timeout}s",
                    timed_out=True,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
        dur = int((time.monotonic() - start) * 1000)
        if len(stdout) > MAX_OUTPUT_BYTES:
            return ExecutionResult(ok=False, error="output too large", duration_ms=dur, exit_code=proc.returncode)
        if proc.returncode != 0:
            tail = stderr.strip().splitlines()[-1:] or ["no stderr"]
            return ExecutionResult(
                ok=False,
                error=f"exit code {proc.returncode}: {tail[0][:500]}",
                stderr=stderr[-8000:],
                duration_ms=dur,
                exit_code=proc.returncode,
            )
        try:
            # Accept trailing log lines on stdout by parsing the last JSON line if needed.
            text = stdout.strip()
            try:
                output = json.loads(text)
            except json.JSONDecodeError:
                output = json.loads(text.splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return ExecutionResult(
                ok=False,
                error="stdout is not valid JSON",
                stderr=stderr[-8000:],
                duration_ms=dur,
                exit_code=proc.returncode,
            )
        return ExecutionResult(ok=True, output=output, stderr=stderr[-8000:], duration_ms=dur, exit_code=0)

    def run(
        self, pkg: SkillPackage, data: Any, timeout: float | None = None, check_schemas: bool = True
    ) -> ExecutionResult:
        if check_schemas and (err := self.check_input(pkg, data)):
            return ExecutionResult(ok=False, error=err)
        res = self._exec(pkg, pkg.entrypoint, data, timeout or pkg.timeout or self.default_timeout)
        if res.ok and check_schemas and (err := self.check_output(pkg, res.output)):
            res.ok, res.error = False, err
        return res

    def run_validator(self, pkg: SkillPackage, data: Any, output: Any, timeout: float = 30.0) -> ExecutionResult:
        if not pkg.validator.exists():
            return ExecutionResult(ok=True, output={"passed": True, "errors": []})
        return self._exec(pkg, pkg.validator, {"input": data, "output": output}, timeout)
