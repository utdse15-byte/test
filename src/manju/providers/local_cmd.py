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
import re
import shlex
import signal
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

# `{identifier}` placeholders in a local_cmd template word — matched once per
# template scan (round-W #58's single-pass substitution relies on this being
# applied to the ORIGINAL word, never to already-substituted text).
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


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
            # F1 resource hygiene: `subprocess.run` (and a bare Popen.kill) only
            # signals the DIRECT child on timeout, ORPHANING any grandchild a
            # wrapper command forked — a local SVD/Wan/CogVideo worker holding
            # VRAM keeps running past the manifest timeout_s. Start the child in
            # its OWN session/process group (start_new_session=True) and, on
            # timeout, SIGKILL the WHOLE group so no grandchild survives, then
            # reap. The ProviderFailure(timeout) surface + every success/failure
            # semantic below is byte-identical to the old subprocess.run path.
            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=str(req.project.root),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    **_new_session_kwargs(),
                )
            except (OSError, ValueError) as exc:  # binary missing / bad argv
                raise ProviderFailure(
                    FailureKind.invalid,
                    f"{self.id}: cannot run {argv[0]!r}: {exc}",
                    detail={"argv": argv,
                            "hint": f"确认 {argv[0]!r} 已安装且在 PATH,或修正 local_cmd.command"},
                ) from exc
            try:
                stdout, stderr = proc.communicate(timeout=self._timeout_s)
            except subprocess.TimeoutExpired as exc:
                _kill_process_group(proc)  # reap the whole group, not just the child
                stdout, stderr = _reap(proc)
                tail = _tail(stderr or _decode(getattr(exc, "stderr", None)))
                raise ProviderFailure(
                    FailureKind.timeout,
                    f"{self.id}: command timed out after {self._timeout_s}s: "
                    f"{argv[0]}" + (f" — {tail}" if tail else ""),
                    detail={"argv": argv},
                ) from exc
            elapsed = round(self._clock() - t0, 3)
            returncode = proc.returncode

            if returncode != 0:
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: command exited {returncode}: {_tail(stderr)}",
                    detail={"argv": argv, "exit_code": returncode,
                            "reason": _tail(stderr),
                            "hint": "复现该命令并看 stderr;必要时在 local_cmd.command 调参"},
                )
            if not out.exists() or out.stat().st_size == 0:
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: command exited 0 but wrote no output to "
                    f"{out.name}: {_tail(stderr)}",
                    detail={"argv": argv, "out": str(out)},
                )

            compiled = str(values.get("prompt") or "") or None
            take = self._register(
                req,
                out,
                params={
                    "argv": argv,
                    "exit_code": returncode,
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
        """Single-pass substitution: ONE regex scan over the ORIGINAL template
        word, each ``{key}`` replaced via a lookup callback (round-W #58).

        The old implementation looped ``key in values`` and called
        ``word.replace(token, str(val))`` once per key, mutating ``word`` on
        every iteration — so a substituted VALUE that happened to contain a
        literal ``{out}`` or ``{seed}`` (a compiled PROMPT can contain
        anything the shot's dialogue/action text does) got RE-SCANNED by a
        later key's replace call and silently replaced again, corrupting the
        prompt. ``re.sub`` with a replacement function scans the template
        ONCE, left to right, and never re-examines text it just inserted — an
        unknown ``{...}`` token (no matching key) is left untouched, matching
        the agent shell's stance."""

        def _repl(m: "re.Match[str]") -> str:
            key = m.group(1)
            return str(values[key]) if key in values else m.group(0)

        return _PLACEHOLDER_RE.sub(_repl, word)

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


def _decode(value: object) -> str:
    """A TimeoutExpired.stderr may be bytes/str/None depending on capture — the
    reaped ``proc.communicate()`` stderr is preferred, this is only the fallback."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _new_session_kwargs() -> dict:
    """Put the child (and every grandchild it forks) in its OWN session +
    process group so a timeout can signal the WHOLE group (F1). POSIX honors
    ``start_new_session`` (a ``setsid`` in the child); Windows accepts the kwarg
    but lacks ``os.killpg`` — the kill path below degrades to the direct child
    there, so guard by capability, not by asserting the group exists."""
    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
        return {"start_new_session": True}
    return {}


# W1 (§3.5): os.name is process-constant; a module flag keeps the Windows
# branch below patchable in tests without touching the global ``os`` module.
_IS_WINDOWS = os.name == "nt"


def _kill_process_group(proc: "subprocess.Popen") -> None:
    """Kill the child's whole process TREE so a wrapper's orphaned
    grandchildren (the leaked GPU worker in F1) die with it.

    POSIX: SIGKILL the process group (byte-identical to the pre-W1 path).
    Windows (W1 §3.5): ``taskkill /PID <pid> /T /F`` walks and force-kills the
    child tree — the old direct-child-only degrade left grandchildren running
    forever. The direct ``proc.kill()`` stays as the belt either way (taskkill
    missing/refusing, tree already gone — benign races all)."""
    if _IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except Exception:
            pass  # taskkill unavailable/hung — the direct kill below still runs
        try:
            proc.kill()
        except OSError:
            pass
        return
    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass  # group vanished or unsignalable — fall through to the child kill
    try:
        proc.kill()
    except OSError:
        pass


def _reap(proc: "subprocess.Popen") -> tuple[str, str]:
    """Collect the killed child's captured output without hanging — a bounded
    second ``communicate`` after the SIGKILL. Returns ``(stdout, stderr)``,
    empty strings if it cannot be read."""
    for _ in range(2):
        try:
            out, err = proc.communicate(timeout=5)
            return _decode(out), _decode(err)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                break
        except (OSError, ValueError):
            break
    return "", ""
