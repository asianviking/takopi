from pathlib import Path

from takopi.config import ProjectConfig, ProjectsConfig
from takopi.context import RunContext
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import Return, ScriptRunner
from takopi.transport_runtime import TransportMessageContext, TransportRuntime


def _make_runtime(*, project_default_engine: str | None = None) -> TransportRuntime:
    codex = ScriptRunner([Return(answer="ok")], engine="codex")
    pi = ScriptRunner([Return(answer="ok")], engine="pi")
    router = AutoRouter(
        entries=[
            RunnerEntry(engine=codex.engine, runner=codex),
            RunnerEntry(engine=pi.engine, runner=pi),
        ],
        default_engine=codex.engine,
    )
    project = ProjectConfig(
        alias="proj",
        path=Path("."),
        worktrees_dir=Path(".worktrees"),
        default_engine=project_default_engine,
    )
    projects = ProjectsConfig(projects={"proj": project}, default_project=None)
    return TransportRuntime(router=router, projects=projects)


def test_resolve_engine_uses_project_default() -> None:
    runtime = _make_runtime(project_default_engine="pi")
    engine = runtime.resolve_engine(
        engine_override=None,
        context=RunContext(project="proj"),
    )
    assert engine == "pi"


def test_resolve_engine_prefers_override() -> None:
    runtime = _make_runtime(project_default_engine="pi")
    engine = runtime.resolve_engine(
        engine_override="codex",
        context=RunContext(project="proj"),
    )
    assert engine == "codex"


def test_resolve_message_uses_transport_context_project_hint() -> None:
    runtime = _make_runtime(project_default_engine="pi")
    transport_ctx = TransportMessageContext(project_hint="proj")
    resolved = runtime.resolve_message(
        text="hello",
        reply_text=None,
        transport_context=transport_ctx,
    )
    assert resolved.context is not None
    assert resolved.context.project == "proj"
    assert resolved.engine_override == "pi"  # project default engine


def test_resolve_message_uses_transport_context_branch_hint() -> None:
    runtime = _make_runtime()
    transport_ctx = TransportMessageContext(project_hint="proj", branch_hint="feat/api")
    resolved = runtime.resolve_message(
        text="hello",
        reply_text=None,
        transport_context=transport_ctx,
    )
    assert resolved.context is not None
    assert resolved.context.project == "proj"
    assert resolved.context.branch == "feat/api"


def test_resolve_message_directive_overrides_transport_context() -> None:
    runtime = _make_runtime(project_default_engine="pi")
    transport_ctx = TransportMessageContext(project_hint="proj", branch_hint="feat/old")
    # User explicitly specifies @branch in message text
    resolved = runtime.resolve_message(
        text="@feat/new hello",
        reply_text=None,
        transport_context=transport_ctx,
    )
    assert resolved.context is not None
    # Project comes from transport hint (no /project directive)
    assert resolved.context.project == "proj"
    # Branch comes from explicit directive, not hint
    assert resolved.context.branch == "feat/new"


def test_resolve_message_project_directive_overrides_transport_context() -> None:
    runtime = _make_runtime()
    transport_ctx = TransportMessageContext(project_hint="proj")
    # User explicitly specifies /project in message text (but proj is only project)
    resolved = runtime.resolve_message(
        text="/proj hello",
        reply_text=None,
        transport_context=transport_ctx,
    )
    assert resolved.context is not None
    assert resolved.context.project == "proj"


def test_resolve_message_without_transport_context_unchanged() -> None:
    runtime = _make_runtime()
    # Without transport_context, behavior is unchanged
    resolved = runtime.resolve_message(
        text="hello",
        reply_text=None,
        transport_context=None,
    )
    # No context when no project directive, no transport hint, no default project
    assert resolved.context is None
