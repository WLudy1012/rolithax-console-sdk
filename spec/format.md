# Rolithax Schema 插件格式

原生实现与 C ABI 定义在 Launcher 的 `native/schema`。此 SDK 的 validate/replay 调用同一程序；以下格式不由 Python 重新解释。

本页前半部分是 SDK 1、2 的格式。SDK 3 延续这些定义，并在文末列出新增字段、控件和回调。

## plugin.json

```json
{
  "id": "example.permissions",
  "version": "1.0.0",
  "sdkVersion": 2,
  "server": "minecraft",
  "engine": "yaml",
  "name": "权限管理",
  "description": "查看玩家与权限组",
  "compatibility": {
    "plugin": "LuckPerms",
    "versions": ["5.4.x", "5.5.x"],
    "locales": ["en"],
    "verification": "upstream-format-fixture"
  }
}
```

必填：`id/version/sdkVersion/server/engine`。可选：`name/description/compatibility`。ID 是至少两段的小写命名空间，总长最多 96；版本为三个非负整数组成的语义版本；server 为小写符号。SDK 1 不要求 engine，并按 YAML 处理。

文件为 UTF-8：manifest 最多 16 KiB；XML、YAML 各 64 KiB；Lua 最多 128 KiB。没有从 manifest 自动执行脚本、访问网络或读取文件的机制。

## ui.xml

根节点为 `<panel title="...">`，最多 128 控件、8 层嵌套。所有控件需要稳定的 id 和 label；id 以小写字母开头，后续只允许小写字母、数字、连字符，最长 64。

| 控件 | 绑定 | 展示 |
| --- | --- | --- |
| section、row | children | 分区、同组操作或指标 |
| text | field 可选 | 静态说明或动态文本 |
| status、badge | field | 状态标签 |
| metric | field | 关键数值 |
| progress | field | 0–100 百分比，宿主截断超出范围的显示值 |
| list | field | 由换行分隔的条目 |
| field | field | 用户参数输入 |
| button | action | 用户触发的命令动作 |
| divider | 无 | 分隔 |

Schema 2 通用展示属性：`description/tone/unit/placeholder`。宿主负责 Material 3 / Fluent 的原生控件、配色、键盘和窄屏排列。插件不能注入 HTML、脚本、外部实体、任意样式或外部图片。

```xml
<panel title="世界预生成">
  <section id="task" label="当前任务">
    <progress id="progress-bar" label="完成进度" field="progress" unit="%"/>
    <button id="refresh" label="查询进度" action="refresh" tone="primary"/>
  </section>
</panel>
```

## rules.yaml

这是两空格缩进的受限映射格式，所有值用双引号包围。仅有 `initial/rules/commands` 三个顶层分区，每个最多 128 项，单个标量最多 512 字节。没有 YAML tag、anchor、数组或任意正则表达式。

```yaml
initial:
  progress: "0"
rules:
  progress:
    contains: "Task running for "
    field: "progress"
    capture: "percent"
commands:
  refresh:
    text: "chunky progress"
```

每条规则的 prefix/suffix/contains 同时满足才匹配。日志会先去掉常见 Minecraft 时间/等级外层及颜色。规则需声明 field，使用 value 常量或 capture 提取，二者不能同时存在。operation 可为 set（默认）或 append（最近 16 条）。

capture 支持 text、player、player-event、players、online、online-summary。Schema 2 增加 number（匹配片段中的首个有符号十进制数）与 percent（紧邻 % 的数字）。prefix 与 suffix 会界定 text 提取片段。不同语言、其他插件转发、聊天中出现相似内容，都需要负向样本；显示数据不是命令的可信来源。

## 动作与参数

固定命令不依赖日志输出。多参数必须显式声明：

```yaml
commands:
  add-parent:
    text: "lp user {target} parent add {group}"
    inputs: "target:player,group:token"
```

每个参数都须对应 XML field。字段值来自用户输入，不能用日志捕获值自动填充并执行。

| 参数类型 | 范围 |
| --- | --- |
| player | 1–16 位 Java 玩家名，ASCII 字母、数字、下划线 |
| token | 1–128 位 ASCII 字母数字及 _ . : / + - |
| integer | 可选负号，最多 10 位整数 |
| number | 可选负号及小数点，最长 32 字节 |

命令模板最多 256 字节，参数替换后的命令最多 4096 字节，不含换行、引号、空白参数或 shell 分隔符。类型校验防止注入，业务合法范围（例如 Chunky 半径）仍由服务端命令验证。SDK 1 的单个 player 参数仍兼容。

## business.lua

Lua 引擎返回一个表：

```lua
return {
    initial = { state = "等待日志" },
    commands = { refresh = { text = "list" } },
    on_log = function(line, state)
        if string.sub(line, 1, 6) == "Done (" then
            return { state = "运行中" }
        end
        return {}
    end
}
```

`state` 是当前状态副本；只有返回表更新已声明的 initial 字段。Lua 不能发送命令。commands 使用与 YAML 相同的 text/inputs 合同。使用 string.sub、普通字符串查找等有界操作；复杂模式、无限循环和超额内存会被拒绝。

