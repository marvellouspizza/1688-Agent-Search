# 1688 Agent Search

面向 1688 智能采购项目的终端 Agent。当前版本先打通普通文字对话：

```text
启动 as1688
→ 读取非秘密配置
→ 选择或解析模型供应商
→ 读取供应商凭证
→ 获取并选择模型
→ 创建 Agent 与 Session
→ 组装上下文
→ 转换为供应商 API 请求
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
3. `src/agent_search_1688/provider.py`：Provider 解析和 Codex 适配器。
4. `src/agent_search_1688/openai_provider.py`：OpenAI Responses API 适配器。
5. `src/agent_search_1688/credentials.py`：API Key 安全存取。
6. `src/agent_search_1688/session_store.py`：SQLite Session 和事务。
7. `src/agent_search_1688/models.py`：统一消息与结果结构。
8. `src/agent_search_1688/prompt_builder.py`：三层上下文。

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
