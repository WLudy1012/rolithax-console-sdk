---
name: rolithax-schema-plugin
description: Create, validate, replay, package, or review Rolithax Schema native console plugins and Git plugin sources using JSON metadata, XML controls, YAML rules, or sandboxed Lua.
---

# Rolithax Schema 插件

先读 [格式规范](../../spec/format.md)。处理源发布时再读 [README 源协议](../../spec/repository.md)。本仓库的 Python SDK 必须调用 Launcher 的原生 `rolithax-schema` CLI；缺少原生引擎时先构建并配置 `ROLITHAX_SCHEMA_CLI`，不要复制或模拟另一套解析器。

- 从 `python rolithax_schema_sdk.py init DIR namespace.plugin` 开始。Lua 使用 `--engine lua`。UI 用 section/row 分组，metric/badge/progress 表达日志状态，field 专门收集命令参数。
- 先确定服务端或插件版本、语言与稳定日志来源。查官方源码或文档，将对应 commit 和样本来源记入插件 README。合成格式样本与真服测试须分别标注，不能用一个样本宣称覆盖所有发行版本。
- 日志仅更新展示状态；声明明确的 action 和 inputs 验证器。权限、具体命令确认、断流状态和热替换由宿主管理。Lua 无网络/文件/命令执行能力，不依赖未开放的标准库。
- Lua 每次加载或日志回调限 4 MiB、100000 个 VM/C 库共享工作单位、25 ms。优先使用 string.sub 和普通 find；含量词的模式必须以 ^ 开头且至多一个量词，gmatch 不允许量词。不要通过 pcall、字节码或 C 库循环规避限制；这些入口已关闭或计入预算。YAML 数字捕获最多 32 字节。
- 用 validate、replay、pack 检查候选。添加正常输出、无关聊天、重复事件、颜色/时间外层及非法参数的样本；不要保存实际令牌、玩家隐私或管理员凭据。
- YAML 源用 README 的 files 清单，运行 source-index 更新 SHA-256 和大小。Lua 必须提供已存在的预构建 ZIP URL、摘要和字节数；包中包含 business.lua 源码。
- 在 Android 与 Windows 检查窄屏、长标签、状态过期与旧版本确认失效。报告已完成的回放、构建、平台实测和未验证范围；发布操作遵循当前用户授权。
