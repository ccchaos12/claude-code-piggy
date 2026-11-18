# claude-code-piggy

让 [dsh-piggy](https://github.com/CLICGGER-TYPES/dsh-piggy) 桌宠跟着你的工作成长，支持 **macOS 和 Windows**。投递方式参考 [opencode-piggy](https://github.com/WolalaQAQ/opencode-piggy)：事件先存到本地，再发给游戏，并按 ID 去重。只用 Python 3 自带的库，不需要安装任何依赖。

仓库里有两样东西，可以只用其中一个：

| 文件 | 作用 |
|---|---|
| `piggy_input.py` | **键鼠计数器**：按你打字和点鼠标的次数让小猪成长，不需要任何 AI 工具 |
| `piggy_hook.py` | **Claude Code hook**：按你在 Claude Code 里的工作让小猪成长 |

## 准备

1. 安装 [Python 3](https://www.python.org/downloads/)。Windows 安装时勾选 "Add python.exe to PATH"；macOS 一般已自带 `python3`。
2. 从 [dsh-piggy Releases](https://github.com/CLICGGER-TYPES/dsh-piggy/releases) 下载游戏（Windows 用 `dsh-piggy-setup-*.exe`，Mac 用 `*-arm64.dmg` 或 `*-x64.dmg`），每次都用 `--work-events` 启动：
   - macOS：`open -a dsh-piggy --args --work-events`
   - Windows：在 dsh-piggy 快捷方式上右键 → 属性 → "目标"末尾加上 ` --work-events`，以后都从这个快捷方式打开

游戏没开时事件会先存在本地，下次打开游戏后自动补发（只限 7 天内的事件）。

## 键鼠计数器

| 活动 | 游戏事件 |
|---|---|
| 每 200 次按键 | `tool` |
| 每 50 次点击 | `tool` |
| 累计活跃 25 分钟（60 秒内有输入算活跃） | `turn` |

每小时最多 40 个事件，游戏自己的每日上限仍然有效。可以用环境变量 `PIGGY_KEYS_PER_EVENT`、`PIGGY_CLICKS_PER_EVENT`、`PIGGY_ACTIVE_MIN_PER_TURN`、`PIGGY_MAX_PER_HOUR` 调整。

**隐私**：只统计次数，不记录按了哪个键、点在哪里，也不记录任何输入内容。发给游戏的只有 `{version, source: "input", id, type, at}`。
- macOS：读取系统自带的事件计数，不需要"输入监控"权限。
- Windows：用系统的低级键鼠钩子计数，代码只检查"按下"这一类消息。不需要管理员权限，但个别杀毒软件可能把键盘钩子当成可疑行为并提示，可以去看 `piggy_input.py` 的源码确认。

### macOS 安装

在仓库目录运行，装好后会在后台运行，并在每次登录时自动启动：

```bash
./install-input.sh
```

查看进度：

```bash
python3 ~/Library/Application\ Support/piggy-input/app/piggy_input.py --status
```

卸载：

```bash
./uninstall-input.sh
```

### Windows 安装

在仓库目录打开 PowerShell 运行，装好后会在后台运行（没有窗口），并放进"启动"文件夹，以后登录时自动启动：

```powershell
powershell -ExecutionPolicy Bypass -File install-input.ps1
```

查看进度：

```powershell
python "$env:APPDATA\piggy-input\app\piggy_input.py" --status
```

卸载：

```powershell
powershell -ExecutionPolicy Bypass -File uninstall-input.ps1
```

## Claude Code hook

| Claude Code hook | 游戏事件 |
|---|---|
| `UserPromptSubmit` | `message` |
| `PostToolUse` | `tool`（按 `tool_use_id` 去重） |
| `PostToolUseFailure` | `toolError` |
| `Stop` | `turn` |
| `StopFailure` | `agentError` |

只发送 `{version, source: "claude-code", id, type, at}`，不发送 prompt、工具参数或输出。

1. 把 `piggy_hook.py` 复制到 `~/.claude/hooks/`（Windows 是 `%USERPROFILE%\.claude\hooks\`）。
2. 在 `~/.claude/settings.json` 里加入下面的内容，和已有字段合并。**Windows 上把 `python3` 换成 `python`。**

```json
{
  "hooks": {
    "UserPromptSubmit":   [{ "hooks": [{ "type": "command", "command": "python3 ~/.claude/hooks/piggy_hook.py", "timeout": 10 }] }],
    "PostToolUse":        [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python3 ~/.claude/hooks/piggy_hook.py", "timeout": 10 }] }],
    "PostToolUseFailure": [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python3 ~/.claude/hooks/piggy_hook.py", "timeout": 10 }] }],
    "Stop":               [{ "hooks": [{ "type": "command", "command": "python3 ~/.claude/hooks/piggy_hook.py", "timeout": 10 }] }],
    "StopFailure":        [{ "hooks": [{ "type": "command", "command": "python3 ~/.claude/hooks/piggy_hook.py", "timeout": 10 }] }]
  }
}
```

查看状态：`python3 ~/.claude/hooks/piggy_hook.py --status`；手动补发：`... --flush`。

## 行为细节

- 只有游戏返回 `applied` / `duplicate` / `ignored` 才删除本地记录；返回 `429` 时按 `Retry-After` 等待；超过 7 天的事件保留在磁盘上，不再发送。
- hook 每次最多花约 3 秒发送，永远以 0 退出，不会打断 Claude Code。
- 键鼠计数器同一时间只会运行一份，重复启动的会直接退出，不会重复计数。

| 数据 | macOS | Windows |
|---|---|---|
| 游戏接口（`endpoint.json`） | `~/Library/Application Support/piggy/work-endpoint` | `%APPDATA%\piggy\work-endpoint` |
| 键鼠计数器队列与进度 | `~/Library/Application Support/piggy-input` | `%APPDATA%\piggy-input` |
| Claude Code hook 队列 | `~/Library/Application Support/claude-code-piggy` | `%APPDATA%\claude-code-piggy` |

其他环境变量：`PIGGY_GAME_DIR`（`endpoint.json` 所在目录）、`PIGGY_USERDATA`、`CLAUDE_PIGGY_QUEUE`、`PIGGY_INPUT_QUEUE`。
