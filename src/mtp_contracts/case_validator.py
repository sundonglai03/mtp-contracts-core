"""用例校验器。

两道校验，都在**执行之前**完成（非法用例不能进入执行器）：

1. **结构校验**：JSON Schema（`case_schema.json`），错误信息带字段路径，
   形如 `steps[2].action: 'navigate' does not match '^[a-z][a-z0-9_]*\\.[a-z][a-z0-9_]*$'`。
2. **语义校验**：
   - 步骤/断言 id 在同一用例内唯一；
   - `{{ ... }}` 引用必须在已声明的命名空间内（未定义的步骤/变量直接报错，
     带路径），避免运行时才发现拼错；
   - 套件内变量引用必须可解析。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
import re
from pathlib import Path
from typing import Any, Iterable

from .action_catalog import spec_for, unknown_action_issue, validate_args
from .assertion_catalog import spec_for_type
from .errors import CaseValidationError, ConfigError
from .variables import (
    NAMESPACES,
    PATH_RE,
    STATIC_KEYS,
    iter_references,
    tokenize,
)

SCHEMA_PATH = Path(__file__).resolve().parent / "case_schema.json"

SUPPORTED_SCHEMA_VERSIONS = (1,)

PHASES = ("preconditions", "steps", "postconditions")


@dataclass
class ValidationIssue:
    path: str
    message: str
    kind: str = "schema"
    # 机器可读的问题码（unknown_action / missing_arg / invalid_arg_type …）。
    # 留空时沿用 kind，保证既有调用方拿到的 code 不变。
    code: str = ""

    @property
    def error_code(self) -> str:
        return self.code or self.kind

    def render(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


@dataclass
class ValidationResult:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    # 不阻断的运行期忠告（kind="lint"）：契约合法，但很可能在平台上跑不稳 / 跑不过。
    # 例如用例没有自己打开页面（平台每个用例都会重建浏览器会话）、固定睡眠、
    # 没有断言或证据、断言取的是整个步骤对象。
    warnings: list[ValidationIssue] = field(default_factory=list)

    def messages(self) -> list[str]:
        return [i.render() for i in self.issues]

    def warning_messages(self) -> list[str]:
        return [i.render() for i in self.warnings]


_schema_cache: dict[str, Any] = {}


def load_schema() -> dict[str, Any]:
    if "schema" not in _schema_cache:
        with SCHEMA_PATH.open("r", encoding="utf-8") as fh:
            _schema_cache["schema"] = json.load(fh)
    return _schema_cache["schema"]


def _get_validator():
    """缓存 Draft202012Validator：构造一次约 2s，逐用例重建会把测试拖成分钟级。"""
    if "validator" not in _schema_cache:
        import jsonschema

        _schema_cache["validator"] = jsonschema.Draft202012Validator(load_schema())
    return _schema_cache["validator"]


def _strip_internal(case: dict[str, Any]) -> dict[str, Any]:
    """去掉平台自己挂上的 `_` 前缀键（如 `_source`），它们不属于用例 schema。"""
    return {k: v for k, v in case.items() if not str(k).startswith("_")}


def load_case(path: str | Path) -> dict[str, Any]:
    """读取 JSON 用例文件。"""
    target = Path(path)
    if not target.exists():
        raise ConfigError(f"用例文件不存在: {target}")
    if target.suffix.lower() != ".json":
        raise ConfigError(f"仅支持 JSON 用例文件: {target}")

    try:
        with target.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise CaseValidationError(f"JSON 用例解析失败: {target}", detail=str(exc)) from exc

    if not isinstance(data, dict):
        raise CaseValidationError(
            f"用例根节点必须是映射: {target}",
            detail=f"实际类型: {type(data).__name__}",
        )
    data.setdefault("_source", str(target))
    return data


def validate_case(
    case: dict[str, Any],
    *,
    source: str = "",
    extra_actions: Iterable[str] = (),
) -> ValidationResult:
    """只校验不抛错，返回所有问题。

    `extra_actions`：调用方自己注册的动作（引擎注入的测试替身、二次开发的自定义工具）。
    它们不在共用目录里，只跳过「动作是否存在 / 参数是否合规」两项，其余规则照常。
    """
    issues: list[ValidationIssue] = []

    validator = _get_validator()
    clean = _strip_internal(case)
    for error in sorted(validator.iter_errors(clean), key=lambda e: list(e.absolute_path)):
        base = _render_path(error.absolute_path)
        # `required` 报在父对象上，路径要补到具体缺失的字段，否则用户只看到 "(root)"
        if error.validator == "required":
            missing = [
                str(name)
                for name in (error.validator_value or [])
                if isinstance(error.instance, dict) and name not in error.instance
            ]
            for name in missing or ["?"]:
                path = f"{base}.{name}" if base != "(root)" else name
                issues.append(
                    ValidationIssue(path=path, message="缺少必填字段", kind="schema")
                )
            continue
        issues.append(ValidationIssue(path=base, message=error.message, kind="schema"))

    # 结构不过关时，语义校验的输入不可信，直接返回
    if issues:
        return ValidationResult(ok=False, issues=issues)

    clean = _strip_internal(case)
    issues.extend(_check_schema_version(clean))
    issues.extend(_check_unique_ids(clean))
    issues.extend(_check_actions(clean, frozenset(extra_actions)))
    issues.extend(_check_assertions(clean))
    issues.extend(_check_variable_references(clean))

    return ValidationResult(ok=not issues, issues=issues, warnings=_lint_case(clean))


def require_valid(
    case: dict[str, Any], *, source: str = "", extra_actions: Iterable[str] = ()
) -> dict[str, Any]:
    """校验失败即抛 `CaseValidationError`（消息里带全部字段路径）。"""
    result = validate_case(case, source=source, extra_actions=extra_actions)
    if not result.ok:
        raise CaseValidationError(
            f"用例不合法（{len(result.issues)} 个问题）",
            detail="\n".join(f"  - {m}" for m in result.messages()),
            extra={"issues": [i.render() for i in result.issues]},
        )
    return case


# ---------------------------------------------------------------------------
# 语义校验
# ---------------------------------------------------------------------------
def _render_path(parts: Any) -> str:
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out or "(root)"


def iter_steps(case: dict[str, Any]):
    """按执行顺序产出 (phase, index, step)。fixtures 先于前置条件执行。"""
    for index, step in enumerate(case.get("fixtures") or []):
        yield "fixtures", index, step
    for phase in PHASES:
        for index, step in enumerate(case.get(phase) or []):
            yield phase, index, step


def iter_assertions(case: dict[str, Any]):
    for index, assertion in enumerate(case.get("assertions") or []):
        yield index, assertion


def all_step_ids(case: dict[str, Any]) -> set[str]:
    return {str(step.get("id")) for _, _, step in iter_steps(case) if step.get("id")}


def _check_schema_version(case: dict[str, Any]) -> list[ValidationIssue]:
    version = case.get("schema_version")
    if version in SUPPORTED_SCHEMA_VERSIONS:
        return []
    return [
        ValidationIssue(
            path="schema_version",
            message=(
                f"不支持的 schema_version: {version!r}，"
                f"当前支持 {list(SUPPORTED_SCHEMA_VERSIONS)}"
            ),
            kind="semantics",
        )
    ]


def _check_unique_ids(case: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    seen: dict[str, str] = {}
    for phase, index, step in iter_steps(case):
        step_id = step.get("id")
        if not step_id:
            continue
        if step_id in seen:
            issues.append(
                ValidationIssue(
                    path=f"{phase}[{index}].id",
                    message=f"步骤 id 重复: {step_id!r}（已在 {seen[step_id]} 使用）",
                    kind="semantics",
                )
            )
        else:
            seen[step_id] = f"{phase}[{index}]"

    # 断言 id：顶层必填且唯一；all/any 子项自动编号
    assertion_ids: dict[str, str] = {}
    for index, assertion in enumerate(case.get("assertions") or []):
        aid = assertion.get("id")
        if not aid:
            issues.append(
                ValidationIssue(
                    path=f"assertions[{index}].id",
                    message="顶层断言必须提供 id",
                    kind="semantics",
                )
            )
            continue
        if aid in assertion_ids:
            issues.append(
                ValidationIssue(
                    path=f"assertions[{index}].id",
                    message=f"断言 id 重复: {aid!r}（已在 {assertion_ids[aid]} 使用）",
                    kind="semantics",
                )
            )
        else:
            assertion_ids[aid] = f"assertions[{index}]"

    return issues


def _check_actions(
    case: dict[str, Any], extra_actions: frozenset[str] = frozenset()
) -> list[ValidationIssue]:
    """按动作目录检查每个步骤（含 fixture 的 cleanup）：动作是否存在、参数是否合规。

    `extra_actions` 是**调用方自己注册的动作**（引擎注入的测试替身 / 二次开发的自定义
    工具）：它们不在共用目录里，只跳过动作与参数检查，不影响其余规则。
    """
    issues: list[ValidationIssue] = []
    for phase, index, step in iter_steps(case):
        if not isinstance(step, dict):
            continue
        _check_action_entry(step, f"{phase}[{index}]", issues, extra_actions)
        cleanup = step.get("cleanup")
        if isinstance(cleanup, dict):
            _check_action_entry(cleanup, f"{phase}[{index}].cleanup", issues, extra_actions)
    return issues


def _check_action_entry(
    entry: dict[str, Any],
    base: str,
    issues: list[ValidationIssue],
    extra_actions: frozenset[str] = frozenset(),
) -> None:
    action = str(entry.get("action") or "")
    if not action:
        return  # 缺 action 已由结构校验报出
    if action in extra_actions:
        return
    if spec_for(action) is None:
        unknown = unknown_action_issue(action)
        issues.append(
            ValidationIssue(
                path=f"{base}.action",
                message=unknown.message,
                kind="semantics",
                code=unknown.code,
            )
        )
        return
    for arg_issue in validate_args(action, entry.get("args")):
        issues.append(
            ValidationIssue(
                path=f"{base}.args.{arg_issue.path}",
                message=arg_issue.message,
                kind="semantics",
                code=arg_issue.code,
            )
        )


def _assertion_field(assertion: dict[str, Any], dotted: str) -> tuple[bool, Any]:
    """读取断言字段并区分「没有这个键」与「值就是 null」。"""
    node: Any = assertion
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _check_assertions(case: dict[str, Any]) -> list[ValidationIssue]:
    """按断言目录递归检查必填字段，避免空断言在运行时被误判为成功。"""
    issues: list[ValidationIssue] = []

    def check(assertion: dict[str, Any], base: str) -> None:
        assertion_type = str(assertion.get("type") or "")
        spec = spec_for_type(assertion_type)
        if spec is None:
            return  # 未知类型已由 JSON Schema 报出

        for field_name in spec.requires:
            present, _ = _assertion_field(assertion, field_name)
            if not present:
                issues.append(
                    ValidationIssue(
                        path=f"{base}.{field_name}",
                        message=f"{assertion_type} 断言缺少必填字段 {field_name}",
                        kind="semantics",
                        code="missing_assertion_field",
                    )
                )

        for group in spec.requires_any:
            values = [_assertion_field(assertion, field_name) for field_name in group]
            if not any(present and value not in (None, "") for present, value in values):
                issues.append(
                    ValidationIssue(
                        path=f"{base}.{group[0]}",
                        message=f"{assertion_type} 断言需要 {' 或 '.join(group)} 之一",
                        kind="semantics",
                        code="missing_assertion_field",
                    )
                )

        if assertion_type in {"all", "any"}:
            for index, child in enumerate(assertion.get("items") or []):
                if isinstance(child, dict):
                    check(child, f"{base}.items[{index}]")

    for index, assertion in iter_assertions(case):
        if isinstance(assertion, dict):
            check(assertion, f"assertions[{index}]")
    return issues


# ---------------------------------------------------------------------------
# 运行期忠告（lint）：契约合法 ≠ 在平台上跑得过
# ---------------------------------------------------------------------------
# 页面级动作：都要求「已经有一个打开的页面」。navigate / navigate_back 负责把页面打开，
# 所以不算在内。
_PAGE_ACTIONS = frozenset(
    {
        "click",
        "type",
        "fill_form",
        "press_key",
        "hover",
        "select_option",
        "wait_for",
        "snapshot",
        "screenshot",
        "evaluate",
        "console_messages",
        "network_requests",
        "resize",
    }
)
_OPEN_ACTIONS = frozenset({"navigate", "navigate_back"})
# 需要「具体值」来比较的断言：actual 写成 {{ steps.x }}（整个步骤对象）几乎必然是错的。
_VALUE_ASSERTION_TYPES = frozenset(
    {
        "equals",
        "contains",
        "status_code",
        "json_path",
        "json_schema",
        "response_time",
        "file_exists",
        "exit_code",
        "db_value",
    }
)
# 「整对象引用」= {{ steps.<id> }}。步骤 id 里允许出现点与连字符（见 schema 的 id 规则），
# 所以不能靠正则区分 id 与字段 —— 一律用用例里真实声明的步骤 id 判断：
# {{ steps.x.y }} 只有在 x.y 恰好是一个步骤 id 时才算整对象引用。
_WHOLE_STEP_RE = re.compile(r"^\{\{\s*steps\.(.+?)\s*\}\}$")


# 「元素类型 + 文本」混写选择器：把「长什么样」和「写什么字」写死在一起。
# 真实事故：登录页的登录按钮是 <input type=button value="登录">（老式 JSP），
# 用例写 button:has-text('登录') → 页面上一个 <button> 都没有，白等 15s 超时；
# 另一处门禁弹窗是自定义 <div class=loginUKeyClass>（不是 Element 对话框），
# 用例写 .el-dialog__wrapper:has-text('UKey驱动未安装') button:has-text('跳过') 同样全空。
# Playwright 的文本选择器 text=文字 同时匹配这几种元素，是更稳的默认写法。
_TYPED_TEXT_SELECTOR = re.compile(
    r"\b(?:button|a|input)\s*:\s*has-text\s*\(\s*(['\"])(.*?)\1\s*\)"
)
_TARGET_TEXT_ACTIONS = {"click", "hover", "type", "select_option"}


def _lint_case(case: dict[str, Any]) -> list[ValidationIssue]:
    """运行期忠告：只报有明确证据、几乎不会误报的问题，宁缺毋滥。

    这些**不阻断**套件生成，但决定了用例在平台上「能不能一次跑过」——写用例的
    agent 拿到这些忠告就能自查，不用去读平台文档。
    """
    warnings: list[ValidationIssue] = []
    entries: list[tuple[str, dict[str, Any]]] = [
        (f"{phase}[{index}]", step) for phase, index, step in iter_steps(case)
    ]
    for _, step in list(entries):
        cleanup = step.get("cleanup")
        if isinstance(cleanup, dict):
            entries.append(("fixture.cleanup", cleanup))

    playwright_actions = {
        str(step.get("action") or "").split(".", 1)[-1]
        for _, step in entries
        if str(step.get("action") or "").startswith("playwright.")
    }
    if (
        playwright_actions & _PAGE_ACTIONS
        and not (playwright_actions & _OPEN_ACTIONS)
        and not bool(case.get("reuse_session"))
    ):
        warnings.append(
            ValidationIssue(
                path="steps",
                message=(
                    "本用例有页面级 playwright 动作，却没有 playwright.navigate："
                    "平台会在每个用例开始前重建浏览器会话，用例不能依赖上一个用例打开的页面。"
                    "请先 navigate 打开页面（并处理登录 / 门禁）；确需复用上一用例的会话时写 reuse_session: true"
                ),
                kind="lint",
                code="no_navigate",
            )
        )

    for path, step in entries:
        if str(step.get("action") or "") != "playwright.wait_for":
            continue
        if (step.get("args") or {}).get("time") is not None:
            warnings.append(
                ValidationIssue(
                    path=f"{path}.args.time",
                    message=(
                        "wait_for 用 time 做固定睡眠会让用例又慢又不稳，"
                        "建议改成等一个明确信号（wait_for 的 target，或断言用的可见元素）"
                    ),
                    kind="lint",
                    code="fixed_sleep",
                )
            )

    if not list(case.get("assertions") or []):
        warnings.append(
            ValidationIssue(
                path="assertions",
                message="没有断言：用例只证明步骤跑完了，没有验证结果",
                kind="lint",
                code="no_assertions",
            )
        )

    # 只有浏览器步骤才有截图可采（平台只对 playwright.* 落证据），
    # 纯 ssh / mysql 用例不该被这条提醒打扰。
    has_browser_steps = any(
        str(step.get("action") or "").startswith("playwright.") for _, step in entries
    )
    if has_browser_steps and not any(list(step.get("evidence") or []) for _, step in entries):
        warnings.append(
            ValidationIssue(
                path="steps",
                message=(
                    "没有任何步骤声明 evidence：失败时没有截图可查，排查只能靠猜"
                    "（浏览器步骤可写 evidence: [\"screenshot\"]）"
                ),
                kind="lint",
                code="no_evidence",
            )
        )

    for path, step in entries:
        action = str(step.get("action") or "")
        if not action.startswith("playwright.") or action.split(".", 1)[-1] not in _TARGET_TEXT_ACTIONS:
            continue
        args = step.get("args") or {}
        target = str(args.get("target") or args.get("selector") or "")
        match = _TYPED_TEXT_SELECTOR.search(target)
        if not match:
            continue
        text = match.group(2).strip()
        warnings.append(
            ValidationIssue(
                path=f"{path}.args.target",
                message=(
                    f"「元素类型 + 文本」混写很脆：「{text}」可能长在 <button> 上，"
                    f"也可能长在 <span> 或 <input type=button value=\"{text}\">（老式 JSP 登录页）"
                    "或自定义组件（Element Plus 弹窗等）上，写死类型就会一个都匹配不到、"
                    f"一路等到超时。建议改用文本选择器 text={text}（它同时匹配这几种元素）；"
                    "平台在超时错误详情里会列出页面上的真实候选元素"
                ),
                kind="lint",
                code="fragile_target",
            )
        )

    step_ids = all_step_ids(case)
    for index, assertion in enumerate(case.get("assertions") or []):
        if not isinstance(assertion, dict):
            continue
        for sub_path, sub in _walk_assertions(assertion, f"assertions[{index}]"):
            if str(sub.get("type") or "") not in _VALUE_ASSERTION_TYPES:
                continue
            actual = sub.get("actual")
            if not isinstance(actual, str):
                continue
            match = _WHOLE_STEP_RE.match(actual.strip())
            if match and match.group(1) in step_ids:
                warnings.append(
                    ValidationIssue(
                        path=f"{sub_path}.actual",
                        message=(
                            f"{actual.strip()} 取的是整个步骤对象，不是可比的值；"
                            "应按断言取具体字段，例如 .stdout / .json / .page_text / .rows / .http_status"
                        ),
                        kind="lint",
                        code="whole_step_actual",
                    )
                )
    return warnings


def _walk_assertions(assertion: dict[str, Any], path: str):
    """断言自身 + all/any 的 items 递归展开。"""
    yield path, assertion
    for index, item in enumerate(assertion.get("items") or []):
        if isinstance(item, dict):
            yield from _walk_assertions(item, f"{path}.items[{index}]")


def _check_variable_references(case: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    step_ids = all_step_ids(case)
    step_actions = {
        str(step.get("id")): str(step.get("action") or "")
        for _, _, step in iter_steps(case)
        if isinstance(step, dict) and step.get("id")
    }
    variables = set((case.get("variables") or {}))
    secrets = set((case.get("secrets") or {}))
    env_keys = set((case.get("environment") or {}))

    def walk(node: Any, path: str, *, in_secrets_block: bool = False) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                walk(value, child, in_secrets_block=(child == "secrets"))
            return
        if isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]", in_secrets_block=in_secrets_block)
            return
        if not isinstance(node, str):
            return
        # secrets 块的值是**实际凭证**（`{逻辑名: 真实值}`），不是环境变量名，
        # 也不在这里解析引用：写了模板就原样交给工具，由使用者自己决定含义，
        # 所以整块跳过引用检查。
        if in_secrets_block:
            return

        for expr in iter_references(node):
            if not PATH_RE.match(expr):
                issues.append(
                    ValidationIssue(
                        path=path,
                        message=f"变量引用格式不合法: {{{{ {expr} }}}}（应为 {{ 命名空间.路径 }}）",
                        kind="semantics",
                    )
                )
                continue

            root, _, rest = expr.partition(".")
            if root not in NAMESPACES:
                issues.append(
                    ValidationIssue(
                        path=path,
                        message=(
                            f"未知命名空间 {{{{ {expr} }}}}，"
                            f"可用: {', '.join(NAMESPACES)}"
                        ),
                        kind="semantics",
                    )
                )
                continue

            if root in STATIC_KEYS:
                if rest not in STATIC_KEYS[root]:
                    issues.append(
                        ValidationIssue(
                            path=path,
                            message=(
                                f"{{{{ {expr} }}}} 不合法，"
                                f"{root} 可用键: {', '.join(sorted(STATIC_KEYS[root]))}"
                            ),
                            kind="semantics",
                        )
                    )
            elif root == "steps":
                # 用 tokenize 而不是 split(".")：`steps.q.rows[0].col` 的第一层字段是
                # `rows`，按点切会切出 `rows[0]` 这种假字段名。
                tokens = tokenize(rest)
                step_id = tokens[0] if tokens and isinstance(tokens[0], str) else ""
                if step_id and step_id not in step_ids:
                    issues.append(
                        ValidationIssue(
                            path=path,
                            message=(
                                f"{{{{ {expr} }}}} 引用了不存在的步骤 {step_id!r}，"
                                f"已声明: {', '.join(sorted(step_ids)) or '(无)'}"
                            ),
                            kind="semantics",
                        )
                    )
                elif len(tokens) > 1 and isinstance(tokens[1], str) and tokens[1]:
                    # 只校验第一段字段名是否可能返回；更深的路径（SQL 列名、响应体结构）
                    # 运行时才知道，目录不假装能静态确定。
                    field = tokens[1]
                    spec = spec_for(step_actions.get(step_id, ""))
                    if spec is not None and field not in spec.known_fields():
                        issues.append(
                            ValidationIssue(
                                path=path,
                                message=(
                                    f"{{{{ {expr} }}}} 引用了 {step_id}（{spec.action}）"
                                    f"不会返回的字段 {field!r}，可用: "
                                    f"{', '.join(sorted(spec.known_fields()))}"
                                ),
                                kind="semantics",
                                code="unknown_result_field",
                            )
                        )
            elif root == "vars":
                # rest 可能以数组下标紧跟首段（users[0].name）；给 tokenizer
                # 补一个虚拟根，避免把 "users[0]" 整体误当成变量名。
                tokens = tokenize(f"vars.{rest}")[1:]
                name = tokens[0] if tokens and isinstance(tokens[0], str) else ""
                if name and name not in variables and name not in secrets:
                    issues.append(
                        ValidationIssue(
                            path=path,
                            message=(
                                f"{{{{ {expr} }}}} 引用了未定义变量 {name!r}，"
                                f"已声明: {', '.join(sorted(variables | secrets)) or '(无)'}"
                            ),
                            kind="semantics",
                        )
                    )
            elif root == "secrets":
                name = rest.split(".")[0]
                if name and name not in secrets:
                    issues.append(
                        ValidationIssue(
                            path=path,
                            message=(
                                f"{{{{ {expr} }}}} 引用了未声明的 secret {name!r}，"
                                f"已声明: {', '.join(sorted(secrets)) or '(无)'}"
                            ),
                            kind="semantics",
                        )
                    )
            elif root == "env":
                tokens = tokenize(f"env.{rest}")[1:]
                name = tokens[0] if tokens and isinstance(tokens[0], str) else ""
                if name and name not in env_keys:
                    issues.append(
                        ValidationIssue(
                            path=path,
                            message=(
                                f"{{{{ {expr} }}}} 引用了未定义的 environment 键 {name!r}，"
                                f"已声明: {', '.join(sorted(env_keys)) or '(无)'}"
                            ),
                            kind="semantics",
                        )
                    )

    walk(case, "")
    return issues


def validate_file(path: str | Path) -> ValidationResult:
    case = load_case(path)
    return validate_case(case, source=str(path))
