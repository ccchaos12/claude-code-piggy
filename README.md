# claude-code-piggy

让 [dsh-piggy](https://github.com/CLICGGER-TYPES/dsh-piggy) 桌宠随你在 Claude Code 里的工作成长。思路照搬 [opencode-piggy](https://github.com/WolalaQAQ/opencode-piggy) 的投递层（先落盘、再投递、按 ID 去重），入口换成 Claude Code hooks。单个 Python 文件，只用标准库。

## 事件映射

| Claude Code hook | 游戏事件 |
|---|---|
| `UserPromptSubmit` | `message` |
| `PostToolUse` | `tool`（按 `tool_use_id` 去重） |
| `PostToolUseFailure` | `toolError` |
| `Stop` | `turn` |
| `StopFailure` | `agentError` |

只发送 `{version, source: "claude-code", id, type, at}`，不发送 prompt、工具参数或输出。

## 安装

1. 把脚本放到固定位置：

```bash
mkdir -p ~/.claude/hooks && cp piggy_hook.py ~/.claude/hooks/ && chmod +x ~/.claude/hooks/piggy_hook.py
```

2. 在 `~/.claude/settings.json` 里加入（与已有字段合并）：

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

3. 用 `--work-events` 启动 dsh-piggy（安装版在启动参数里加，源码版在 `apps/desktop` 里 `npx electron . --work-events`）。

## 行为

- 游戏没开：事件存进 `~/Library/Application Support/claude-code-piggy/events/`，下次任何 hook 触发或手动 `--flush` 时补投。
- 只有游戏返回 `applied` / `duplicate` / `ignored` 才删除记录；`429` 按 `Retry-After` 退避；超过 7 天的事件保留但不再投递。
- 每次投递最多耗时约 3 秒；脚本永远以 0 退出、不向 stdout 输出，不会打断会话。

## 常用命令

```bash
python3 ~/.claude/hooks/piggy_hook.py --status
```

```bash
python3 ~/.claude/hooks/piggy_hook.py --flush
```

环境变量：`PIGGY_GAME_DIR`（`endpoint.json` 所在目录）、`PIGGY_USERDATA`、`CLAUDE_PIGGY_QUEUE`（队列目录）。
