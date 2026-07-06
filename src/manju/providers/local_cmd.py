"""Local-command adapter (§8.6 ``module:Class`` escape hatch).

Drive ANY local generator CLI (a local Stable-Video-Diffusion script, a Wan/
CogVideo wrapper, an ffmpeg one-liner) as a first-class provider. The command
is a shlex template; ``{out}`` is a temp path the tool must write, and the
resulting file is registered as a take with full lineage (argv, exit code,
duration). Costs are 0 (local); the duration is recorded.

Substitution mirrors :func:`manju.agents.build_command`: the template is
shell-split FIRST, then placeholders are substituted INTO each word, so a
``{prompt}`` that contains spaces stays exactly ONE argv element and is never
word-split or re-quoted.

Manifest (``local_cmd`` section)::

    # ~/.manju/providers/svd/provider.yaml
    id: svd_local
    type: video
    adapter: manju.providers.local_cmd:LocalCommandProvider
    capabilities: [image_to_video]
    cost: {per_call: 0.0, currency: CNY}     # local: free (duration is recorded)
    local_cmd:
      command: "svd-cli --image {image} --prompt {prompt} --seconds {duration_s} --seed {seed} --out {out}"
      timeout_s: 600
      output_ext: .mp4

The command runs with ``cwd`` = project root and the parent environment plus
``MANJU_*`` vars (MANJU_SHOT_ID / MANJU_PROMPT / MANJU_WIDTH / MANJU_HEIGHT /
MANJU_FPS / MANJU_DURATION_S / MANJU_SEED / MANJU_IMAGE / MANJU_REFS_DIR /
MANJU_OUT / MANJU_PROJECT_ROOT), so a tool can read either argv or the env.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from ..core.container import Project, TakeInfo
from ..core.models import RemoteJobInfo
from .base import FailureKind, GenerationRequest, Provider, ProviderFailure
from .manifest import ProviderManifest


class LocalCommandProvider(Provider):
    """Run a configured local command and register its output as a take."""

    kind = "local"

    def __init__(
        self,
        manifest: ProviderManifest,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.manifest = manifest
        self.id = manifest.id
        cfg = manifest.local_cmd
        if not cfg.command:
            raise ValueError(f"manifest {manifest.id}: local_cmd.command is required")
        if "{out}" not in cfg.command:
            raise ValueError(
                f"manifest {manifest.id}: local_cmd.command must contain the {{out}} "
                "placeholder"
            )
        try:
            self._words = shlex.split(cfg.command)
        except ValueError as exc:
            raise ValueError(
                f"manifest {manifest.id}: local_cmd.command is not valid shell syntax: {exc}"
            ) from exc
        if not self._words:
            raise ValueError(f"manifest {manifest.id}: local_cmd.command is empty")
        self._timeout_s = cfg.timeout_s
        ext = cfg.output_ext or ".mp4"
        self._ext = ext if ext.startswith(".") else "." + ext
        self._clock = clock

    # ------------------------------------------------------------- Provider

    def generate(self, req: GenerationRequest) -> list[TakeInfo]:
        values = self._values(req)
        with tempfile.TemporaryDirectory(prefix=f"localcmd_{req.shot.id}_") as tmp:
            out = Path(tmp) / f"out{self._ext}"
            values["out"] = str(out)
            argv = self._build_argv(values)
            env = self._env(req, values)

            t0 = self._clock()
            try:
                proc = subprocess.run(
                    argv,
                    cwd=str(req.project.root),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                tail = _tail(exc.stderr or "")
                raise ProviderFailure(
                    FailureKind.timeout,
                    f"{self.id}: command timed out after {self._timeout_s}s: "
                    f"{argv[0]}" + (f" — {tail}" if tail else ""),
                    detail={"argv": argv},
                ) from exc
            except (OSError, ValueError) as exc:  # binary missing / bad argv
                raise ProviderFailure(
                    FailureKind.invalid,
                    f"{self.id}: cannot run {argv[0]!r}: {exc}",
                    detail={"argv": argv},
                ) from exc
            elapsed = round(self._clock() - t0, 3)

            if proc.returncode != 0:
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: command exited {proc.returncode}: {_tail(proc.stderr)}",
                    detail={"argv": argv, "exit_code": proc.returncode},
                )
            if not out.exists() or out.stat().st_size == 0:
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: command exited 0 but wrote no output to "
                    f"{out.name}: {_tail(proc.stderr)}",
                    detail={"argv": argv, "out": str(out)},
                )

            compiled = str(values.get("prompt") or "") or None
            take = self._register(
                req,
                out,
                params={
                    "argv": argv,
                    "exit_code": proc.returncode,
                    "duration_s": elapsed,  # local: cost 0, but duration is recorded
                    "command": self.manifest.local_cmd.command,
                },
                compiled_prompt=compiled,
                remote=RemoteJobInfo(
                    job_id=None, cost=0.0, currency=self.manifest.cost.currency
                ),
            )
            return [take]

    # ------------------------------------------------------------- helpers

    def _values(self, req: GenerationRequest) -> dict[str, object]:
        from .prompt import compile_prompt

        config = req.project.load_config()
        return {
            "prompt": compile_prompt(req.shot, req.bible),
            "width": config.width,
            "height": config.height,
            "fps": config.fps,
            "duration_s": req.duration_ms / 1000.0,
            "seed": req.params.get("seed", 0),
            "image": _resolve_ref_image(req),
            "refs_dir": str(req.project.refs_dir),
        }

    def _build_argv(self, values: dict) -> list[str]:
        """Substitute placeholders INTO each pre-split word (never across word
        boundaries) — one templated word stays one argv element (build_command
        semantics). Unknown ``{...}`` tokens are left untouched, like the agent
        shell."""
        return [self._sub(w, values) if "{" in w else w for w in self._words]

    @staticmethod
    def _sub(word: str, values: dict) -> str:
        for key, val in values.items():
            token = "{" + key + "}"
            if token in word:
                word = word.replace(token, str(val))
        return word

    def _env(self, req: GenerationRequest, values: dict) -> dict[str, str]:
        env = dict(os.environ)  # parent passthrough
        env.update(
            {
                "MANJU_SHOT_ID": req.shot.id,
                "MANJU_PROJECT_ROOT": str(req.project.root),
                "MANJU_PROMPT": str(values.get("prompt", "")),
                "MANJU_WIDTH": str(values.get("width", "")),
                "MANJU_HEIGHT": str(values.get("height", "")),
                "MANJU_FPS": str(values.get("fps", "")),
                "MANJU_DURATION_S": str(values.get("duration_s", "")),
                "MANJU_SEED": str(values.get("seed", "")),
                "MANJU_IMAGE": str(values.get("image", "")),
                "MANJU_REFS_DIR": str(values.get("refs_dir", "")),
                "MANJU_OUT": str(values.get("out", "")),
            }
        )
        return env


def _tail(text: str, limit: int = 300) -> str:
    """Last ``limit`` chars of ``text``, whitespace-collapsed to one line."""
    if not text:
        return ""
    return " ".join(text.split())[-limit:]


def _resolve_ref_image(req: GenerationRequest) -> str:
    """Resolve {image}: explicit param, then a bible ref_image (characters then
    scene). Returns an absolute path string, or "" when none is available."""
    project: Project = req.project
    explicit = req.params.get("image")
    if explicit:
        p = _as_path(project, explicit)
        return str(p) if p and p.exists() else str(explicit)
    bible = req.bible or {}
    keys = list(req.shot.characters) + ([req.shot.scene] if req.shot.scene else [])
    for key in keys:
        entry = bible.get(key)
        if isinstance(entry, dict) and entry.get("ref_image"):
            p = _as_path(project, entry["ref_image"])
            if p and p.exists():
                return str(p)
    return ""


def _as_path(project: Project, value) -> Path | None:
    p = Path(value)
    if p.is_absolute():
        return p
    try:
        return project.resolve(value)
    except Exception:
        return None
