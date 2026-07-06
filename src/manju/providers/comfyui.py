"""ComfyUI adapter (§8.6 ``module:Class`` escape hatch, like EdgeTts).

ComfyUI is a local node-graph engine; it speaks a submit/poll/download REST
shape but with a JSON *workflow graph* body the generic_cloud adapter cannot
express, so it gets a dedicated adapter class. There is NO websocket use here —
we poll ``GET /history/{prompt_id}`` (scope decision).

Verified against the current ComfyUI HTTP API::

    POST /prompt            body {"prompt": <API-format graph>, "client_id": ...}
                            -> 200 {"prompt_id", "number", "node_errors": {}}
                            -> 400 {"error": ..., "node_errors": {...}}  (bad graph)
    GET  /history/{id}      -> {} while running; when done:
                               {id: {"outputs": {node_id: {"images"|"gifs"|
                                     "videos": [{filename, subfolder, type}]}},
                                     "status": {"status_str": "success"|"error",
                                                "completed": bool,
                                                "messages": [[event, data], ...]}}}
                               execution errors carry an ["execution_error",
                               {node_id, node_type, exception_message, ...}] message
    GET  /view?filename=&subfolder=&type=   -> raw bytes

Manifest (``comfyui`` section)::

    # ~/.manju/providers/comfyui/provider.yaml
    id: comfyui
    type: video
    adapter: manju.providers.comfyui:ComfyUIProvider
    capabilities: [image_to_video, first_last_frame, text_to_video]
    cost: {per_call: 0.0, currency: CNY}     # local: free (duration is recorded)
    comfyui:
      base_url: http://127.0.0.1:8188
      workflow_file: comfyui/wan_i2v_api.json   # project-relative, "Save (API Format)"
      input_map:                                 # "node_id.input_name" -> template
        "6.text": "{prompt}"                     # bare -> typed; embedded -> string
        "3.seed": "{seed}"
        "50.width": "{width}"
        "50.height": "{height}"
        "52.image": "{image}"
      poll_interval_s: 1.0
      # output_node: "9"                         # optional: pin the output node
      output_prefer: [videos, gifs, images]      # first video wins, else gif, else image

Costs are 0 (local); the take's lineage still records the workflow file hash,
the mapped node inputs, the prompt_id and the wall-clock duration (§4.2).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from ..core.container import Project, TakeInfo
from ..core.hashing import hash_text
from ..core.models import RemoteJobInfo
from .base import FailureKind, GenerationRequest, Provider, ProviderFailure
from .generic_cloud import Transport, default_transport, render_body
from .manifest import COMFYUI_ADAPTER, ProviderManifest

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


class ComfyUIProvider(Provider):
    """Drive a local ComfyUI graph: load workflow -> map inputs -> POST /prompt
    -> poll /history -> download first matching output -> register a take."""

    kind = "local"

    def __init__(
        self,
        manifest: ProviderManifest,
        *,
        transport: Transport | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        client_id: str | None = None,
    ):
        self.manifest = manifest
        self.id = manifest.id
        cfg = manifest.comfyui
        if not cfg.workflow_file:
            raise ValueError(f"manifest {manifest.id}: comfyui.workflow_file is required")
        if not cfg.base_url.startswith(("http://", "https://")):
            raise ValueError(
                f"manifest {manifest.id}: comfyui.base_url is not an HTTP(S) URL"
            )
        self._cfg = cfg
        self._base = cfg.base_url.rstrip("/")
        self._transport = transport or default_transport
        self._sleep = sleep_fn
        self._clock = clock
        self._client_id = client_id or uuid.uuid4().hex

    # ------------------------------------------------------------- Provider

    def generate(self, req: GenerationRequest) -> list[TakeInfo]:
        workflow_text, wf_rel = self._load_workflow(req)
        workflow = self._parse_workflow(workflow_text, wf_rel)
        values = self._values(req)
        mapped = self._apply_input_map(workflow, values)

        t0 = self._clock()
        prompt_id = self._submit(workflow)
        outputs = self._poll(prompt_id)
        chosen = self._select_output(outputs)
        if chosen is None:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: ComfyUI prompt {prompt_id} produced no image/video "
                f"outputs (looked for {self._cfg.output_prefer} across "
                f"{sorted(outputs)})",
                detail={"prompt_id": prompt_id, "outputs": outputs},
            )

        tmp = Path(tempfile.mkdtemp(prefix=f"comfyui_{req.shot.id}_"))
        try:
            media = self._download(chosen, tmp)
            elapsed = round(self._clock() - t0, 3)
            take = self._register(
                req,
                media,
                params={
                    "workflow_file": wf_rel,
                    "workflow_sha256": hash_text(workflow_text),
                    "prompt_id": prompt_id,
                    "mapped_inputs": mapped,
                    "output": chosen,
                    "base_url": self._base,
                    "duration_s": elapsed,  # local: cost 0, but duration is recorded
                },
                compiled_prompt=str(values.get("prompt") or "") or None,
                remote=RemoteJobInfo(
                    job_id=prompt_id, cost=0.0, currency=self.manifest.cost.currency
                ),
            )
            return [take]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ------------------------------------------------------- workflow + map

    def _load_workflow(self, req: GenerationRequest) -> tuple[str, str]:
        rel = self._cfg.workflow_file or ""
        try:
            path = req.project.resolve(rel)
        except Exception as exc:  # ProjectError: escapes root, etc.
            raise ProviderFailure(
                FailureKind.invalid, f"{self.id}: bad comfyui.workflow_file {rel!r}: {exc}"
            ) from exc
        if not path.exists():
            raise ProviderFailure(
                FailureKind.invalid,
                f"{self.id}: workflow file not found: {rel} "
                "(export it from ComfyUI with 'Save (API Format)')",
            )
        return path.read_text(encoding="utf-8"), rel

    def _parse_workflow(self, text: str, rel: str) -> dict:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderFailure(
                FailureKind.invalid, f"{self.id}: workflow {rel} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(data, dict) or not data:
            raise ProviderFailure(
                FailureKind.invalid,
                f"{self.id}: workflow {rel} is not an API-format graph "
                "(expected an object keyed by node id)",
            )
        return data

    def _values(self, req: GenerationRequest) -> dict[str, object]:
        from .prompt import compile_prompt

        config = req.project.load_config()
        values: dict[str, object] = {
            "width": config.width,
            "height": config.height,
            "fps": config.fps,
            "duration_s": req.duration_ms / 1000.0,
            "duration_ms": req.duration_ms,
            "seed": req.params.get("seed", 0),
            "shot_id": req.shot.id,
        }
        values.update(req.params)  # shot generation.params verbatim (§8.6)
        # prompt + resolved image are authoritative — set after the params merge
        values["prompt"] = compile_prompt(req.shot, req.bible)
        values["image"] = _resolve_ref_image(req)
        return values

    def _apply_input_map(self, workflow: dict, values: dict) -> dict[str, object]:
        """Write templated values into ``workflow[node_id]['inputs'][name]``.
        Reuses generic_cloud's render_body typing: a bare ``{seed}`` stays an
        int, ``{width}x{height}`` becomes a string. Returns the applied map for
        the take's lineage."""
        mapped: dict[str, object] = {}
        for target, template in self._cfg.input_map.items():
            node_id, _, input_name = target.partition(".")
            if not node_id or not input_name:
                raise ProviderFailure(
                    FailureKind.invalid,
                    f"{self.id}: input_map key {target!r} must be 'node_id.input_name'",
                )
            node = workflow.get(node_id)
            if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                raise ProviderFailure(
                    FailureKind.invalid,
                    f"{self.id}: workflow has no node {node_id!r} with an inputs "
                    f"object (input_map key {target!r})",
                )
            value = render_body(template, values)  # raises invalid on unknown placeholder
            node["inputs"][input_name] = value
            mapped[target] = value
        return mapped

    # ---------------------------------------------------- submit/poll/view

    def _http(self, method: str, url: str, body: bytes | None = None):
        headers = {"Content-Type": "application/json"} if body is not None else {}
        try:
            return self._transport(method, url, headers, body)
        except ProviderFailure as exc:
            # default_transport wraps URLError/OSError as a timeout ProviderFailure;
            # for a local server that always means "unreachable" — reframe it.
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: cannot reach ComfyUI at {self._base} — is ComfyUI "
                f"running? ({exc})",
                detail={"base_url": self._base, "url": url},
            ) from exc
        except OSError as exc:  # a raw connection error from a custom transport
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: cannot reach ComfyUI at {self._base} — is ComfyUI "
                f"running? ({exc})",
                detail={"base_url": self._base, "url": url},
            ) from exc

    def _submit(self, workflow: dict) -> str:
        body = json.dumps(
            {"prompt": workflow, "client_id": self._client_id}, ensure_ascii=False
        ).encode("utf-8")
        resp = self._http("POST", f"{self._base}/prompt", body)
        if resp.status >= 400:
            # graph validation failure: surface error + node_errors verbatim
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: ComfyUI rejected the workflow (HTTP {resp.status}): "
                f"{resp.text()[:800]}",
                detail={"status": resp.status, "body": resp.text()[:2000]},
            )
        try:
            data = resp.json()
            prompt_id = data["prompt_id"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: no prompt_id in ComfyUI /prompt response ({exc})",
                detail={"body": resp.text()[:800]},
            ) from exc
        return str(prompt_id)

    def _poll(self, prompt_id: str) -> dict:
        start = self._clock()
        while True:
            resp = self._http("GET", f"{self._base}/history/{prompt_id}")
            if resp.status >= 400:
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: /history/{prompt_id} failed (HTTP {resp.status})",
                    detail={"prompt_id": prompt_id},
                )
            try:
                data = resp.json() or {}
            except json.JSONDecodeError as exc:
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: /history/{prompt_id} returned non-JSON ({exc})",
                ) from exc
            entry = data.get(prompt_id)
            if entry:  # prompt_id only appears once the job is queued+done
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise self._node_error(prompt_id, status)
                return entry.get("outputs") or {}
            if self._clock() - start >= self._cfg.poll_timeout_s:
                raise ProviderFailure(
                    FailureKind.timeout,
                    f"{self.id}: ComfyUI prompt {prompt_id} did not finish within "
                    f"{self._cfg.poll_timeout_s}s on {self._base}",
                    detail={"prompt_id": prompt_id},
                )
            self._sleep(self._cfg.poll_interval_s)

    def _node_error(self, prompt_id: str, status: dict) -> ProviderFailure:
        node_id = node_type = None
        message = ""
        for msg in status.get("messages") or []:
            if (
                isinstance(msg, (list, tuple))
                and len(msg) == 2
                and msg[0] == "execution_error"
            ):
                data = msg[1] or {}
                node_id = data.get("node_id")
                node_type = data.get("node_type")
                message = data.get("exception_message") or data.get("exception_type") or ""
                break
        where = f"node {node_id}" + (f" ({node_type})" if node_type else "")
        detail = f"{where}: {message}".strip() if node_id else "execution error"
        return ProviderFailure(
            FailureKind.provider_error,
            f"{self.id}: ComfyUI prompt {prompt_id} failed — {detail}",
            detail={"prompt_id": prompt_id, "node_id": node_id, "status": status},
        )

    def _select_output(self, outputs: dict) -> dict | None:
        """First video output wins, else first gif/sequence, else first image
        (``output_prefer``). ``output_node`` pins the search to one node."""
        node_ids = (
            [self._cfg.output_node]
            if self._cfg.output_node
            else sorted(outputs, key=_node_sort_key)
        )
        for key in self._cfg.output_prefer:
            for nid in node_ids:
                entries = (outputs.get(nid) or {}).get(key)
                if entries:
                    entry = entries[0]
                    return {
                        "node_id": nid,
                        "output_key": key,
                        "filename": entry.get("filename"),
                        "subfolder": entry.get("subfolder", ""),
                        "type": entry.get("type", "output"),
                    }
        return None

    def _download(self, chosen: dict, dest_dir: Path) -> Path:
        query = urlencode(
            {
                "filename": chosen["filename"],
                "subfolder": chosen.get("subfolder", ""),
                "type": chosen.get("type", "output"),
            }
        )
        resp = self._http("GET", f"{self._base}/view?{query}")
        if resp.status >= 400 or not resp.body:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: failed to download {chosen['filename']!r} from "
                f"ComfyUI (HTTP {resp.status})",
                detail={"output": chosen},
            )
        suffix = Path(chosen["filename"]).suffix or ".png"
        dest = dest_dir / f"comfyui{suffix}"
        dest.write_bytes(resp.body)
        return dest


def _node_sort_key(node_id: str):
    """Numeric node ids sort numerically ("9" before "10"), others lexically."""
    s = str(node_id)
    return (0, int(s)) if s.isdigit() else (1, s)


def _resolve_ref_image(req: GenerationRequest) -> str:
    """Resolve a reference image for {image}: explicit param, then a bible
    ref_image (characters then scene). Returns an absolute path string, or ""."""
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
