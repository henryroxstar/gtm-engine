"""Phase C contract tests: data-spine types and storage-adapter invariants.

Covers:
  - WorkspaceId validation (reject traversal chars, empty strings)
  - Workspace slug validation
  - TenantContext validation + content_path() output format
  - LocalStorageAdapter: read/write/exists/list_prefix/delete round-trips
  - LocalStorageAdapter: path-traversal rejection
  - StorageAdapter.delete_workspace(): cascades all tenant files
"""

import pytest

from gtm_core.capabilities import Entitlement
from gtm_core.tenancy import (
    LocalStorageAdapter,
    TenantContext,
    Workspace,
    WorkspaceId,
    _check_storage_path,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ws(slug: str = "acme-corp") -> Workspace:
    return Workspace(
        id=WorkspaceId("ws-abc123"),
        slug=slug,
        owner_user_id="user-xyz",
        entitlement=Entitlement.PRO,
    )


def _make_ctx(profile: str = "example", slug: str = "acme-corp") -> TenantContext:
    return TenantContext(workspace=_make_ws(slug), profile_name=profile)


# ── WorkspaceId ───────────────────────────────────────────────────────────────


def test_workspace_id_valid():
    wid = WorkspaceId("550e8400-e29b-41d4-a716-446655440000")
    assert str(wid) == "550e8400-e29b-41d4-a716-446655440000"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "/etc/passwd",
        "../../escape",
        "ws\x00null",
        "ws\\backslash",
        ".",
        "..",
    ],
)
def test_workspace_id_rejects_unsafe(bad):
    with pytest.raises(ValueError):
        WorkspaceId(bad)


# ── Workspace ─────────────────────────────────────────────────────────────────


def test_workspace_valid():
    ws = _make_ws("acme-corp")
    assert ws.slug == "acme-corp"
    assert ws.entitlement == Entitlement.PRO


@pytest.mark.parametrize(
    "bad_slug",
    [
        "",
        "acme/corp",
        "acme\\corp",
        "../../escape",
        ".",
        "..",
        "acme\x00corp",
    ],
)
def test_workspace_rejects_unsafe_slug(bad_slug):
    with pytest.raises(ValueError):
        Workspace(
            id=WorkspaceId("ws-1"),
            slug=bad_slug,
            owner_user_id="u1",
            entitlement=Entitlement.FREE,
        )


# ── TenantContext ─────────────────────────────────────────────────────────────


def test_tenant_context_workspace_id_property():
    ctx = _make_ctx()
    assert ctx.workspace_id == WorkspaceId("ws-abc123")


def test_content_path_no_parts():
    ctx = _make_ctx()
    assert ctx.content_path() == "workspace/ws-abc123/profile/example"


def test_content_path_with_parts():
    ctx = _make_ctx()
    p = ctx.content_path("history.jsonl")
    assert p == "workspace/ws-abc123/profile/example/history.jsonl"


def test_content_path_nested():
    ctx = _make_ctx()
    p = ctx.content_path("runs", "run-001.json")
    assert p == "workspace/ws-abc123/profile/example/runs/run-001.json"


def test_content_path_rejects_traversal():
    ctx = _make_ctx()
    with pytest.raises(ValueError):
        ctx.content_path("../escape.txt")


@pytest.mark.parametrize(
    "bad_profile",
    [
        "",
        "/etc",
        "../../escape",
        "example\x00",
        "example\\",
        ".",
        "..",
    ],
)
def test_tenant_context_rejects_unsafe_profile(bad_profile):
    with pytest.raises(ValueError):
        TenantContext(workspace=_make_ws(), profile_name=bad_profile)


# ── _check_storage_path helper ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "safe",
    [
        "history.jsonl",
        "runs/run-001.json",
        "accounts/sggc/dossier.docx",
    ],
)
def test_check_storage_path_valid(safe):
    _check_storage_path(safe)  # must not raise


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "/absolute/path",
        "../escape",
        "runs/../../../etc/passwd",
        "nul\x00char",
        "back\\slash",
    ],
)
def test_check_storage_path_rejects_unsafe(bad):
    with pytest.raises(ValueError):
        _check_storage_path(bad)


