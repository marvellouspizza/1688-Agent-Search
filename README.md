# 1688 Agent Search

面向 1688 智能采购项目的终端 Agent。当前版本支持普通文字对话和受控网页搜索：

```text
启动 as1688
→ 读取非秘密配置
→ 选择或解析模型供应商
→ 读取供应商凭证
→ 获取并选择模型
→ 创建 Agent 与 Session
→ 组装上下文
→ 转换为供应商 API 请求
→ 需要公开信息时调用唯一允许的 `web_search` 工具
→ 本机 SearXNG 返回搜索结果
→ 接收并显示流式回复
→ 保存会话
→ 等待下一次输入
```

## 支持的模型供应商

### Local Codex / ChatGPT

复用本机 Codex 的 ChatGPT 登录和模型目录。选择这个供应商时才需要：

- Codex CLI 0.144.x
- `codex login` 使用 ChatGPT 登录

### OpenAI API

使用用户自己的 OpenAI API Key。这个供应商不需要安装 Codex。

- 通过 `GET /v1/models` 读取当前账号的文本模型。
- 通过 Responses API 请求模型。
- 通过 SSE 接收 `response.output_text.delta` 流式文字。
- 请求不提供任何工具，并拒绝非文字工具输出项。

在 macOS 上，API Key 保存到系统钥匙串；在非 macOS 系统或没有钥匙串
工具时，保存到权限为 `0600` 的 `~/.1688-agent-search/credentials.json`。
钥匙串更新或删除失败时会明确报错，不会静默改用另一份旧凭证。
API Key 不会写入普通 `config.json` 或 SQLite 会话库。

## 本地 SearXNG 搜索

本机 Codex Provider 会把本项目的工具注册表作为 `1688-tools` stdio MCP
Server 提供给模型。Codex 自带网页搜索、终端、文件、浏览器、插件、Skill 和
用户已有 MCP 均保持禁用；模型只能调用本项目注册的只读 `web_search`。

默认连接地址为 `http://127.0.0.1:8888`。若 SearXNG 运行在 OrbStack，须把
容器端口发布到 macOS 宿主机，例如：

```yaml
ports:
  - "127.0.0.1:8888:8080"
```

并在 SearXNG 的 `settings.yml` 中允许 JSON：

```yaml
search:
  formats:
    - html
    - json
```

可在 `~/.1688-agent-search/config.json` 配置非秘密连接信息：

```json
{
  "searxng_base_url": "http://127.0.0.1:8888",
  "searxng_timeout_seconds": 30,
  "max_iterations": 500
}
```

`max_iterations` 与 Hermes 一致，限制单轮对话中的模型/工具迭代次数；达到
上限时会关闭工具并让模型根据已经取得的结果给出最终总结，不会直接把整轮标记为失败。

SearXNG 仅提供搜索索引。查找 1688 商家时，程序会提供候选页面链接和搜索摘要，
不会把结果表述为库存、价格、发票或商家身份已核验。

## SOUL.md

SOUL 是每轮发送给模型的稳定身份与行为偏好。和 Hermes 一样，本项目每个
实例只使用一个全局文件：

```text
~/.1688-agent-search/SOUL.md
```

首次启动时若文件不存在，程序会创建采购版初始内容。之后直接编辑或替换这一个
文件即可切换身份；新 Session 会读取更新后的内容。不要同时加载多个 SOUL，避免
角色和规则冲突。

## 安装

支持 macOS 和 Linux，需要 Python 3.9 或更高版本。

直接在线安装：

```bash
curl -fsSL https://raw.githubusercontent.com/marvellouspizza/1688-Agent-Search/main/install.sh | sh
```

安装一次后，在任意目录输入 `as1688` 即可启动。

如果已经下载或克隆了项目，也可以在项目目录安装：

```bash
sh install.sh
```

安装器会：

1. 检查 Python 版本。
2. 把源码复制到用户目录后构建独立 ZipApp。
3. 安装运行包到 `~/.local/share/as1688/as1688.pyz`。
4. 创建全局命令 `~/.local/bin/as1688`。
5. 必要时把 `~/.local/bin` 加入终端 `PATH`。

安装路径不包含下载目录和用户名硬编码。安装完成后，原下载目录可以移动。

如果安装器提示更新了 `PATH`，重新打开一次终端即可。

卸载程序但保留配置和会话：

```bash
./uninstall.sh
```

## 首次启动

在任意目录运行：

```bash
as1688
```

如果还没有配置，会依次显示：

```text
选择供应商
→ 配置该供应商的登录或 API Key
→ 读取该供应商的模型目录
→ 选择默认模型
→ 进入聊天
```

## 常用命令

直接聊天：

```bash
as1688
```

选择或切换供应商，并为新供应商选择模型：

```bash
as1688 provider
as1688 provider --list
as1688 provider --status
as1688 provider --update-key
as1688 provider --delete-key
```

`--update-key` 会先用新 Key 读取模型目录，验证成功后才覆盖旧 Key。
如果 Key 来自 `OPENAI_API_KEY` 环境变量，请先修改或取消该环境变量。

选择当前供应商的模型：

```bash
as1688 model
as1688 model --list
as1688 model --status
```

单次提问或恢复 Session：

```bash
as1688 chat -q "你好"
as1688 sessions
as1688 chat --session session_xxx
```

会话内命令：

```text
/model
/model MODEL
/session
/stop
/help
/quit
```

模型生成时按 `Ctrl+C` 会中止请求，未完成回复不会保存为成功消息。
等待用户输入时按 `Ctrl+C` 会直接退出 CLI。

## 配置和会话

```text
~/.1688-agent-search/config.json       非秘密配置
~/.1688-agent-search/credentials.json  钥匙串不可用时的凭证文件
~/.1688-agent-search/sessions.db        SQLite 会话数据库
```

配置优先级保持为：CLI 参数 → 配置文件 → 环境变量 → Provider 默认值。
OpenAI API Key 也可以只通过 `OPENAI_API_KEY` 环境变量提供。

## 代码阅读顺序

1. `src/agent_search_1688/cli.py`：供应商、模型和终端交互。
2. `src/agent_search_1688/runtime.py`：统一 Agent 状态机。
3. `src/agent_search_1688/providers/`：Provider 解析和模型适配器。
   - `codex.py`：本机 Codex / ChatGPT 适配器。
   - `openai.py`：OpenAI Responses API 适配器。
5. `src/agent_search_1688/credentials.py`：API Key 安全存取。
6. `src/agent_search_1688/session_store.py`：SQLite Session 和事务。
7. `src/agent_search_1688/models.py`：统一消息与结果结构。
8. `src/agent_search_1688/prompt_builder.py`：三层上下文。
9. `src/agent_search_1688/tools/`：受控工具注册表、MCP Server 与网页搜索后端。

工具目录按能力分类：

```text
tools/
├── registry.py       工具注册与调度
├── mcp_server.py     Codex 使用的 stdio MCP 适配器
└── web/
    ├── search.py     web_search 工具定义
    └── searxng.py    SearXNG 后端客户端
```

稳定核心入口仍然是：

```python
PurchaseAgentRuntime.chat(user_input, session_id)
```

## 测试分支

为了让 `main` 分支保持精简，完整测试套件保存在
`archive/with-tests` 分支：

```bash
git switch archive/with-tests
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
