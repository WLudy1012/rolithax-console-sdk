# Rolithax Schema Git 插件源

用户添加 GitHub 仓库地址，例如 `https://github.com/WLudy1012/rolithax-schema-plugins`。客户端解析仓库 HEAD 为不可变 commit，再从该 commit 的根目录读取 README.md。README 必须恰有一个语言标记为 `rolithax-schema-repository` 的围栏 JSON；普通说明文字不参与解析。

## README 格式

```json
{
  "schemaVersion": 1,
  "id": "example.repository",
  "name": "示例插件源",
  "description": "Minecraft 控制面板",
  "plugins": [
    {
      "id": "example.panel",
      "version": "1.0.0",
      "name": "示例面板",
      "description": "查看运行状态",
      "engine": "yaml",
      "source": {
        "type": "files",
        "path": "plugins/example",
        "files": [
          {"path": "plugin.json", "sha256": "由 SDK 生成", "size": 0},
          {"path": "ui.xml", "sha256": "由 SDK 生成", "size": 0},
          {"path": "rules.yaml", "sha256": "由 SDK 生成", "size": 0}
        ]
      }
    }
  ]
}
```

示例的摘要字符串需要替换为真实 SHA-256；初始化可以填 64 个 0，然后运行 `source-index`。源 id 采用命名空间。插件 id 不重复，一份源最多 256 个插件，README 上限 256 KiB。插件可带 `compatibility` 对象与供阅读的 `readme` 相对路径。

files 模式仅用于 YAML，恰好列出 plugin.json、ui.xml、rules.yaml。path 相对于该 README 所在的仓库根目录，所有文件都从同一个固定 commit 读取。禁止绝对路径、反斜线、路径穿越和编码后的路径分隔符。每个文件先检查大小与 SHA-256，再用原生引擎校验，最后在本地打成 ZIP。安装不运行 Git hooks、构建脚本或仓库代码。

## Lua 预构建包

Lua 插件必须改用：

```json
{
  "type": "package",
  "url": "https://github.com/example/repository/releases/download/v1.0.0/example.panel-1.0.0.zip",
  "sha256": "64 位小写十六进制摘要",
  "size": 1234
}
```

URL 要求 HTTPS，不带凭据，重定向也保持 HTTPS。下载摘要和字节数必须与 README 一致；ZIP 内的 id/version/engine 必须与条目一致。禁止重复 ZIP 条目、链接、加密条目、目录穿越和未知文件。ZIP 根目录为 plugin.json、ui.xml、business.lua；rules.yaml 可以省略。所有条目按原始大小检查，压缩包上限 512 KiB。

先 `pack` 生成 ZIP，上传版本化下载地址，最后把 pack 输出的摘要及字节数写入 README。未上传的未来 URL 不代表可用插件，不能加入默认源的已发布目录。

## 维护流程

1. 修改插件，补上日志样本、期望字段和官方格式来源。
2. 运行 validate、replay 与 pack。
3. `source-index ROOT` 更新 files 模式的 README 摘要。
4. `source-index ROOT --check` 在 CI 验证一致性，再提交插件与 README。
5. 发布 Lua ZIP 后更新相应 package 条目。更新运行时、模板及 SDK 引用时固定 commit SHA。

摘要证明下载内容与选中的 Git commit 一致，不证明仓库作者可信。宿主仍独立管理来源信任、实例安装和逐动作授权。首次安装及更新都显示来源、插件版本与命令清单；授权不得由新版本扩张。

`fetch` 支持 GitHub、GitLab 仓库及自托管 HTTPS `README.md` 地址。GitHub 和 GitLab 仓库先解析为不可变 commit。自托管地址可以直接提供目录，下载内容仍按声明的摘要和大小核验。

## V2 源目录

SDK 3 读取 `rolithax-schema-repository-v2` 围栏，内部使用 `schemaVersion: 2`。同一 README 可保留一个旧围栏用于 SDK 1、2 客户端，新客户端优先读取 V2。README 总计仍限 256 KiB。

V2 源提供 `id`、`name`、`description`、`authors`、`homepage`、`icon` 和 `plugins`。每个插件增加 `sdkVersion`、`authors`、`license`、`homepage` 与 `icon`，并沿用 id、名称、版本、说明、引擎和下载方式。`icon` 为图片描述，包含 `mediaType`、`size`、`sha256` 和仓库相对 `path` 或 HTTPS `url`。SDK 3 插件的目录资料与 `plugin.json` 一致，目录图标与清单的图标资产使用相同摘要、大小和媒体类型。

SDK 3 的 YAML `files` 清单包括三个定义文件和所有包内 `assets/` 文件。ZIP 与 `files` 两种方式都会一次下载完整插件。Lua 继续使用 `package`。外链资源在安装时按清单下载、校验并缓存。安装结束后，用户另行选择实例和授权动作。

旧 SDK 1、2 插件可放进 V2 目录以显示图标和作者，原包格式保持不变。更新本地图标或包内文件后运行 `source-index ROOT`。同一 README 有两个版本时，命令更新 V2 围栏，旧围栏保留给仍在使用的旧包。自定义源的下载与校验不会运行仓库内的脚本。