运行时采用 Lua 5.5.1。每次加载或处理一条日志，脚本可分配 4 MiB 内存，最多消耗 100000 个工作单位，并有 25 ms 截止时间。VM 指令和 C 标准库的估算工作量共用额度；VM 每 100 条指令检查一次，内存分配也检查时间。超额调用不提交字段更新，热替换失败保留旧面板。

运行时不开放 os、io、package、debug、require、load、loadfile、dofile、coroutine、pcall、xpcall、collectgarbage、print、warn、元表访问和 string.dump/pack/packsize/unpack；不接受 Lua 字节码。模式函数的输入最多 8 KiB、模式最多 256 字节，至多一个量词，且含量词的模式必须以 `^` 开头。gmatch 不允许量词，因为 Lua 不把它的 `^` 当作锚点。平衡、前沿和反向引用模式不可用。`string.find(text, literal, 1, true)` 可进行普通字符串查找；表操作最多 8192 个元素，move/unpack 的显式范围也受限制。

不要让日志构造函数名或命令。使用 Lua 时在源 README 提供预构建 ZIP 的 HTTPS URL、SHA-256 与字节数。YAML 的 number/percent 捕获使用线性扫描，超过 32 字节的数字不更新状态。

## 原生接口与热替换

`rolithax-schema` 从 stdin 逐行读取 JSON，在 stdout 逐行返回 JSON，整个进程持有同一会话：

```json
{"op":"inspect","source":{"manifest":"JSON text","ui":"XML text","rules":"YAML text","lua":""}}
```

响应为 `{"ok":true,"data":...}` 或 `{"ok":false,"error":{"file":"ui.xml","line":2,"message":"..."}}`。操作包括 inspect、reload、log、snapshot、command、reset、gap、restore。

reload 会在候选包校验和日志回放完成后替换当前包；失败保留当前有效面板。packageRevision 变化使旧操作确认失效；command 需同时匹配 actionId、arguments、授权列表及 expectedRevision。断流由 gap 标记为 stale，UI 必须显示数据可能过期。安装包或 README 不能自行授予动作权限。

## SDK 3 元数据和媒体

将 `sdkVersion` 设置为 `3`。`name`、`description`、`authors`、`license`、HTTPS `homepage` 和 `icon` 为必填字段，`icon` 指向 `assets` 中的图片 ID。`compatibility` 可继续记录版本和日志语言。

`assets` 的每个条目包含 `id`、`mediaType`、`size` 和 `sha256`，再声明 `path` 或 `url`。包内文件放在 `assets/`，外链使用无凭据的 HTTPS。图片支持 PNG、JPEG、WebP，单项最多 8 MiB。视频支持 MP4，单项最多 64 MiB。完整 ZIP 及媒体下载合计最多 128 MiB。宿主逐项校验后才安装，视频由用户主动播放。

```xml
<panel title="实例面板">
  <tabs id="views" label="功能">
    <tab id="overview" label="概览">
      <image id="logo" label="插件图标" asset="panel-icon"/>
      <expander id="manual" label="使用说明">
        <video id="preview" label="操作视频" asset="preview-clip"/>
      </expander>
    </tab>
    <tab id="settings" label="参数">
      <toggle id="enabled" label="启用" field="enabled"/>
      <select id="world" label="世界" field="world">
        <option id="overworld" label="主世界" value="world"/>
      </select>
    </tab>
  </tabs>
</panel>
```

`toggle` 和 `select` 都给命令提供输入。前者只能返回 `true` 或 `false`，可以使用 `boolean` 输入验证器。后者只接受自身声明的 `option.value`。`image` 与 `video` 只引用静态资产 ID，`label` 同时提供无障碍说明。

## SDK 3 动作和版本预检

`plugin.json` 用 `actions` 保存命名动作。`button.action` 可引用 `actions` 或原有 `commands`，同一个 ID 只能声明一次。

```json
{
  "actions": {
    "inspect-config": {"kind":"file","operation":"list","root":"mcdr-config"},
    "diagnostics": {"kind":"host-task","taskId":"server-status"}
  },
  "preflight": [{"kind":"mcdrPlugin","id":"example_plugin","version":"2.1.0"}]
}
```

文件动作支持 `list`、`read`、`write`、`upload`、`download`、`rename`、`delete`、`mkdir`。`root` 为 `instance`、`mcdr-config`、`mcdr-plugins` 或 `minecraft`，可选 `path` 是该目录内的相对路径。用户输入的文件内容和目的地由 Launcher 收集。宿主任务只声明 Daemon 管理员配置的任务 ID。两种动作都需要用户在目标实例上授权并逐次确认。

`preflight.kind` 可为 `mod`、`minecraftPlugin` 或 `mcdrPlugin`。启用插件前，Launcher 用当前实例查询该 ID 并比较完整版本字符串。找不到、无法确认或版本不符时，面板应写明原因。预检结果不会发送命令或修改文件。

Lua 保留 `on_log(line, state)`。可选 `on_preflight(result, state)` 和 `on_action(result, state)` 接收宿主返回的有界 JSON 对象，按原有规则更新已声明字段。回调共用当前沙箱的工作量、时间和内存限制。解析器的 `event` 请求要求 `eventType`、`result` 和当前 `expectedRevision`，不会接受过期的事件。参见 Launcher 原生接口规范中 `prepareAction` 和 `event` 的定义。
