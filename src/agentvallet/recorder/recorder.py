"""Work recorder: captures *observable* AI-assisted work as an exact, ordered run history.

Usage::

    with vallet.recorder.start("Sum invoice lines", tags=["finance"]) as run:
        run.input({"items": [...]})
        run.prompt("Add the line amounts")
        run.response("Total is 42.5")
        run.code(open("solve.py").read())      # the code that produced the result
        run.output({"total": 42.5})
    # -> status "success" (or "failed" if the block raised)

Only what the caller explicitly provides is stored; hidden model reasoning is never captured.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any

from ..models import Run, RunStatus, Step, StepKind
from ..store import ArtifactStore, RunStore
from .redact import redact, redact_text


class RunHandle:
    def __init__(self, recorder: WorkRecorder, run: Run):
        self._rec = recorder
        self.run = run
        self.finished = False

    @property
    def id(self) -> str:
        return self.run.id

    def _add(self, kind: StepKind, content: str = "", data: Any = None, artifact_hash: str | None = None) -> Step:
        if self.finished:
            raise RuntimeError("run already finished")
        if self._rec.redact_secrets:
            content, data = redact_text(content), redact(data)
        step = self._rec.runs.add_step(
            Step(run_id=self.run.id, kind=kind, content=content, data=data, artifact_hash=artifact_hash)
        )
        return step

    def step(self, kind: StepKind | str, content: str = "", data: Any = None) -> Step:
        """Generic step (``kind`` = input/output/prompt/response/tool_call/code/note/error/...)."""
        kind = StepKind(kind)
        if kind == StepKind.CODE:
            return self.code(content)
        return self._add(kind, content, data)

    def input(self, data: Any, note: str = "") -> Step:
        return self._add(StepKind.INPUT, note, data)

    def output(self, data: Any, note: str = "") -> Step:
        return self._add(StepKind.OUTPUT, note, data)

    def prompt(self, text: str, **meta: Any) -> Step:
        return self._add(StepKind.PROMPT, text, meta or None)

    def response(self, text: str, **meta: Any) -> Step:
        return self._add(StepKind.RESPONSE, text, meta or None)

    def note(self, text: str) -> Step:
        return self._add(StepKind.NOTE, text)

    def validation(self, checks: list[dict[str, Any]], note: str = "output checks") -> Step:
        return self._add(StepKind.VALIDATION, note, checks)

    def error(self, text: str, **meta: Any) -> Step:
        return self._add(StepKind.ERROR, text, meta or None)

    def code(self, source: str, language: str = "python", role: str = "solution") -> Step:
        """Record code that produced (part of) the result. ``role='solution'`` code that reads JSON
        from stdin and prints JSON can be adopted verbatim as a skill's run.py."""
        if self._rec.redact_secrets:
            source = redact_text(source)
        ext = "py" if language == "python" else "txt"
        art = self._rec.artifacts.put_bytes(source.encode(), name=f"code.{ext}")
        return self._add(StepKind.CODE, source, {"language": language, "role": role}, art.hash)

    def tool_call(self, name: str, args: Any = None, result: Any = None, ok: bool = True) -> Step:
        return self._add(StepKind.TOOL_CALL, name, {"args": args, "result": result, "ok": ok})

    def file(self, path: Path | str, note: str = "") -> Step:
        p = Path(path)
        art = self._rec.artifacts.put_file(p)
        return self._add(
            StepKind.FILE, note or p.name, {"name": p.name, "size": art.size, "media_type": art.media_type}, art.hash
        )

    def command(self, cmd: list[str] | str, cwd: Path | str | None = None, timeout: float = 300) -> Step:
        """Execute a command and record argv, exit code, duration and (truncated) output."""
        start = time.monotonic()
        shell = isinstance(cmd, str)
        try:
            proc = subprocess.run(cmd, shell=shell, cwd=cwd, capture_output=True, text=True, timeout=timeout)
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            code, out, err = -1, str(exc.stdout or ""), f"timeout after {timeout}s"
        data = {
            "argv": cmd,
            "exit_code": code,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "stdout": out[-20000:],
            "stderr": err[-20000:],
        }
        label = cmd if isinstance(cmd, str) else " ".join(cmd)
        return self._add(StepKind.COMMAND, label, data)

    def finish(self, status: RunStatus | str = RunStatus.SUCCESS, **metadata: Any) -> Run:
        if self.finished:
            return self.run
        st = RunStatus(status)
        self.run = self._rec.runs.finish(self.run.id, st, metadata or None)
        self.finished = True
        for hook in self._rec.on_finish:
            hook(self.run)
        return self.run

    def __enter__(self) -> RunHandle:
        return self

    def __exit__(self, et: type[BaseException] | None, ev: BaseException | None, tb: TracebackType | None) -> None:
        if self.finished:
            return
        if ev is not None:
            try:
                self.error(f"{et.__name__ if et else 'Error'}: {ev}")
            finally:
                self.finish(RunStatus.FAILED)
        else:
            self.finish(RunStatus.SUCCESS)


class WorkRecorder:
    def __init__(self, runs: RunStore, artifacts: ArtifactStore, redact_secrets: bool = True):
        self.runs = runs
        self.artifacts = artifacts
        self.redact_secrets = redact_secrets
        self.on_finish: list[Callable[[Run], None]] = []

    def start(self, goal: str, tags: list[str] | None = None, agent: str = "unknown", **metadata: Any) -> RunHandle:
        run = Run(
            goal=redact_text(goal) if self.redact_secrets else goal, tags=tags or [], agent=agent, metadata=metadata
        )
        return RunHandle(self, self.runs.create(run))

    def resume(self, run_id: str) -> RunHandle:
        run = self.runs.require(run_id)
        h = RunHandle(self, run)
        h.finished = run.status != RunStatus.RUNNING
        return h
