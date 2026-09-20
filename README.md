# mtp-contracts-core

MTP 测试用例契约的唯一事实来源，提供：

- JSON 用例 Schema 与语义校验；
- 变量解析和凭据脱敏；
- 错误、执行结果、适配器及平台配置数据模型。

本项目不提供 MCP 服务、不执行测试，也不连接任何目标系统。`mtp-contracts-mcp`
和 `mtp-platform` 都依赖本包，不再保存契约源码副本。

用例文件只支持 UTF-8 编码的 `.json`；YAML 文件不再加载。

## 本地开发

```bash
uv sync --frozen --extra dev
uv run --frozen pytest -q
```

消费项目通过 `tool.uv.sources` 依赖固定版本标签，不跟踪 `main`：

```toml
dependencies = ["mtp-contracts-core==0.1.0"]

[tool.uv.sources]
mtp-contracts-core = { git = "https://github.com/sundonglai03/mtp-contracts-core.git", tag = "v0.1.0" }
```

需要同时开发尚未发布的契约改动时，可以临时把 source 改为相邻目录的可编辑路径；
发布新标签后应立即切回固定 Git tag 并更新 `uv.lock`。

契约有不兼容变更时必须提升主版本或 `schema_version`，并在消费项目升级依赖前分别
运行测试。