# ── LocalStorageAdapter ───────────────────────────────────────────────────────


@pytest.fixture()
def adapter(tmp_path):
    return LocalStorageAdapter(tmp_path)


@pytest.fixture()
def ctx():
    return _make_ctx()


def test_write_then_read(adapter, ctx):
    adapter.write(ctx, "history.jsonl", b'{"event":"test"}\n')
    assert adapter.read(ctx, "history.jsonl") == b'{"event":"test"}\n'


def test_exists_false_before_write(adapter, ctx):
    assert not adapter.exists(ctx, "missing.txt")


def test_exists_true_after_write(adapter, ctx):
    adapter.write(ctx, "flag.txt", b"1")
    assert adapter.exists(ctx, "flag.txt")


def test_nested_write_creates_dirs(adapter, ctx):
    adapter.write(ctx, "runs/r1/manifest.json", b"{}")
    assert adapter.exists(ctx, "runs/r1/manifest.json")


def test_list_prefix_empty_before_any_write(adapter, ctx):
    assert list(adapter.list_prefix(ctx, "")) == []


def test_list_prefix_returns_all_files(adapter, ctx):
    adapter.write(ctx, "a.txt", b"a")
    adapter.write(ctx, "b.txt", b"b")
    adapter.write(ctx, "sub/c.txt", b"c")
    paths = list(adapter.list_prefix(ctx, ""))
    assert sorted(paths) == ["a.txt", "b.txt", "sub/c.txt"]


def test_list_prefix_scoped_to_profile(adapter, ctx):
    """Files written for one profile are invisible to another profile's context."""
    other_ctx = TenantContext(workspace=_make_ws(), profile_name="example2")
    adapter.write(ctx, "secret.txt", b"example-only")
    assert list(adapter.list_prefix(other_ctx, "")) == []


def test_delete_removes_file(adapter, ctx):
    adapter.write(ctx, "temp.txt", b"x")
    adapter.delete(ctx, "temp.txt")
    assert not adapter.exists(ctx, "temp.txt")


def test_delete_is_noop_if_missing(adapter, ctx):
    adapter.delete(ctx, "nonexistent.txt")  # must not raise


def test_delete_workspace_removes_all_files(adapter, ctx):
    adapter.write(ctx, "history.jsonl", b"line1\n")
    adapter.write(ctx, "costs.jsonl", b"cost1\n")
    adapter.write(ctx, "runs/r1.json", b"{}")
    adapter.delete_workspace(ctx)
    assert list(adapter.list_prefix(ctx, "")) == []


def test_delete_workspace_does_not_touch_other_profiles(adapter, ctx):
    other_ctx = TenantContext(workspace=_make_ws(), profile_name="example2")
    adapter.write(ctx, "mine.txt", b"example")
    adapter.write(other_ctx, "theirs.txt", b"example2")
    adapter.delete_workspace(ctx)
    assert list(adapter.list_prefix(ctx, "")) == []
    assert list(adapter.list_prefix(other_ctx, "")) == ["theirs.txt"]


# ── Traversal rejection in LocalStorageAdapter ────────────────────────────────


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.txt",
        "../../etc/passwd",
        "/absolute/path",
        "sub/../../escape",
    ],
)
def test_write_rejects_traversal(adapter, ctx, bad_path):
    with pytest.raises((ValueError, OSError)):
        adapter.write(ctx, bad_path, b"attack")


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.txt",
        "/absolute/path",
    ],
)
def test_read_rejects_traversal(adapter, ctx, bad_path):
    with pytest.raises((ValueError, OSError, FileNotFoundError)):
        adapter.read(ctx, bad_path)


def test_exists_returns_false_for_traversal(adapter, ctx):
    # exists() must not raise on traversal — just return False safely
    assert not adapter.exists(ctx, "../escape.txt")
