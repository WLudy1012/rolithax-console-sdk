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

SDK 的 fetch 当前支持 GitHub HTTPS 仓库及 raw.githubusercontent.com 的固定 commit README 地址。其他 Git 托管服务可以提供等价的固定提交读取适配器；不要把任意网页地址当成原始文件。
