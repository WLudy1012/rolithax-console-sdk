---
name: rolithax-schema-plugin
description: Create, validate, replay, package, or review Rolithax Schema plugins and Git sources. Covers SDK 1–3, JSON metadata, native XML controls and media, YAML log rules, sandboxed Lua, typed actions, and instance version checks.
---

# Rolithax Schema 插件

先读 [格式规范](../../spec/format.md)。处理源发布时再读 [README 源协议](../../spec/repository.md)。本仓库的 Python SDK 必须调用 Launcher 的原生 `rolithax-schema` CLI；缺少原生引擎时先构建并配置 `ROLITHAX_SCHEMA_CLI`，不要复制或模拟另一套解析器。

- 用 `rolithax-schema-sdk doctor` 确认引擎版本，再用 `init DIR namespace.plugin` 建项目。Lua 使用 `--engine lua`。如需兼容旧宿主，指定 `--sdk-version 2`，不要在旧格式中混用新控件。
- UI 用 section、row、tabs 和 expander 分组。用 metric、badge、progress 表达日志状态。需要命令输入时选 field、toggle 或 select。图片和视频引用清单里的资产 ID，填写能独立读懂的 label。显示等待、未知和空结果，拒绝把样本写成运行时数据。
- 先确定服务端或插件版本、语言与稳定日志来源。查官方源码或文档，将对应 commit 和样本来源记入插件 README。合成格式样本与真服测试须分别标注，不能用一个样本宣称覆盖所有发行版本。
- 日志及 `on_preflight`、`on_action` 结果只更新展示状态。声明具体 action 与输入验证器。文件动作选用实例或 MCDR 根目录别名，宿主机任务引用管理员配置的 taskId。Launcher 负责版本探测、实例授权、逐次确认和热替换。Lua 保持在受限运行时内，不调用网络、文件或进程接口。
- Lua 每次加载或日志回调限 4 MiB、100000 个 VM/C 库共享工作单位、25 ms。优先使用 string.sub 和普通 find；含量词的模式必须以 ^ 开头且至多一个量词，gmatch 不允许量词。不要通过 pcall、字节码或 C 库循环规避限制；这些入口已关闭或计入预算。YAML 数字捕获最多 32 字节。
- 用 `check`、`replay`、`pack` 和再次 `check ZIP` 检查候选。准备正常输出、无关聊天、重复事件、颜色或时间前缀、非法输入以及失败预检样本。Lua 回调可用 `replay --events` 输入合成的预检和已确认动作结果。
- V2 README 填齐仓库及插件的名称、说明、作者、主页与图标，插件还需版本、SDK 版本和许可证。YAML 可列出完整文件集合，Lua 使用已存在的预构建 ZIP URL。变更文件后用 `source-index` 更新摘要，再用 `source-index --check` 验证。
- 在 Android 与 Windows 检查窄屏、长标签、状态过期与旧版本确认失效。报告已完成的回放、构建、平台实测和未验证范围；发布操作遵循当前用户授权。
