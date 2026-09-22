"""MTP 平台稳定契约。

只放**稳定、可序列化、跨项目共享**的内容：

- 错误模型与错误码（`errors`）
- 脱敏规则与脱敏结果模型（`redaction`）
- 用例 schema、变量引用（`case_validator` / `variables` / `case_schema.json`）
- 结果模型与状态枚举（`results`）
- 适配器契约（`adapters`）
- 配置数据模型（`config`）

不放运行时对象、线程、future、MCP client 或文件句柄 —— 那些属于 engine / adapters。
"""

from .action_catalog import ACTIONS, ActionSpec, ArgSpec, describe, render_markdown, render_text, spec_for
from .assertion_catalog import ASSERTIONS, AssertionSpec, known_types, spec_for_type
from .adapters import ActionResult, Adapter, StepContext
from .config import McpServerConfig, PlatformConfig
from .errors import (
    AuthenticationError,
    CancelledError_,
    CaseValidationError,
    ConfigError,
    McpUnavailableError,
    MtpError,
    NetworkError,
    PolicyDeniedError,
    StateCorruptError,
    TimeoutError_,
    ToolExecutionError,
    classify_exception,
)
from .redaction import (
    DEFAULT_PLACEHOLDER,
    DEFAULT_REDACT_KEYS,
    SecretRegistry,
    collect_secret_values,
    redact,
    redact_text,
    registry_for_cases,
)
from .results import (
    CaseResult,
    RunState,
    StepResult,
    StepStatus,
    new_run_id,
    now_iso,
)

__all__ = [
    # errors
    "MtpError",
    "TimeoutError_",
    "AuthenticationError",
    "ToolExecutionError",
    "NetworkError",
    "ConfigError",
    "PolicyDeniedError",
    "McpUnavailableError",
    "CaseValidationError",
    "CancelledError_",
    "StateCorruptError",
    "classify_exception",
    # redaction
    "DEFAULT_REDACT_KEYS",
    "DEFAULT_PLACEHOLDER",
    "SecretRegistry",
    "redact",
    "redact_text",
    "collect_secret_values",
    "registry_for_cases",
    # action catalog（生成器 / 校验器 / 执行器共用的动作契约）
    "ACTIONS",
    "ActionSpec",
    "ArgSpec",
    "spec_for",
    "describe",
    "render_text",
    "render_markdown",
    # results
    "RunState",
    "StepStatus",
    "StepResult",
    "CaseResult",
    "now_iso",
    "new_run_id",
    # adapters
    "ActionResult",
    "StepContext",
    "Adapter",
    # config
    "McpServerConfig",
    "PlatformConfig",
]
