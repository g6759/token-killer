# Local MCP client setup | 本地 MCP 客户端接入

Supported clients / 支持客户端: **Codex CLI/IDE, Claude Code CLI, Google Antigravity CLI/IDE**. Each can launch the local Python server over stdio. No API key is required. / 三者均可通过 stdio 启动本机 Python 服务，不需要 API Key。

## Generate snippets | 生成配置片段

From the project root in PowerShell / 在项目根目录的 PowerShell 中运行：

```powershell
python -B .\游戏MCP\setup_mcp.py --out-dir .\游戏MCP\client-configs
```

The generator writes three standalone snippets only to the chosen output directory; it never reads or changes client config. / 生成器只在指定目录写入三份独立片段，不读取或修改客户端配置。

Use the Python executable running the script by default. If MCP dependencies use another interpreter, pass `--python C:\path\to\python.exe`. / 默认 Python 为运行生成器的解释器；依赖在其他环境时用 `--python C:\完整路径\python.exe` 指定。

## Codex CLI and IDE | Codex CLI 与 IDE

Merge the section in `client-configs\codex.config.toml` into `%USERPROFILE%\.codex\config.toml`. Codex CLI and IDE share this configuration. `codex mcp add` is another option, but it writes user-level config directly, so the generator does not run it. Refresh/restart and check with `codex mcp list`.

将 `codex.config.toml` 中的 `[mcp_servers.research_game]` 区块手动合并到 `%USERPROFILE%\.codex\config.toml`。CLI 与 IDE 共用该配置。`codex mcp add` 也可添加，但会直接写用户级配置，所以生成器不会代为运行。刷新/重启后用 `codex mcp list` 检查。

Locally checked CLI version / 本机已核对版本：`codex-cli 0.171.0-alpha.13.2`。The generated command uses the absolute path to `游戏MCP/server.py`, so the client can start it from any working directory. / 生成的启动命令使用 `游戏MCP/server.py` 的绝对路径，可从任意工作目录启动。

## Claude Code CLI | Claude Code 命令行

`claude-code.mcp.json` uses the project `.mcp.json` shape. Merge its `mcpServers` object into `.mcp.json` at the project root, or add the same command and args through `/mcp`. It uses an absolute server path and works from any cwd. Review and approve a project server on first connection. Check status with `claude mcp list` or `/mcp`.

`claude-code.mcp.json` 是项目级 `.mcp.json` 结构。将其中的 `mcpServers` 对象手动合并到项目根目录 `.mcp.json`，或在 `/mcp` 中填入相同的 command/args。它使用服务脚本绝对路径，可从任意 cwd 启动。项目服务器首次连接需审阅并批准。用 `claude mcp list` 或 `/mcp` 查看状态。

Official CLI command / 官方 CLI 命令也支持 `claude mcp add-json <name> '<JSON>'`，默认 scope 为 local；`--scope project` 写项目 `.mcp.json`，`--scope user` 写用户级配置。按需手工运行；生成器不会运行此命令。Claude Code CLI is not installed here, so local version is unknown. Current official MCP docs state no minimum version. These steps target CLI; Claude Code Desktop Code sessions and IDE extensions also read local configuration.

Claude Code CLI 未安装于本机，版本未知。当前官方 MCP 文档未声明最低版本。本步骤针对 CLI；Claude Code Desktop 的 Code 会话和 IDE 扩展也读取本机配置。

## Google Antigravity CLI and IDE | Google Antigravity 命令行与 IDE

`antigravity.mcp_config.json` uses the documented schema for Antigravity CLI, IDE, and App. It uses an absolute server path and works from any cwd. For project scope, manually merge the entry into `<project>/.agents/mcp_config.json`. The global file is `%USERPROFILE%\.gemini\config\mcp_config.json` on Windows or `~/.gemini/config/mcp_config.json` on macOS/Linux and applies across projects; the generator leaves it untouched.

`antigravity.mcp_config.json` 使用 Antigravity CLI、IDE、App 的官方配置结构，服务脚本采用绝对路径，可从任意 cwd 启动。项目级配置请手动合并到 `<project>/.agents/mcp_config.json`。全局文件为 Windows 的 `%USERPROFILE%\.gemini\config\mcp_config.json` 或 macOS/Linux 的 `~/.gemini/config/mcp_config.json`，适用于所有项目；生成器不会修改该文件。

In CLI, enter `/mcp` to manage/reload connections. In IDE, use Agent side panel `…` → **MCP Servers** → **Manage MCP Servers** → **View raw config**. The config schema is shared; management UI differs. Antigravity CLI is not installed here, so local version is unknown. Google's current MCP docs specify support and fields but no minimum version.

