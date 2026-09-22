"""Contract test for Virtual File System (VFS) parity and security boundaries.

Verifies that LocalVFS and CloudVFS (in-memory/mock implementation) exhibit:
1. Byte-for-byte and text read/write parity across all I/O operations.
2. Identical path traversal rejection (e.g. `../`, null bytes, absolute paths).
3. Consistent metadata checks (`exists`, `is_file`, `is_dir`, `list_dir`, `glob`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.vfs import VFS, CloudVFS, LocalVFS


@pytest.fixture
def local_vfs(tmp_path: Path) -> LocalVFS:
    return LocalVFS(root=tmp_path)


@pytest.fixture
def cloud_vfs() -> CloudVFS:
    return CloudVFS(bucket="test-tenant-bucket", prefix="workspaces/test-ws")


@pytest.mark.parametrize("vfs_fixture", ["local_vfs", "cloud_vfs"])
def test_vfs_text_and_bytes_roundtrip(vfs_fixture: str, request: pytest.FixtureRequest):
    vfs: VFS = request.getfixturevalue(vfs_fixture)

    # 1. Text roundtrip
    text_content = "Hello GTM Content OS!\nLine 2 with utf-8: \u2713 \u2014 \u00e9"
    vfs.write_text("knowledge/brand.md", text_content)
    assert vfs.exists("knowledge/brand.md")
    assert vfs.is_file("knowledge/brand.md")
    assert not vfs.is_dir("knowledge/brand.md")
    assert vfs.read_text("knowledge/brand.md") == text_content

    # 2. Bytes roundtrip
    binary_content = b"\x00\x01\x02\xfe\xff\r\nPNG\x89"
    vfs.write_bytes("assets/logo.png", binary_content)
    assert vfs.exists("assets/logo.png")
    assert vfs.is_file("assets/logo.png")
    assert vfs.read_bytes("assets/logo.png") == binary_content


@pytest.mark.parametrize("vfs_fixture", ["local_vfs", "cloud_vfs"])
def test_vfs_directory_listing_and_glob(vfs_fixture: str, request: pytest.FixtureRequest):
    vfs: VFS = request.getfixturevalue(vfs_fixture)

    vfs.write_text("articles/index.md", "# Index")
    vfs.write_text("articles/guide.md", "# Guide")
    vfs.write_text("articles/sub/detail.txt", "detail")
    vfs.write_text("config.toml", "key = 'val'")

    assert vfs.exists("articles")
    assert vfs.is_dir("articles")
    assert not vfs.is_file("articles")

    # List dir
    entries = sorted(vfs.list_dir("articles"))
    assert entries == ["guide.md", "index.md", "sub"]

    # Glob
    md_files = sorted(vfs.glob("articles/*.md"))
    assert md_files == ["articles/guide.md", "articles/index.md"]

    all_files = sorted(vfs.glob("**/*.md"))
    assert all_files == ["articles/guide.md", "articles/index.md"]


@pytest.mark.parametrize("vfs_fixture", ["local_vfs", "cloud_vfs"])
def test_vfs_delete(vfs_fixture: str, request: pytest.FixtureRequest):
    vfs: VFS = request.getfixturevalue(vfs_fixture)

    vfs.write_text("temp.txt", "to be deleted")
    assert vfs.exists("temp.txt")
    vfs.delete("temp.txt")
    assert not vfs.exists("temp.txt")
    assert not vfs.is_file("temp.txt")


@pytest.mark.parametrize("vfs_fixture", ["local_vfs", "cloud_vfs"])
def test_vfs_missing_file_raises_filenotfound(vfs_fixture: str, request: pytest.FixtureRequest):
    vfs: VFS = request.getfixturevalue(vfs_fixture)

    assert not vfs.exists("nonexistent.txt")
    with pytest.raises(FileNotFoundError):
        vfs.read_text("nonexistent.txt")
    with pytest.raises(FileNotFoundError):
        vfs.read_bytes("nonexistent.txt")


@pytest.mark.parametrize("vfs_fixture", ["local_vfs", "cloud_vfs"])
@pytest.mark.parametrize(
    "malicious_path",
    [
        "../escape.txt",
        "foo/../../bar.txt",
        "/absolute/path.txt",
        "nested/../..",
        "foo\x00bar.txt",
        "$HOME/secrets.env",
        "%USERPROFILE%\\test.txt",
    ],
)
def test_vfs_rejects_path_traversal_and_escapes(
    vfs_fixture: str, malicious_path: str, request: pytest.FixtureRequest
):
    vfs: VFS = request.getfixturevalue(vfs_fixture)

    with pytest.raises((ValueError, PermissionError)):
        vfs.read_text(malicious_path)

    with pytest.raises((ValueError, PermissionError)):
        vfs.write_text(malicious_path, "pwned")

    with pytest.raises((ValueError, PermissionError)):
        vfs.exists(malicious_path)


def test_vfs_exact_parity_between_local_and_cloud(tmp_path: Path):
    local = LocalVFS(root=tmp_path)
    cloud = CloudVFS(bucket="parity-bucket", prefix="tenants/acme")

    paths = [
        "profiles/brand.toml",
        "profiles/knowledge/icp.md",
        "hooks/hooks.toml",
        "signals/2026-09-20/signal-1.json",
    ]

    payloads = {
        "profiles/brand.toml": "[brand]\nname = 'Acme'\n",
        "profiles/knowledge/icp.md": "# ICP Definition\nEnterprise B2B SaaS.\n",
        "hooks/hooks.toml": "[hooks]\nf1 = 'Question hook'\n",
        "signals/2026-09-20/signal-1.json": '{"type": "news", "id": "sig-1"}',
    }

    for path in paths:
        local.write_text(path, payloads[path])
        cloud.write_text(path, payloads[path])

    for path in paths:
        assert local.read_text(path) == cloud.read_text(path)
        assert local.exists(path) == cloud.exists(path)
        assert local.is_file(path) == cloud.is_file(path)
        assert local.is_dir(path) == cloud.is_dir(path)

    assert sorted(local.glob("profiles/**/*.md")) == sorted(cloud.glob("profiles/**/*.md"))
    assert sorted(local.list_dir("profiles")) == sorted(cloud.list_dir("profiles"))


@pytest.mark.parametrize("vfs_fixture", ["local_vfs", "cloud_vfs"])
def test_vfs_root_inspection_and_safety(vfs_fixture: str, request: pytest.FixtureRequest):
    vfs: VFS = request.getfixturevalue(vfs_fixture)

    vfs.write_text("root_file.txt", "root level file")
    vfs.write_text("sub_dir/nested.txt", "nested file")

    # 1. Root inspection works for both '.' and ''
    for root_query in [".", ""]:
        assert vfs.exists(root_query) is True
        assert vfs.is_dir(root_query) is True
        assert vfs.is_file(root_query) is False
        top_entries = sorted(vfs.list_dir(root_query))
        assert "root_file.txt" in top_entries
        assert "sub_dir" in top_entries

    # 2. File read/write/delete on root path raises ValueError (cannot overwrite/delete root as file)
    for bad_path in ["", "."]:
        with pytest.raises(ValueError):
            vfs.read_text(bad_path)
        with pytest.raises(ValueError):
            vfs.read_bytes(bad_path)
        with pytest.raises(ValueError):
            vfs.write_text(bad_path, "boom")
        with pytest.raises(ValueError):
            vfs.write_bytes(bad_path, b"boom")
        with pytest.raises(ValueError):
            vfs.delete(bad_path)
