# mtp-contracts-core

MTP 测试用例契约的唯一事实来源，提供：

- YAML/JSON 用例 Schema 与语义校验；
- 变量解析和凭据脱敏；
- 错误、执行结果、适配器及平台配置数据模型。

本项目不提供 MCP 服务、不执行测试，也不连接任何目标系统。`mtp-contracts-mcp`
和 `mtp-platform` 都依赖本包，不再保存契约源码副本。

## 本地开发

```bash
uv sync --frozen --extra dev
uv run --frozen pytest -q
```

相邻目录中的两个消费项目目前通过 `tool.uv.sources` 使用本地可编辑依赖，便于三个
项目一起开发。发布时创建版本标签，例如 `v0.1.0`，然后让消费项目依赖固定版本，
不要跟踪 `main`：

```toml
dependencies = ["mtp-contracts-core==0.1.0"]

[tool.uv.sources]
mtp-contracts-core = { git = "https://github.com/sundonglai03/mtp-contracts-core.git", tag = "v0.1.0" }
```

契约有不兼容变更时必须提升主版本或 `schema_version`，并在消费项目升级依赖前分别
运行测试。
