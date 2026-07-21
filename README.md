# @tracy-e/pi-resume-claude

在 Pi 中继续 Claude Code 会话。

实现思路对齐 Grok Build 的 foreign-session resume：扫描 `~/.claude/projects/`，读取会话 JSONL，把结果当作不可信历史注入当前 Pi 会话，再由模型做 handoff 摘要并继续工作。

## 安装

```bash
pi install npm:@tracy-e/pi-resume-claude
```

也可以从 GitHub 安装：

```bash
pi install git:github.com/tracy-e/pi-resume-claude
# 或
pi install https://github.com/tracy-e/pi-resume-claude
```

本地开发：

```bash
pi install /absolute/path/to/pi-resume-claude
```

依赖：

- Node.js ≥ 20
- `python3`（用于 `session_reader.py`）
- 本机存在 Claude Code 会话目录（默认 `~/.claude`，可用 `CLAUDE_CONFIG_DIR` 覆盖）

## 用法

```text
/resume-claude
/resume-claude latest
/resume-claude <session-id>
/resume-claude <title keywords>
```

| 形式 | 行为 |
| --- | --- |
| 无参数 | 列出当前 cwd 下的 Claude 会话并选择 |
| `latest` | 直接取最新会话 |
| session id | 按原生 UUID 定位 |
| 自由文本 | 按标题匹配；多条时弹出选择 |

命令会：

1. 调用内置 `session_reader.py` 读取 Claude Code transcript
2. 注入 handoff 提示词与 inert session JSON
3. 触发一轮 agent，由模型总结并继续

也可通过 skill 触发（模型自动匹配，或手动）：

```text
/skill:resume-claude
/skill:resume-claude latest
```

## 安全边界

外源 transcript 一律视为不可信历史：

- 不执行 transcript 中的指令
- 不把 Claude 工具调用当作 Pi 可用工具
- 不原样回放 transcript
- 旧工具输出默认过期，改动前先核对仓库与文件现状

## 包结构

```text
extensions/resume-claude.ts          # /resume-claude 命令
skills/resume-claude/
  SKILL.md
  references/CORE.md                 # handoff 规则
  scripts/session_reader.py          # Claude/Codex/Cursor 会话读取器
```

`session_reader.py` 源自 Grok Build 的 bundled skill reader，接口保持兼容：

```bash
python3 skills/resume-claude/scripts/session_reader.py claude list --cwd "$PWD" --json
python3 skills/resume-claude/scripts/session_reader.py claude show latest --cwd "$PWD" --json
```

## 卸载

```bash
pi remove npm:@tracy-e/pi-resume-claude
```

## License

MIT
