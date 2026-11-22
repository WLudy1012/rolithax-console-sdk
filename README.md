# Rolithax Schema SDK

Rolithax Schema 把 Minecraft 服务端日志转成 Android 与 Windows 的原生控制面板。插件由 JSON 元数据、XML 布局及 YAML 规则或 Lua 逻辑组成，不需要在服务器安装额外的 Rolithax 插件。

SDK 3 的校验与回放直接使用 [RolithaxLauncher 的 C++ 引擎](https://github.com/WLudy1012/RolithaxLauncher/tree/dev/native/schema)。保留 SDK 1、2 插件格式，新增图片、视频、原生组合控件、版本预检和需要授权的文件及宿主机动作。

## 开始

SDK CI 提供 Windows x64、Linux x64/ARM64 和 macOS x64/ARM64 的工具包。解压后使用 Python 3.11+ 和包内的 `bin/rolithax-schema-sdk`，Windows 使用 `bin\rolithax-schema-sdk.cmd`。从源码构建原生 CLI 时需要 CMake 3.22+ 和支持 C++17 的编译器。

```sh
cmake -S ../RolithaxLauncher/native/schema -B build/schema -DCMAKE_BUILD_TYPE=Release
cmake --build build/schema --config Release --parallel
export ROLITHAX_SCHEMA_CLI="$PWD/build/schema/rolithax-schema"
python rolithax_schema_sdk.py init ../my-panel example.my-panel
python rolithax_schema_sdk.py check ../my-panel
python rolithax_schema_sdk.py replay ../my-panel ../my-panel/fixtures/logs.txt ../my-panel/fixtures/expected.json
python rolithax_schema_sdk.py pack ../my-panel ../my-panel.zip
```

Windows 使用 `$env:ROLITHAX_SCHEMA_CLI = "C:\path\to\rolithax-schema.exe"`，Visual Studio 多配置构建的程序通常在 `build/schema/Release/`。也可以为每次调用传入 `--native <路径>`，放在子命令之前。将本仓库 `bin` 加入 PATH 后可运行 `rolithax-schema-sdk`。

生成 Lua 模板：

```sh
python rolithax_schema_sdk.py init ../my-lua-panel example.lua-panel --engine lua
```

`init` 默认创建 SDK 3 项目。面向旧版 Launcher 使用 `--sdk-version 2`。安装工具后先运行 `rolithax-schema-sdk doctor` 检查 Python 命令是否找到匹配的原生引擎。

## 命令

| 命令 | 用途 |
| --- | --- |
| `init DIR [ID] [--engine yaml\|lua] [--sdk-version 2\|3]` | 在新目录或空目录生成可回放模板 |
| `check DIR_OR_ZIP [--format text\|json]` | 离线校验项目或构建包，检查本地媒体及原生布局 |
| `doctor` | 显示工具版本、目标平台和原生 SDK 范围 |
| `validate DIR` | 用原生引擎检查 manifest、XML、YAML/Lua 和动作绑定 |
| `replay DIR LOG [EXPECTED] [--events FILE]` | 回放日志及模拟宿主事件，可断言最终字段值 |
| `pack DIR OUTPUT.zip` | 原生校验后生成确定性 ZIP，输出 SHA-256 与字节数 |
| `source-index ROOT [--check]` | 校验源 README；更新或检查纯文件插件的摘要与大小 |
| `fetch REPOSITORY_URL ID OUTPUT.zip [--asset-cache DIR]` | 读取 GitHub、GitLab 或 HTTPS README，校验并下载完整插件与外链媒体 |

`replay` 的 EXPECTED 是字段名到字符串值的 JSON 对象；仅检查写出的字段。样本上限 16 MiB / 100000 行。任何校验、回放、摘要或路径错误都会非零退出；不会自动回退到其他解析器。

事件样本是由 `{ "eventType": "preflight" | "action", "result": { ... } }` 组成的 JSON 数组，按顺序在日志样本之后交给 Lua 回调。`replay` 不连接实例或执行动作。

## 插件结构

```text
plugin.json
ui.xml
rules.yaml       YAML 引擎
business.lua     Lua 引擎
fixtures/        开发样本，不打入插件 ZIP
assets/          SDK 3 声明的包内媒体
```

YAML 的根目录包含 `plugin.json`、`ui.xml`、`rules.yaml`。Lua 使用 `business.lua`，也可以附带空的 `rules.yaml`。SDK 3 包一并收录清单声明的 `assets/` 媒体。外链提供 HTTPS、大小和 SHA-256，`fetch` 校验后保存在输出 ZIP 旁的 `.assets` 目录。旧包的 512 KiB 限额保持不变。Lua 使用预构建 ZIP 分发，包内保留可审阅的 Lua 源码。

- [格式规范](spec/format.md)：元数据、原生 XML、规则、Lua、参数校验与热替换。
- [插件源协议](spec/repository.md)：README 发现、纯文件下载、预构建包和完整性检查。
- [模板仓库](https://github.com/WLudy1012/rolithax-console-plugin-template)：可直接创建自己的仓库。
- [官方插件源](https://github.com/WLudy1012/rolithax-schema-plugins)：Paper、LuckPerms、Chunky 和 MCDR 文件工作区。
- [Agent skill](skills/rolithax-schema-plugin/SKILL.md)：供社区开发与审查使用。

## 开发与 CI

```sh
python -m unittest discover -s tests -v
```

设置 `ROLITHAX_SCHEMA_CLI` 后，测试会额外验证真实 C++ 引擎的 YAML/Lua 校验、回放与打包。未设置时只运行 Python 包装层的路径、摘要、ZIP 和 README 测试。

CI 默认使用工作流中固定的 Launcher 提交构建原生引擎。仓库变量 `ROLITHAX_RUNTIME_REPOSITORY` 和 `ROLITHAX_RUNTIME_REF` 可覆盖来源。SDK、模板和客户端升级时一起回放样本。

日志和事件结果只更新显示字段。需要与服务器交互时，Launcher 在选定实例上核对授权，并请用户确认具体操作。宿主任务使用 Daemon 管理员配置的任务 ID。
