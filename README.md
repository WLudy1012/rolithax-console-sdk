# Rolithax Schema SDK

Rolithax Schema 把 Minecraft 服务端日志转成 Android 与 Windows 的原生控制面板。插件由 JSON 元数据、XML 布局及 YAML 规则或 Lua 逻辑组成，不需要在服务器安装额外的 Rolithax 插件。

SDK 2 的校验与回放直接使用 [RolithaxLauncher 的 C++ 引擎](https://github.com/WLudy1012/RolithaxLauncher/tree/dev/native/schema)。本仓库只负责模板、命令行编排、可复现 ZIP 和 Git 源元数据，不再维护另一份 Kotlin 解析器。SDK 1 格式仍由原生引擎兼容。

## 开始

需要 Python 3.11+、CMake 3.22+ 和支持 C++17 的编译器。已有 SDK 发行包内附的原生 CLI 时，无需本地 C++ 工具链。

```sh
cmake -S ../RolithaxLauncher/native/schema -B build/schema -DCMAKE_BUILD_TYPE=Release
cmake --build build/schema --config Release --parallel
export ROLITHAX_SCHEMA_CLI="$PWD/build/schema/rolithax-schema"
python rolithax_schema_sdk.py init ../my-panel example.my-panel
python rolithax_schema_sdk.py validate ../my-panel
python rolithax_schema_sdk.py replay ../my-panel ../my-panel/fixtures/logs.txt ../my-panel/fixtures/expected.json
python rolithax_schema_sdk.py pack ../my-panel ../my-panel.zip
```

Windows 使用 `$env:ROLITHAX_SCHEMA_CLI = "C:\path\to\rolithax-schema.exe"`，Visual Studio 多配置构建的程序通常在 `build/schema/Release/`。也可以为每次调用传入 `--native <路径>`，放在子命令之前。将本仓库 `bin` 加入 PATH 后可运行 `rolithax-schema-sdk`。

生成 Lua 模板：

```sh
python rolithax_schema_sdk.py init ../my-lua-panel example.lua-panel --engine lua
```

## 命令

| 命令 | 用途 |
| --- | --- |
| `init DIR [ID] [--engine yaml\|lua]` | 在新目录或空目录生成可回放模板 |
| `validate DIR` | 用原生引擎检查 manifest、XML、YAML/Lua 和动作绑定 |
| `replay DIR LOG [EXPECTED]` | 将日志逐行交给原生会话；可断言最终字段值 |
| `pack DIR OUTPUT.zip` | 原生校验后生成确定性 ZIP，输出 SHA-256 与字节数 |
| `source-index ROOT [--check]` | 校验源 README；更新或检查纯文件插件的摘要与大小 |
| `fetch REPOSITORY_URL ID OUTPUT.zip` | 固定 GitHub commit、校验下载摘要、原生校验后打包 |

`replay` 的 EXPECTED 是字段名到字符串值的 JSON 对象；仅检查写出的字段。样本上限 16 MiB / 100000 行。任何校验、回放、摘要或路径错误都会非零退出；不会自动回退到其他解析器。

## 插件结构

```text
plugin.json
ui.xml
rules.yaml       YAML 引擎
business.lua     Lua 引擎
fixtures/        开发样本，不打入插件 ZIP
```

YAML ZIP 根目录恰好三个文件。Lua ZIP 根目录包含 `plugin.json`、`ui.xml`、`business.lua`，可以附带空的 `rules.yaml`。ZIP 限 512 KiB；解压后各文件单独限额。Lua 的“二进制下载”指已打包的 ZIP，包内仍然是可审阅的 Lua 源码。

- [格式规范](spec/format.md)：元数据、原生 XML、规则、Lua、参数校验与热替换。
- [插件源协议](spec/repository.md)：README 发现、纯文件下载、预构建包和完整性检查。
- [模板仓库](https://github.com/WLudy1012/rolithax-console-plugin-template)：可直接创建自己的仓库。
- [官方插件源](https://github.com/WLudy1012/rolithax-schema-plugins)：Paper、LuckPerms、Chunky。
- [Agent skill](skills/rolithax-schema-plugin/SKILL.md)：供社区开发与审查使用。

## 开发与 CI

```sh
python -m unittest discover -s tests -v
```

设置 `ROLITHAX_SCHEMA_CLI` 后，测试会额外验证真实 C++ 引擎的 YAML/Lua 校验、回放与打包。未设置时只运行 Python 包装层的路径、摘要、ZIP 和 README 测试。

CI 从 `ROLITHAX_RUNTIME_REPOSITORY`（默认 `WLudy1012/RolithaxLauncher`）的 `ROLITHAX_RUNTIME_REF`（默认 `dev`）构建唯一的原生引擎。发布时应把 REF 设为包含 `native/schema` 的已发布 commit SHA，以固定工具链行为。SDK、模板和客户端升级时一起回放同一套样本。

日志数据只更新显示字段。SDK 不会连接服务器，也不会执行任何 Minecraft 命令。插件命令须由宿主按实例授权，再将用户确认的具体命令交给所选远端连接。
