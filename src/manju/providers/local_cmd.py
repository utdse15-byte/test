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
MANJU_FPS / MANJU_DURATION_S / MANJU_SEED / MANJU_IMAGE / MANJU_VIDEO_REF /
MANJU_REFS_DIR / MANJU_OUT / MANJU_PROJECT_ROOT), so a tool can read either argv
or the env.

Reference placeholders (goal item 7), resolved through the shared
:func:`manju.providers.refs.resolve_refs` so tiering + lineage match every other
provider. Precedence within each kind is most-specific-first:

    {image}      the primary reference IMAGE (shot.params > shot refs > bible >
                 media/refs), as an absolute path, or "" when none resolves
    {video_ref}  the primary reference VIDEO, same tier order, or ""
    {refs_dir}   the media/refs directory itself, for tools that scan it
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from ..core.container import TakeInfo
from ..core.models import RemoteJobInfo
from .base import (
    FailureKind,
    GenerationRequest,
    Provider,
    ProviderFailure,
    record_provider_failure,
)
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
        # A plain Provider records its own terminal failures (goal 10): a missing
        # binary, a nonzero exit, or a silent no-output run all land in
        # reports/failures.jsonl in the SAME shape, keyed to the shot — the
        # exception type and one-line message are untouched.
        try:
            return self._generate(req)
        except ProviderFailure as exc:
            record_provider_failure(req.project, req.shot.id, self.id, exc)
            raise

    def _generate(self, req: GenerationRequest) -> list[TakeInfo]:
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
                    detail={"argv": argv,
                            "hint": f"确认 {argv[0]!r} 已安装且在 PATH,或修正 local_cmd.command"},
                ) from exc
            elapsed = round(self._clock() - t0, 3)

            if proc.returncode != 0:
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: command exited {proc.returncode}: {_tail(proc.stderr)}",
                    detail={"argv": argv, "exit_code": proc.returncode,
                            "reason": _tail(proc.stderr),
                            "hint": "复现该命令并看 stderr;必要时在 local_cmd.command 调参"},
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
                    "ref_delivery": self._ref_delivery(req),
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
        refset = req.refset()
        primary_video = refset.primary_video
        return {
            "prompt": compile_prompt(req.shot, req.bible),
            "width": config.width,
            "height": config.height,
            "fps": config.fps,
            "duration_s": req.duration_ms / 1000.0,
            "seed": req.params.get("seed", 0),
            "image": refset.primary_image_path_str(),
            "video_ref": str(primary_video) if primary_video is not None else "",
            "refs_dir": str(req.project.refs_dir),
        }

    def _ref_delivery(self, req: GenerationRequest) -> dict:
        """What refs the command line actually consumed (goal item 7), keyed off
        the placeholders present in the command — so the build layer can flag a
        shot whose declared ref never reaches the tool."""
        refset = req.refset()
        command = self.manifest.local_cmd.command or ""
        images: list[dict] = []
        videos: list[dict] = []
        if "{image}" in command and refset.image_items():
            images.append({"ref": refset.primary_image_path_str(),
                           "tier": refset.primary_image_source, "delivered_as": "path"})
        if "{video_ref}" in command and refset.primary_video is not None:
            v = refset.video_items()[0]
            videos.append({"ref": v.ref, "tier": v.tier, "delivered_as": "path"})
        return {
            "image_mode": "path" if images else "none",
            "video_mode": "path" if videos else "none",
            "images": images,
            "videos": videos,
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
                "MANJU_VIDEO_REF": str(values.get("video_ref", "")),
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
