"""FP plugin-api — the provider plugin surface is FROZEN as a v1 contract.

Roadmap item 10 (stable plugin interfaces, explicitly NO marketplace): third
parties extend Manju exactly two ways today — a ``provider.yaml`` manifest
(``generic_cloud`` or the ``module:Class`` adapter escape hatch) or a
:class:`manju.providers.base.Provider` subclass registered via
``register_provider``. Both already work; what was missing is the PROMISE that
they keep working. This file is that promise's teeth:

  * the surface carries a declared contract id (``manju.provider-plugin-api/v1``)
    registered in CONTRACTS.yaml as a stable schema row, so the existing
    literal⇄registry consistency tests (test_fp_contracts) bind code and
    registry in both directions;
  * signatures, member floors and constructor-compat laws are pinned so a
    plugin written against v1 cannot be broken by an additive refactor —
    additions stay legal (floors, keyword-only-with-default rules), renames
    and removals go RED here.

Genre note: these are SURFACE pins. Provider *behaviour* (retry classes,
submission admission, fallback walking) is owned by the existing behaviour
suites (test_providers*, test_dr06*, test_p0*) and is not re-tested here —
except two one-line semantic promises plugin authors build against (string
enum values; the chain never dead-ends), each pinned once.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from manju.core import contracts
from manju.providers import base as B
from manju.providers import registry as R

PLUGIN_API_ID = "manju.provider-plugin-api/v1"


# ------------------------------------------------------------ contract identity


def test_plugin_api_contract_declared_in_code():
    """The frozen surface names its contract id in code — the same literal the
    registry row carries, so the F0 literal⇄registry tests bind them."""
    assert B.PLUGIN_API_CONTRACT == PLUGIN_API_ID


def test_plugin_api_registered_stable():
    """CONTRACTS.yaml carries the row: stable (additive-only from now on),
    owned by the module that defines the ABC, no downgrade promise."""
    e = contracts.entry(PLUGIN_API_ID)
    assert e["kind"] == "schema"
    assert e["status"] == "stable"
    assert e["owner"] == "manju.providers.base"
    assert e["read_older"] is True
    assert e["write_older"] is False


# ------------------------------------------------------------- exported surface


def test_package_exports_are_a_floor():
    """Everything a plugin author imports rides ``manju.providers`` — the names
    below may never disappear from ``__all__`` (additions are fine)."""
    import manju.providers as P

    required = {
        "FailureKind", "ProviderFailure", "NeedsHumanInput",
        "GenerationRequest", "Provider", "CloudProvider",
        "get_provider", "available_providers", "register_provider",
        "fallback_chain", "generate_with_fallback",
    }
    missing = required - set(P.__all__)
    assert not missing, f"plugin-facing exports vanished from __all__: {sorted(missing)}"


def test_failure_kinds_are_a_floor_with_stable_values():
    """The five v1 failure kinds exist forever, and each serializes to its own
    name (str-enum — these values are on disk in reports/failures.jsonl)."""
    assert issubclass(B.FailureKind, str)
    for name in ("rate_limited", "timeout", "content_rejected",
                 "provider_error", "invalid"):
        member = B.FailureKind[name]
        assert member.value == name


# ------------------------------------------------- constructor-compat laws


def test_generation_request_head_fields_and_additive_law():
    """(a) The five head fields keep their names, order and requiredness —
    ``GenerationRequest(project, shot, bible, spec_hash, duration_ms)`` is the
    v1 constructor shape. (b) EVERY other field must carry a default: the
    additive-only law that has kept each new field ("additive and
    default-absent" throughout base.py) from breaking existing plugin and test
    call sites — now enforced rather than remembered."""
    fields = dataclasses.fields(B.GenerationRequest)
    head = [f.name for f in fields[:5]]
    assert head == ["project", "shot", "bible", "spec_hash", "duration_ms"]
    for f in fields[:5]:
        assert f.default is dataclasses.MISSING and \
            f.default_factory is dataclasses.MISSING, (
                f"head field {f.name} grew a default — v1 keeps it required")
    for f in fields[5:]:
        assert f.default is not dataclasses.MISSING or \
            f.default_factory is not dataclasses.MISSING, (
                f"field {f.name} has NO default — that breaks every existing "
                f"GenerationRequest(...) call site; new fields must be additive")


def test_provider_failure_signature_and_attributes():
    """``ProviderFailure(kind, message, *, detail=None, disposition=None)`` —
    and the four attributes plugins read after catching it."""
    params = inspect.signature(B.ProviderFailure.__init__).parameters
    names = list(params)
    assert names[:3] == ["self", "kind", "message"]
    for kw in ("detail", "disposition"):
        assert params[kw].kind is inspect.Parameter.KEYWORD_ONLY
        assert params[kw].default is None
    assert issubclass(B.ProviderFailure, RuntimeError)

    exc = B.ProviderFailure(B.FailureKind.timeout, "boom")
    assert (exc.kind, exc.message, exc.detail, exc.disposition) == (
        B.FailureKind.timeout, "boom", {}, None)


def test_cloud_provider_kwargs_are_keyword_only_with_defaults():
    """The six documented CloudProvider knobs stay keyword-only WITH defaults —
    a subclass calling ``super().__init__()`` bare must keep working; new knobs
    must follow the same rule (checked for every parameter, present and
    future)."""
    params = inspect.signature(B.CloudProvider.__init__).parameters
    documented = {"timeout_s", "base_delay", "max_delay", "max_retries",
                  "sleep_fn", "logger"}
    assert documented <= set(params), (
        f"documented CloudProvider kwargs vanished: {sorted(documented - set(params))}")
    for name, p in params.items():
        if name == "self":
            continue
        assert p.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"CloudProvider.__init__ parameter {name} must be keyword-only")
        assert p.default is not inspect.Parameter.empty, (
            f"CloudProvider.__init__ parameter {name} must have a default")


def test_registry_entry_point_signatures():
    """The four registry entry points a plugin calls, by name and shape."""
    assert list(inspect.signature(R.register_provider).parameters) == ["provider"]
    assert list(inspect.signature(R.get_provider).parameters) == ["name"]
    assert list(inspect.signature(R.fallback_chain).parameters) == ["shot"]

    gwf = inspect.signature(R.generate_with_fallback).parameters
    assert list(gwf)[:2] == ["req", "chain"]
    assert gwf["chain"].default is None
    assert gwf["log"].kind is inspect.Parameter.KEYWORD_ONLY
    assert gwf["log"].default is None


# ------------------------------------------------- the plugin-author proofs


def test_minimal_provider_subclass_registers_and_resolves():
    """The documented minimal surface is SUFFICIENT: ``id`` + ``generate`` is a
    working provider. If the ABC ever grows a new abstract member, this fails —
    which is exactly the review moment it should force (a new abstract breaks
    every shipped v1 plugin)."""

    class _TinyPlugin(B.Provider):
        id = "_fp_freeze_tiny"

        def generate(self, req):  # pragma: no cover - never called
            return []

    plugin = _TinyPlugin()
    assert plugin.kind == "local"  # the documented default
    R.register_provider(plugin)
    try:
        assert R.get_provider("_fp_freeze_tiny") is plugin
        assert "_fp_freeze_tiny" in R.available_providers()
    finally:
        R._REGISTRY.pop("_fp_freeze_tiny", None)


def test_cloud_provider_abstract_trio_is_exactly_enforced():
    """submit/poll/download is the whole cloud contract: implementing the trio
    instantiates (with every documented kwarg); omitting one member refuses."""

    class _Cloud(B.CloudProvider):
        id = "_fp_freeze_cloud"

        def submit(self, req):  # pragma: no cover
            return "job-1"

        def poll(self, job_id):  # pragma: no cover
            return "succeeded", {}

        def download(self, job_id, dest_dir):  # pragma: no cover
            return []

    _Cloud(timeout_s=1.0, base_delay=0.1, max_delay=0.2, max_retries=0,
           sleep_fn=lambda s: None, logger=None)

    class _Partial(B.CloudProvider):
        id = "_fp_freeze_partial"

        def submit(self, req):  # pragma: no cover
            return "job-1"

        def poll(self, job_id):  # pragma: no cover
            return "succeeded", {}

    with pytest.raises(TypeError):
        _Partial()  # download missing → abstract


# ------------------------------------------------- manifest plugin path


def test_manifest_surface_floor():
    """A provider.yaml written against v1 keeps validating: the adapter default
    name and the manifest fields it fills may never disappear."""
    from manju.providers.manifest import GENERIC_ADAPTER, ProviderManifest

    assert GENERIC_ADAPTER == "generic_cloud"
    required = {"id", "type", "adapter", "capabilities", "disabled",
                "limits", "auth", "submit", "poll", "cost"}
    missing = required - set(ProviderManifest.model_fields)
    assert not missing, f"manifest fields vanished: {sorted(missing)}"


def test_broken_or_shadowing_manifest_never_breaks_the_registry(tmp_path, monkeypatch):
    """The two protective rules of the manifest path, pinned:
    (a) an adapter string that is not 'module:Class' surfaces in
        manifest_errors() and the registry still serves the builtins;
    (b) a manifest id shadowing a builtin is skipped with an error — a plugin
        can never replace manual_import/ffmpeg_kenburns/caption_card."""
    bad = tmp_path / "badplug"
    bad.mkdir()
    (bad / "provider.yaml").write_text(
        "id: badplug\ntype: video\nadapter: not_a_module_class_string\n",
        encoding="utf-8")
    shadow = tmp_path / "caption_card"
    shadow.mkdir()
    (shadow / "provider.yaml").write_text(
        "id: caption_card\ntype: video\nadapter: generic_cloud\n",
        encoding="utf-8")
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path))

    provs = R.available_providers()
    for builtin in ("manual_import", "ffmpeg_kenburns", "caption_card"):
        assert builtin in provs
    assert type(provs["caption_card"]).__name__ == "CaptionCardProvider"

    errors = "\n".join(R.manifest_errors())
    assert "module:Class" in errors
    assert "shadows a built-in provider" in errors


def test_fallback_chain_never_dead_ends():
    """§8.4's one promise plugin authors rely on when they DON'T handle a shot:
    the chain always terminates at the network-independent caption_card."""
    from manju.core.models import ShotSpec

    chain = R.fallback_chain(ShotSpec(id="s1"))
    assert chain and chain[-1] == "caption_card"


def test_module_class_adapter_end_to_end(tmp_path, monkeypatch):
    """The HAPPY path of the escape hatch, as a realistic third-party plugin:
    a real out-of-tree module resolves via ``adapter: module:Class``, the
    registry instantiates it as ``cls(manifest)`` — THE adapter constructor
    convention, pinned here because registry.py calls exactly that — and a
    declared capability slots the plugin into shots' fallback chains ahead of
    the locals (the §8.6 promise that a filled-in manifest needs no code
    changes anywhere else)."""
    from manju.core.models import ShotSpec

    pkg = tmp_path / "pypath"
    pkg.mkdir()
    (pkg / "acme_plugin_freeze.py").write_text(
        "from manju.providers.base import Provider\n"
        "class AcmeProvider(Provider):\n"
        "    kind = 'cloud'\n"
        "    def __init__(self, manifest):\n"
        "        self.manifest = manifest\n"
        "        self.id = manifest.id\n"
        "    def generate(self, req):\n"
        "        return []\n",
        encoding="utf-8")
    provdir = tmp_path / "providers"
    (provdir / "acme_video").mkdir(parents=True)
    (provdir / "acme_video" / "provider.yaml").write_text(
        "id: acme_video\ntype: video\nadapter: acme_plugin_freeze:AcmeProvider\n"
        "capabilities: [image_to_video]\n",
        encoding="utf-8")
    monkeypatch.syspath_prepend(str(pkg))
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(provdir))

    provs = R.available_providers()
    plugin = provs.get("acme_video")
    assert plugin is not None and type(plugin).__name__ == "AcmeProvider"
    assert plugin.id == "acme_video"
    assert plugin.manifest.id == "acme_video"  # cls(manifest) convention
    assert R.manifest_errors() == []

    # image_to_video has no local builtin -> the plugin fills the step, and
    # the chain still terminates network-independent.
    chain = R.fallback_chain(ShotSpec(id="s1"))
    assert chain[0] == "acme_video" and chain[-1] == "caption_card"
