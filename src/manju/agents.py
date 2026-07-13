"""Agent-CLI resolution for `manju auto` (§10 — the autopilot shell).

`manju auto` drives ANY one-shot agent CLI, not just Claude Code. The
underlying collaboration surface is already agent-agnostic (files + CLI +
MCP); this module generalizes the last Claude-specific piece — which binary
gets the composed prompt.

Resolution order (first hit wins):

1. ``--agent`` flag           — a template or a known agent name
2. ``MANJU_AGENT`` env var    — same grammar
3. ``project.yaml: agent:``   — same grammar, recorded per project
4. PATH probe                 — first of the known agents found on PATH

A *template* is a shell-style string containing ``{prompt}`` (e.g.
``claude -p {prompt}``); a bare *name* (``codex``) expands via KNOWN_AGENTS.
A template without ``{prompt}`` gets the prompt appended as one final
argument. Structured integrations should prefer `manju serve-mcp` — this
shell exists for agents that only speak "prompt in, work out".
"""

from __future__ import annotations

import os
import shlex
import shutil

# One-shot prompt invocation per agent CLI, verified against each tool's
# documented non-interactive mode. Bare names in --agent/MANJU_AGENT/
# project.yaml expand through this table; PATH probing walks it in order.
KNOWN_AGENTS: dict[str, str] = {
    "claude": "claude -p {prompt}",          # Claude Code
    "codex": "codex exec {prompt}",          # OpenAI Codex CLI
    "gemini": "gemini -p {prompt}",          # Gemini CLI
    "qwen": "qwen -p {prompt}",              # Qwen Code (gemini-cli lineage)
    "aider": "aider --message {prompt}",     # aider
}


class AgentResolutionError(RuntimeError):
    """No agent CLI could be resolved — carries the full how-to in one line."""


def _expand(spec: str) -> str:
    """A bare known name becomes its template; anything else IS a template."""
    return KNOWN_AGENTS.get(spec.strip(), spec)


def build_command(template: str, prompt: str) -> list[str]:
    """Split the template shell-style and substitute ``{prompt}`` as a WHOLE
    argument (never word-split, never quoted-injected). A template without
    the placeholder gets the prompt appended as the final argument."""
    # Windows gate round 3: POSIX shlex eats the backslashes out of real
    # Windows paths (the auto-agent's fake-agent invocation died with exit
    # 127, 1 event instead of 7). One owner: local_cmd's platform-aware split.
    from .providers.local_cmd import _split_command

    words = _split_command(template)
    if not words:
        raise AgentResolutionError("agent template is empty")
    if any("{prompt}" in w for w in words):
        return [w.replace("{prompt}", prompt) if "{prompt}" in w else w
                for w in words]
    return [*words, prompt]


def resolve_agent(project_agent: str | None, flag: str | None) -> str:
    """Return the agent TEMPLATE per the resolution order, or raise with a
    one-line message naming every way to configure one.

    Trust boundary (round W, review #83): ``--agent`` and ``MANJU_AGENT`` are
    machine-level (the operator typed them) and accept free-form templates.
    ``project.yaml:agent`` travels WITH the project — a downloaded project must
    not be able to run an arbitrary local command — so the project tier only
    accepts a KNOWN agent name; a template there is refused with the fix.
    """
    for source, spec in (("flag", flag),
                         ("env", os.environ.get("MANJU_AGENT")),
                         ("project", project_agent)):
        if spec and spec.strip():
            spec = spec.strip()
            if source == "project" and spec not in KNOWN_AGENTS:
                raise AgentResolutionError(
                    "project.yaml 里的 agent 只能是已知代理名("
                    + "/".join(KNOWN_AGENTS)
                    + f");收到自定义命令 {spec!r}。项目文件可能来自他人,"
                    "自定义模板请改用 --agent 或 MANJU_AGENT 在本机传入"
                )
            return _expand(spec)
    for name, template in KNOWN_AGENTS.items():
        if shutil.which(name):
            return template
    raise AgentResolutionError(
        "no agent CLI found — install one of "
        + "/".join(KNOWN_AGENTS)
        + ", or set --agent/MANJU_AGENT/project.yaml:agent to a template like "
        + '"claude -p {prompt}" (structured integrations: manju serve-mcp)'
    )
