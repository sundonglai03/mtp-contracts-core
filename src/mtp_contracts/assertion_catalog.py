"""断言目录：校验器、执行器、Agent 说明共用的**断言契约**。

定位与 `action_catalog` 完全一致 —— 一份数据服务三处，避免「说明写了、实现没有」
或反过来的漂移：

1. **说明**（MCP 工具说明、文档）：每种断言要哪些字段；
2. **执行**（`mtp-platform` 的断言引擎）：`_assert_<type>` 必须实现目录里的每一种；
3. **测试**：platform 侧比对「实现 vs 目录」，MCP 侧比对「说明 vs 目录」，
   任一侧漂移都会让测试变红。

约定：

- 只声明**已实现**的断言类型；
- `requires` 是缺了就没法比较的字段；`optional` 是可选字段（`args.*` 记法表示
  断言自己的 `args` 下的键）；
- 容器内部结构（SQL 列名、响应体形状）不声明 —— 运行时才知道。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class AssertionSpec:
    """一种断言的契约。"""

    type: str
    summary: str
    requires: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    requires_any: tuple[tuple[str, ...], ...] = ()
    example: Mapping[str, Any] = field(default_factory=dict)
    note: str = ""

    def required_text(self) -> str:
        return " + ".join(self.requires)


ASSERTIONS: dict[str, AssertionSpec] = {
    spec.type: spec
    for spec in (
        AssertionSpec(
            "equals",
            "严格相等（数字/字符串/布尔都按值比）",
            ("actual", "expected"),
            example={"id": "a1", "type": "equals", "actual": "{{ steps.q.rows[0].CfgByPass }}", "expected": "0"},
        ),
        AssertionSpec(
            "contains",
            "actual 包含 expected（字符串子串、列表元素、字典键）",
            ("actual", "expected"),
            example={
                "id": "a2",
                "type": "contains",
                "actual": "{{ steps.ssh.stdout }}",
                "expected": "CH3_STATE=NORMAL",
            },
        ),
        AssertionSpec(
            "status_code",
            "HTTP 状态码比较",
            ("actual", "expected"),
            note="actual 一般写 `{{ steps.x.http_status }}`（api 步骤的返回字段）。",
        ),
        AssertionSpec(
            "json_path",
            "按 JSONPath 取值后比较",
            ("source", "args.path"),
            optional=("expected", "args.expected"),
            note="source 指向步骤（取它的 `.json`），`args.path` 是 JSONPath 表达式。",
        ),
        AssertionSpec(
            "json_schema",
            "校验 JSON 结构是否符合 schema",
            ("source",),
            optional=("args.schema", "args.schema_file"),
            requires_any=(("args.schema", "args.schema_file"),),
            note="`args.schema` 直接给 schema；或 `args.schema_file` 指向文件。",
        ),
        AssertionSpec(
            "response_time",
            "响应耗时（毫秒）比较",
            ("actual", "expected"),
            optional=("args.mode",),
            note="`args.mode` 可选比较方式；actual 一般写 `{{ steps.x.duration_ms }}`。",
        ),
        AssertionSpec(
            "page_text_contains",
            "页面可见文本包含某段文字",
            (),
            optional=("source", "args.text"),
            # 文案可以写在 expected，也可以写在 args.text；两者都给才算合规。
            requires_any=(("expected", "args.text"),),
            note="source 指向 snapshot 步骤（如 `{{ steps.snap-home }}`）；不给则取当前页面快照。",
        ),
        AssertionSpec(
            "element_visible",
            "页面上某元素可见",
            (),
            optional=("source", "args.timeout_ms"),
            # 只要求选择器：实现（wait_for target）探的是当前页面，并不读 source。
            requires_any=(("args.target", "args.selector"),),
            note="`args.target`（或 `args.selector`）是 Playwright 选择器，断言探当前页面；"
            "source 目前只是说明性字段，实现不读它。",
        ),
        AssertionSpec(
            "file_exists",
            "文件存在（可要求最小字节数）",
            ("args.path",),
            optional=("args.min_bytes",),
        ),
        AssertionSpec(
            "exit_code",
            "命令退出码比较",
            ("actual", "expected"),
            note="actual 一般写 `{{ steps.ssh.exit_code }}`。",
        ),
        AssertionSpec(
            "db_value",
            "数据库查询结果里的某个值",
            ("actual", "expected"),
            optional=("args.row", "args.column"),
            note="actual 一般写 `{{ steps.q.rows[0].列名 }}`。",
        ),
        AssertionSpec("all", "全部子断言通过", ("items",), note="`items` 是子断言数组。"),
        AssertionSpec("any", "任一子断言通过", ("items",), note="`items` 是子断言数组。"),
    )
}


def known_types() -> list[str]:
    return sorted(ASSERTIONS)


def spec_for_type(assertion_type: str) -> AssertionSpec | None:
    return ASSERTIONS.get(assertion_type)


def describe(assertion_type: str) -> str:
    """一行：`type(必填…) — 说明[（可选: …）]`。"""
    spec = ASSERTIONS.get(assertion_type)
    if spec is None:
        return f"{assertion_type}(未收录)"
    line = (
        f"{spec.type}({spec.required_text()}) — {spec.summary}"
        if spec.requires
        else f"{spec.type} — {spec.summary}"
    )
    if spec.note:
        line += f"。{spec.note}"
    if spec.optional:
        line += f"（可选: {', '.join(spec.optional)}）"
    if spec.requires_any:
        groups = [" 或 ".join(group) for group in spec.requires_any]
        line += f"（至少一个: {'；'.join(groups)}）"
    return line


def render_text() -> str:
    """每种断言一行，供工具说明 / 文档直接引用。"""
    return "\n".join(describe(assertion_type) for assertion_type in known_types())


def render_markdown() -> str:
    lines = ["| 断言 | 必填 | 可选 | 说明 |", "| --- | --- | --- | --- |"]
    for assertion_type in known_types():
        spec = ASSERTIONS[assertion_type]
        lines.append(
            "| `{type}` | {required} | {optional} | {summary} |".format(
                type=spec.type,
                required=", ".join(f"`{name}`" for name in spec.requires) or "—",
                optional=", ".join(f"`{name}`" for name in spec.optional) or "—",
                summary=spec.summary,
            )
        )
    return "\n".join(lines)


__all__ = [
    "ASSERTIONS",
    "AssertionSpec",
    "describe",
    "known_types",
    "render_markdown",
    "render_text",
    "spec_for_type",
]
