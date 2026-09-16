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
    redact,
    redact_text,
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
