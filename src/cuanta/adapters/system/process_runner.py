from __future__ import annotations

import contextlib
import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from cuanta.adapters.system.process_tree import ProcessTree, creation_flags
from cuanta.ports.system import Completed

EXIT_GRACE_S = 2.0
POLL_S = 0.2


def _environment(
    extra: Mapping[str, str] | None, unset: Sequence[str] = ()
) -> dict[str, str] | None:
    if extra is None and not unset:
        return None
    merged = dict(os.environ)
    merged.update(extra or {})
    for name in unset:
        merged.pop(name, None)
    return merged


class SubprocessStream:
    def __init__(
        self, process: subprocess.Popen[str], suspended: bool = False, keep_stdin: bool = False
    ) -> None:
        self._process = process
        self._tree = ProcessTree(process, suspended)
        self._input = process.stdin if keep_stdin else None
        self._writing = threading.Lock()
        self._stderr: list[str] = []
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._reader = threading.Thread(target=self._drain_stderr, daemon=True)
        self._reader.start()
        self._out = threading.Thread(target=self._drain_stdout, daemon=True)
        self._out.start()

    def _drain_stdout(self) -> None:
        stream = self._process.stdout
        try:
            if stream is not None:
                for line in stream:
                    self._lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self._lines.put(None)

    def _drain_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return
        for line in stream:
            self._stderr.append(line)

    def lines(self) -> Iterator[str]:
        exited_at: float | None = None
        while True:
            try:
                line = self._lines.get(timeout=POLL_S)
            except queue.Empty:
                if self._process.poll() is None:
                    continue
                now = time.monotonic()
                if exited_at is None:
                    exited_at = now
                elif now - exited_at >= EXIT_GRACE_S:
                    self._tree.close()
                    self._out.join(timeout=5)
                    return
                continue
            if line is None:
                return
            yield line.rstrip("\r\n")

    def wait(self) -> int:
        self.end_input()
        code = self._process.wait()
        self._reader.join(timeout=5)
        return code

    def stderr_text(self) -> str:
        return "".join(self._stderr)

    def terminate(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()

    def send(self, text: str) -> bool:
        with self._writing:
            stdin = self._input
            if stdin is None:
                return False
            try:
                stdin.write(text)
                stdin.flush()
            except (OSError, ValueError):
                return False
            return True

    def end_input(self) -> None:
        with self._writing:
            stdin, self._input = self._input, None
        if stdin is not None:
            with contextlib.suppress(OSError, ValueError):
                stdin.close()

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        self._tree.close()
        stdin, self._input = self._input, None
        if stdin is not None:
            with contextlib.suppress(OSError, ValueError):
                stdin.close()
        self._reader.join(timeout=5)
        self._out.join(timeout=5)
        for stream in (self._process.stdout, self._process.stderr):
            if stream is not None:
                stream.close()


def _feed(process: subprocess.Popen[str], text: str) -> None:
    stdin = process.stdin
    if stdin is None:
        return
    try:
        stdin.write(text)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            stdin.close()


class SubprocessRunner:
    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def resolve(self, args: Sequence[str]) -> list[str]:
        if not args:
            return []
        head = shutil.which(args[0]) or args[0]
        return [head, *args[1:]]

    def run(
        self,
        args: Sequence[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Completed:
        started = time.perf_counter()
        process: subprocess.Popen[str] | None = None
        tree: ProcessTree | None = None
        try:
            flags = creation_flags()
            process = subprocess.Popen(
                self.resolve(args),
                cwd=cwd,
                env=_environment(env),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                creationflags=flags,
            )
            tree = ProcessTree(process, suspended=flags != 0)
            stdout, stderr = process.communicate(timeout=timeout)
            return Completed(
                process.returncode, stdout or "", stderr or "", time.perf_counter() - started
            )
        except FileNotFoundError as error:
            return Completed(127, "", str(error), time.perf_counter() - started)
        except OSError as error:
            return Completed(126, "", str(error), time.perf_counter() - started)
        except subprocess.TimeoutExpired as error:
            if tree is not None:
                tree.close()
            if process is not None:
                process.kill() if process.poll() is None else None
                stdout, _ = process.communicate(timeout=EXIT_GRACE_S)
            else:
                stdout = error.stdout if isinstance(error.stdout, str) else ""
            return Completed(
                124, stdout or "", f"timed out after {timeout}s", time.perf_counter() - started
            )
        finally:
            if tree is not None:
                tree.close()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=EXIT_GRACE_S)
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()

    def stream(
        self,
        args: Sequence[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
        unset: Sequence[str] = (),
        keep_stdin: bool = False,
    ) -> SubprocessStream:
        flags = creation_flags()
        process = subprocess.Popen(
            self.resolve(args),
            cwd=cwd,
            env=_environment(env, unset),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL if stdin_text is None and not keep_stdin else subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=True,
            creationflags=flags,
        )
        stream = SubprocessStream(process, suspended=flags != 0, keep_stdin=keep_stdin)
        if keep_stdin and stdin_text is not None:
            threading.Thread(target=stream.send, args=(stdin_text,), daemon=True).start()
        elif stdin_text is not None:
            threading.Thread(target=_feed, args=(process, stdin_text), daemon=True).start()
        return stream
