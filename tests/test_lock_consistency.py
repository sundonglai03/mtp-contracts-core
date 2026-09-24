"""守卫：uv.lock 里本包的版本必须与 pyproject 一致。

同一类问题出过两次（提交时漏掉新文件 / 漏掉随版本变动的 lock），都是"本地跑得好好的、
发布的 tag 树却自相矛盾"。这条测试不需要网络，能在提交前就拦住。
"""

from __future__ import annotations

import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_uv_lock_matches_project_version() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "mtp-contracts-core"' in lock, "uv.lock 里找不到本包条目"
    block = lock.split('name = "mtp-contracts-core"', 1)[1][:300]
    assert f'version = "{version}"' in block, (
        f"uv.lock 里本包版本与 pyproject 不一致（应为 {version}）："
        "升版后记得一并提交 uv.lock"
    )