CLI 输入 `/mcp` 管理/刷新连接。IDE 使用 Agent 侧栏 `…` → **MCP Servers** → **Manage MCP Servers** → **View raw config**。两端共用配置结构，但管理入口不同。Antigravity CLI 未安装于本机，版本未知。Google 当前 MCP 文档有支持平台和字段说明，但未声明最低版本。

## Identity and usage boundaries | 身份与用量边界

- Any connected client can use the general queue: `game_status` → `claim_turn` → make a candidate → `submit_turn`. `thread_id` is optional. / 任一已连接客户端都可走通用队列：`game_status` → `claim_turn` → 生成候选 → `submit_turn`。`thread_id` 可省略。
- When a user requests a new game from a client, set `start_game.client` to `codex-mcp`, `claude-code`, or `antigravity` for that real client. Pass `target_tokens` only when the user sets a token target. / 用户要求从某客户端新开对局时，`start_game.client` 按实际客户端填写 `codex-mcp`、`claude-code` 或 `antigravity`。只有用户指定 token 目标时才传 `target_tokens`。
- The square leaderboard's **single-session parallel batch** (`start_packing_batch` / `plan_packing_batch`) uses Codex `CODEX_THREAD_ID` for linking local usage. That is a Codex thread ID, not a Claude/Antigravity session ID. Never substitute or impersonate one; use this path only from the actual Codex conversation. / 方格冲榜的**单会话并行批次**使用 Codex `CODEX_THREAD_ID` 关联本机用量；它不是 Claude 或 Antigravity 的会话 ID，禁止冒充或替代。仅在真正的 Codex 对话里使用该路径。
- `claim_turn.thread_id` is only for linking Codex measured usage on certain square tasks. Third-party clients can omit it. / 部分方格任务中的 `claim_turn.thread_id` 只用于关联 Codex 实测用量。第三方客户端可留空。
- An external client may submit `usage_receipt` only from that round's real receipt, shaped as `{response_id, uncached_input_tokens, output_tokens}`. If any field is unavailable, omit the entire receipt. External usage is stored separately from native Codex subscription usage and excluded from its native leaderboard. Without a receipt, normal claim/submit remains available. Token-targeted rounds save the candidate and stop when the receipt is missing; otherwise the game proceeds by round count. / 外部客户端仅可传入该轮真实回执中的 `usage_receipt`，字段形如 `{response_id, uncached_input_tokens, output_tokens}`。缺少任一字段时，省略整份回执。外部用量与 Codex 订阅原生用量分列，不计入 Codex 原生榜单。缺少回执仍可正常接手和提交；设有 token 目标的局会保存候选并在缺回执时暂停，否则按轮数继续。
- Starting a new task changes the local queue. Call `start_game` only when the user asks to start one. / 新开任务会改变本机网页队列；仅在用户要求开局时调用 `start_game`。

## Offline check | 离线检查

For a Claude message receipt using Anthropic’s native counters, report `input_tokens + cache_creation_input_tokens` as uncached input. Report `output_tokens` unchanged. Exclude cache reads. When your client exposes only account percentages or a conversation total, omit the entire receipt. / Claude 单条消息回执按上述公式合并普通输入和首次写入缓存的输入。只有账户百分比或会话总量时，整份用量回执留空。[Anthropic cache counters](https://platform.claude.com/docs/en/build-with-claude/prompt-caching#tracking-cache-performance)

```powershell
python -B -m unittest 游戏MCP.test_client_setup
```

The test starts the generated command from a temporary directory and discovers the tools over stdio. A separate in-memory fixture checks claim and submit without a model call. / 测试从任意临时目录启动生成的命令，通过 stdio 握手并列出工具。另用内存夹具检查接手与提交，全程无需模型。

macOS/Linux command example / macOS/Linux 命令示例：

```sh
python3 -B 游戏MCP/setup_mcp.py --out-dir ./游戏MCP/client-configs
```

The generated Codex snippet is for `~/.codex/config.toml`; the Claude Code project file is `<project>/.mcp.json`. / 生成的 Codex 片段合并到 `~/.codex/config.toml`；Claude Code 项目文件为 `<project>/.mcp.json`。

## Official references | 官方文档

- [Codex MCP servers](https://developers.openai.com/learn/docs-mcp) — shared CLI/IDE config / CLI 与 IDE 共用配置.
- [Codex config reference](https://developers.openai.com/ja-JP/docs/config-file/config-reference) — stdio `command`, `args`, `cwd` fields / stdio 的 `command`、`args`、`cwd` 字段。
- [Claude Code MCP](https://code.claude.com/docs/en/mcp) — stdio, scopes, approval / stdio、scope、审批。
- [Google Antigravity MCP](https://antigravity.google/docs/mcp) — CLI/IDE/App paths and fields / CLI、IDE、App 路径与字段。
