"""动作目录：生成器、校验器、执行器共用的**动作契约**。

一份数据同时服务三处，避免三套说明各自漂移：

1. **校验**（`case_validator`）：未知 action、必填参数、参数类型、结果字段路径
   —— 在创建任务之前就报错；
2. **执行**（平台直连 tools）：参数名与返回字段的事实来源，实现与目录不一致时
   由校验先暴露，而不是等运行到一半才失败；
3. **Agent 说明**：MCP 的工具说明与 skill 文档由 `render_*` 从同一份数据生成。

约定：

- 只声明**已实现**的动作，不发明动作；
- 返回字段只写**可知**的部分：`rows` / `json` / `extracted` 这类容器内部结构由
  运行时决定（例如 SQL 查出什么列），目录不假装能静态确定；
- 模板与字面量区别对待：`"{{ env.db }}"` 在 JSON 里是字符串，解析后是对象，
  因此**整串就是一个引用**的参数跳过类型检查（见 `is_template`）；
- 本模块是纯声明，不引入浏览器、数据库等执行依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

TEMPLATE_SPAN = ("{{", "}}")

# 引擎在每个步骤结果上统一附加的字段（与具体动作无关）
COMMON_RESULT_FIELDS = frozenset(
    {"step_ok", "step_status", "step_id", "error", "expected_failure"}
)


@dataclass(frozen=True)
class ArgSpec:
    """一个参数的契约。`kind` 为 JSON 类型名或 `any`（不做类型检查）。"""

    name: str
    kind: str = "any"
    required: bool = False
    aliases: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class ActionSpec:
    """一个动作的契约。"""

    action: str
    summary: str
    args: tuple[ArgSpec, ...] = ()
    returns: tuple[str, ...] = ()
    # 若干「至少给一个」的参数组，例如 ssh 的 (password, ssh_key_filepath)
    requires_any: tuple[tuple[str, ...], ...] = ()
    example: Mapping[str, Any] = field(default_factory=dict)
    note: str = ""

    def arg(self, name: str) -> ArgSpec | None:
        for spec in self.args:
            if spec.name == name or name in spec.aliases:
                return spec
        return None

    def known_fields(self) -> frozenset[str]:
        return frozenset(set(self.returns) | COMMON_RESULT_FIELDS)

    def required_names(self) -> list[str]:
        return [a.name for a in self.args if a.required]

    def signature(self) -> str:
        """`action(必填, [可选])`，用于说明文本。"""
        required = [a.name for a in self.args if a.required]
        optional = [a.name for a in self.args if not a.required]
        body = ", ".join(required)
        if optional:
            body += (", " if body else "") + "[" + ", ".join(optional) + "]"
        if self.requires_any:
            groups = [" | ".join(group) for group in self.requires_any]
            body += (", " if body else "") + "{" + ", ".join(groups) + "}"
        return f"{self.action}({body})"


@dataclass(frozen=True)
class ArgIssue:
    """参数级问题。`path` 相对该动作的 `args`，由调用方补上外部路径。"""

    path: str
    code: str
    message: str


def is_template(value: Any) -> bool:
    """整串恰好是一个 `{{ ... }}` 引用（解析后类型由上下文决定）。"""
    if not isinstance(value, str):
        return False
    text = value.strip()
    return (
        text.startswith(TEMPLATE_SPAN[0])
        and text.endswith(TEMPLATE_SPAN[1])
        and text.count(TEMPLATE_SPAN[0]) == 1
        and "{{" not in text[2:-2]
    )


_KIND_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def _type_ok(value: Any, kind: str) -> bool:
    if kind == "any":
        return True
    expected = _KIND_TYPES.get(kind)
    if expected is None:
        return True
    if isinstance(value, bool) and kind in {"integer", "number"}:
        return False  # True 不是 1
    return isinstance(value, expected)


# ---------------------------------------------------------------------------
# 目录内容（以平台 tools 的实际实现为准）
# ---------------------------------------------------------------------------
def _playwright() -> list[ActionSpec]:
    common = ("duration_ms", "page_url")
    timeout = ArgSpec("timeout", "integer", description="毫秒")
    target = ArgSpec("target", "string", required=True, aliases=("selector", "ref"), description="CSS 选择器")
    return [
        ActionSpec(
            "playwright.navigate",
            "打开 URL",
            (ArgSpec("url", "string", required=True), timeout),
            ("url", "http_status", "text") + common,
            example={"url": "{{ env.base_url }}"},
        ),
        ActionSpec("playwright.navigate_back", "后退", (timeout,), ("text",) + common),
        ActionSpec(
            "playwright.click",
            "点击元素",
            (target, timeout),
            ("target", "text") + common,
            note="返回的是 `clicked <选择器>` 摘要，**不是页面正文**；要断言页面内容请先 wait_for 再 snapshot。",
            example={"target": "button:has-text('查询')"},
        ),
        ActionSpec(
            "playwright.type",
            "填写输入框",
            (target, ArgSpec("text", "string"), ArgSpec("slowly", "boolean"), timeout),
            ("target", "text") + common,
        ),
        ActionSpec(
            "playwright.fill_form",
            "批量填表",
            (ArgSpec("fields", "object", required=True, description="选择器 -> 值"), timeout),
            ("filled", "text") + common,
        ),
        ActionSpec(
            "playwright.press_key",
            "按键",
            (ArgSpec("key", "string", required=True),),
            ("key", "text") + common,
        ),
        ActionSpec("playwright.hover", "悬停", (target, timeout), ("target", "text") + common),
        ActionSpec(
            "playwright.select_option",
            "选择下拉项",
            (target, ArgSpec("values", "any", aliases=("value",)), timeout),
            ("target", "text") + common,
        ),
        ActionSpec(
            "playwright.wait_for",
            "等待元素出现或等待指定秒数",
            (ArgSpec("target", "string", aliases=("selector",), description="支持 text=xxx 文本选择器"), ArgSpec("time", "number", description="秒"), timeout),
            ("target", "text") + common,
            requires_any=(("target", "time"),),
            note="等待**明确结果**用 target / text= 选择器；`time` 只在没有可等待信号时兜底，不要用它代替结果等待。",
            example={"target": "text=成功 1", "timeout": 15000},
        ),
        ActionSpec(
            "playwright.snapshot",
            "页面快照（文本）",
            (),
            ("page_text", "title", "text") + common,
            note="断言页面内容用 `page_text`；`text` 是带标题和 URL 的可读快照。",
        ),
        ActionSpec("playwright.screenshot", "截图（进证据）", (ArgSpec("fullPage", "boolean"),), ("bytes",) + common),
        ActionSpec(
            "playwright.evaluate",
            "执行 JS（高危）",
            (
                ArgSpec("function", "string", aliases=("expression",)),
                ArgSpec("allow_js", "boolean", required=True, description="高危动作必须显式确认"),
            ),
            ("json", "text") + common,
            requires_any=(("function",),),
            example={"function": "() => document.title", "allow_js": True},
        ),
        ActionSpec("playwright.console_messages", "控制台日志", (ArgSpec("level", "string"),), ("messages", "text") + common),
        ActionSpec("playwright.network_requests", "网络请求", (), ("requests", "text") + common),
        ActionSpec(
            "playwright.resize",
            "调整视口",
            (ArgSpec("width", "integer"), ArgSpec("height", "integer")),
            ("width", "height", "text") + common,
        ),
        ActionSpec(
            "playwright.close",
            "关闭浏览器会话",
            (),
            ("text",) + common,
            note="用例之间互不污染：浏览器用例在 postconditions 里显式 close。",
        ),
    ]


_CREDENTIALS = ArgSpec(
    "credentials",
    "object",
    required=True,
    description="连接参数 {host,port,user,password,database}，可直接写或引用 {{ env.db }}；tools 不保存连接配置",
)


def _mysql() -> list[ActionSpec]:
    common = ("duration_ms", "host", "database")
    table = ArgSpec("table_name", "string", required=True)
    where = ArgSpec("where", "string", description="SQL 片段，值走 where_params 绑定")
    params = ArgSpec("where_params", "array")
    return [
        ActionSpec("mysql.health_check", "连通性探活", (_CREDENTIALS,), ("ok",) + common),
        ActionSpec("mysql.databases", "列出数据库", (_CREDENTIALS,), ("databases",) + common),
        ActionSpec("mysql.tables", "列出表", (_CREDENTIALS,), ("tables",) + common),
        ActionSpec("mysql.describe", "查看表结构", (_CREDENTIALS, table), ("columns",) + common),
        ActionSpec(
            "mysql.query",
            "执行只读 SELECT",
            (_CREDENTIALS, ArgSpec("sql", "string", required=True), params, ArgSpec("read_only", "boolean")),
            ("rows", "row_count") + common,
            example={"credentials": "{{ env.db }}", "sql": "SELECT 1 AS n"},
            note="写语句必须走 insert / update / delete（那三个才有写同意与影响行数闸门）。",
        ),
        ActionSpec(
            "mysql.fetch",
            "按条件取行",
            (_CREDENTIALS, table, where, params, ArgSpec("order_by", "string"), ArgSpec("limit", "integer")),
            ("rows", "row_count") + common,
        ),
        ActionSpec("mysql.count", "按条件计数", (_CREDENTIALS, table, where, params), ("count",) + common),
        ActionSpec(
            "mysql.insert",
            "插入一行",
            (_CREDENTIALS, table, ArgSpec("row", "object", required=True)),
            ("rows_affected",) + common,
        ),
        ActionSpec(
            "mysql.update",
            "按条件更新",
            (_CREDENTIALS, table, ArgSpec("updates", "object", required=True), ArgSpec("where", "string", required=True), params),
            ("rows_affected",) + common,
        ),
        ActionSpec(
            "mysql.delete",
            "按条件删除",
            (_CREDENTIALS, table, ArgSpec("where", "string", required=True), params),
            ("rows_affected",) + common,
        ),
    ]


def _ssh() -> list[ActionSpec]:
    common = ("duration_ms", "host")
    host = ArgSpec("host", "string", required=True)
    user = ArgSpec("user", "string", required=True)
    password = ArgSpec("password", "string", description="可直接写或引用 {{ secrets.x }}")
    key = ArgSpec("ssh_key_filepath", "string")
    port = ArgSpec("port", "integer")
    allow_any = ArgSpec("allow_any_host", "boolean", description="需配置层同时放行")
    return [
        ActionSpec(
            "ssh.execute",
            "在远端执行命令",
            (
                host,
                user,
                password,
                key,
                port,
                allow_any,
                ArgSpec("command", "string", required=True),
                ArgSpec("timeout", "number", description="秒"),
                ArgSpec("ok_exit_codes", "array", description="默认 [0]"),
                ArgSpec("connect_timeout", "number"),
            ),
            ("command", "exit_code", "stdout", "stderr", "succeeded") + common,
            requires_any=(("password", "ssh_key_filepath"),),
            example={"host": "{{ vars.proxy_host }}", "user": "root", "password": "{{ secrets.ssh_password }}", "command": "hostname"},
            note="断言请针对 stdout / exit_code 等具体字段；整个结果对象含原始 command，用它做包含判断会把命令里的期望文字误判成实际输出。",
        ),
        ActionSpec(
            "ssh.upload",
            "上传文件/目录（SFTP）",
            (host, user, password, key, port, allow_any, ArgSpec("local_path", "string"), ArgSpec("local_dir", "string"), ArgSpec("remote_path", "string", required=True)),
            ("local_path", "remote_path") + common,
            requires_any=(("password", "ssh_key_filepath"),),
        ),
        ActionSpec(
            "ssh.download",
            "下载文件（SFTP）",
            (host, user, password, key, port, allow_any, ArgSpec("remote_file", "string"), ArgSpec("remote_path", "string"), ArgSpec("local_path", "string", required=True)),
            ("remote_file", "local_path") + common,
            requires_any=(("password", "ssh_key_filepath"), ("remote_file", "remote_path")),
        ),
    ]


def _api() -> list[ActionSpec]:
    common = ("http_status", "status_code", "ok", "duration_ms", "url", "method", "headers", "json", "text", "extracted", "error")
    shared = (
        ArgSpec("url", "string"),
        ArgSpec("path", "string", description="配合 environment.base_url"),
        ArgSpec("base_url", "string"),
        ArgSpec("headers", "object"),
        ArgSpec("cookies", "object"),
        ArgSpec("query", "object", aliases=("params",)),
        ArgSpec("json", "any"),
        ArgSpec("form", "any", aliases=("data",)),
        ArgSpec("files", "array"),
        ArgSpec("timeout_sec", "number"),
        ArgSpec("extract", "object", description="名称 -> JSONPath，结果进 extracted"),
        ArgSpec("allow_redirects", "boolean"),
        ArgSpec("allow_error", "boolean", description="4xx/5xx 不算步骤失败"),
        ArgSpec("allow_any_host", "boolean"),
        ArgSpec("use_proxy", "boolean"),
    )
    specs = [
        ActionSpec(f"api.{name}", f"HTTP {name.upper()}", shared, common, requires_any=(("url", "path"),))
        for name in ("get", "post", "put", "patch", "delete", "head", "options", "request")
    ]
    specs.append(
        ActionSpec(
            "api.download",
            "HTTP 下载到文件",
            shared + (ArgSpec("output", "string", description="必须落在 api_download_root 内"),),
            # 下载落的是文件，没有 json/text/extracted —— 不能沿用 api 通用的返回集，
            # 否则用例可以引用到**实现根本不会返回**的字段（校验通过、执行时引用失败）。
            ("output", "bytes", "http_status", "status_code", "ok", "duration_ms", "url", "method"),
            requires_any=(("url", "path"),),
        )
    )
    return specs


def _catalog() -> dict[str, ActionSpec]:
    specs: Iterable[ActionSpec] = (*_playwright(), *_mysql(), *_ssh(), *_api())
    return {spec.action: spec for spec in specs}


ACTIONS: dict[str, ActionSpec] = _catalog()


def spec_for(action: str) -> ActionSpec | None:
    return ACTIONS.get(str(action or ""))


def known_actions() -> list[str]:
    return sorted(ACTIONS)


def suggestions(action: str) -> list[str]:
    """给未知道动作猜几个相近的（同适配器下的动作优先）。"""
    adapter, _, name = str(action or "").partition(".")
    same = [a for a in known_actions() if a.split(".", 1)[0] == adapter]
    if same:
        return same
    return known_actions()[:4]


def validate_args(action: str, args: Mapping[str, Any] | None) -> list[ArgIssue]:
    """按目录检查参数：必填、类型、「至少给一个」参数组。

    整串 `{{ ... }}` 的模板跳过类型检查——解析后是什么类型由上下文决定。
    """
    spec = spec_for(action)
    if spec is None:
        return []
    given = dict(args or {})
    issues: list[ArgIssue] = []

    for arg in spec.args:
        present = [key for key in (arg.name, *arg.aliases) if key in given]
        if not present:
            if arg.required:
                issues.append(
                    ArgIssue(path=arg.name, code="missing_arg", message=f"{action} 缺少必填参数 {arg.name}")
                )
            continue
        value = given[present[0]]
        if value is None:
            # 可选参数显式写 null = 不提供；但**必填参数写 null 是不合法的** ——
            # 这里以前直接 continue，于是 navigate.url=null 之类能过校验、到执行才炸。
            if arg.required:
                issues.append(
                    ArgIssue(
                        path=present[0],
                        code="invalid_arg_type",
                        message=f"{action} 参数 {present[0]} 必填，不能为 null",
                    )
                )
            continue
        if is_template(value):
            continue
        if not _type_ok(value, arg.kind):
            issues.append(
                ArgIssue(
                    path=present[0],
                    code="invalid_arg_type",
                    message=(
                        f"{action} 参数 {present[0]} 应为 {arg.kind}，"
                        f"实际 {type(value).__name__}"
                    ),
                )
            )

    for group in spec.requires_any:
        # 「至少给一个」要把别名一起算上：wait_for 只写 selector、evaluate 只写
        # expression 都是合法的（工具侧会归一化），不能因为主名缺席就报缺参数。
        names: list[str] = []
        for name in group:
            arg_spec = spec.arg(name)
            names.extend((arg_spec.name, *arg_spec.aliases) if arg_spec else (name,))
        if not any(name in given and given[name] not in (None, "") for name in names):
            issues.append(
                ArgIssue(
                    path=group[0],
                    code="missing_arg",
                    message=f"{action} 需要 {' 或 '.join(group)} 之一",
                )
            )

    # 未声明的参数不报错：执行器可能接受额外透传字段（如 api 的 use_proxy 已声明，
    # 但步骤级扩展字段不该被目录拦死）。这里只保证「声明的参数类型正确」。
    return issues


def unknown_action_issue(action: str) -> ArgIssue:
    near = suggestions(action)
    return ArgIssue(
        path="action",
        code="unknown_action",
        message=f"未知动作 {action!r}；可用: {', '.join(near)}",
    )


# ---------------------------------------------------------------------------
# 说明文本（Agent 说明与文档直接引用，避免手写一套易漂移的说明）
# ---------------------------------------------------------------------------
def describe(action: str) -> str:
    spec = spec_for(action)
    if spec is None:
        return f"{action}: (未收录)"
    required = spec.required_names()
    detail = f"必填: {', '.join(required) or '无'}；返回: {', '.join(spec.returns) or '无'}"
    return f"{spec.signature()} — {spec.summary}。{detail}"


def render_text() -> str:
    """每个动作一行：`action(必填[, 可选]) — 说明；返回: ...`"""
    return "\n".join(describe(action) for action in known_actions())


def render_markdown() -> str:
    lines = ["| 动作 | 必填参数 | 参数（可选/别名） | 返回字段 | 说明 |", "| --- | --- | --- | --- | --- |"]
    for action in known_actions():
        spec = ACTIONS[action]
        required = ", ".join(f"`{a.name}`" for a in spec.args if a.required) or "—"
        optional = ", ".join(
            f"`{a.name}`" + (f"(`{'`/`'.join(a.aliases)}`)" if a.aliases else "")
            for a in spec.args
            if not a.required
        ) or "—"
        returns = ", ".join(f"`{name}`" for name in spec.returns) or "—"
        lines.append(f"| `{action}` | {required} | {optional} | {returns} | {spec.summary} |")
    return "\n".join(lines)


__all__ = [
    "ACTIONS",
    "COMMON_RESULT_FIELDS",
    "ActionSpec",
    "ArgIssue",
    "ArgSpec",
    "describe",
    "is_template",
    "known_actions",
    "render_markdown",
    "render_text",
    "spec_for",
    "suggestions",
    "unknown_action_issue",
    "validate_args",
]
