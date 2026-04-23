# Codex Imagen2 API

把 OpenAI 兼容的 `POST /v1/chat/completions` 图片请求转发到 Codex `/responses` 接口的轻量服务。

这个项目适合在本地已经登录 Codex 的前提下，复用 `~/.codex/auth.json` 来做图片生成或图片编辑，并对外暴露一个更容易接入的 HTTP API。

## 功能

- 支持文生图
- 支持通过 `image_url` 做图片编辑
- 使用本地 Codex 认证信息，无需额外手填 API Key
- 在收到 `401` 时自动尝试刷新 token
- 服务端会把生成结果保存到本地 `images/`
- 返回 OpenAI 风格响应，图片内容位于 `choices[0].message.content`

## 工作方式

服务接收 OpenAI 兼容格式的 `chat.completions` 请求后，会：

1. 从最后一条 `user` 消息里提取文本和图片
2. 把请求转换成 Codex `/responses` 所需的 `input` 和 `image_generation` tool 调用
3. 通过 SSE 读取上游返回
4. 提取生成的图片结果并保存到本地
5. 把图片重新包装成 Markdown 的 data URL 返回给调用方

返回内容类似：

```markdown
![image](data:image/png;base64,...)
```

## 环境要求

- Python 3.9+
- `uv`
- 本机存在有效的 `~/.codex/auth.json`

## 安装

如果你还没同步依赖：

```bash
uv sync
```

## 启动服务

```bash
uv run python server.py
```

可选参数：

```bash
uv run python server.py --host 0.0.0.0 --port 8000 --workers 1
```

如果想调高日志详细程度，可以临时设置：

```bash
CODEX_IMAGE_SERVER_LOG_LEVEL=DEBUG uv run python server.py --port 4000
```

默认监听地址：

- `host`: `0.0.0.0`
- `port`: `8000`
- `workers`: `1`

## 快速示例

运行内置示例：

```bash
uv run python example.py --mode all
```

可选模式：

```bash
uv run python example.py --mode text
uv run python example.py --mode edit
```

示例会调用本地服务：

- 文生图结果保存到 `example_outputs/text_to_image.png`
- 图片编辑结果保存到 `example_outputs/image_edit.png`

如果编辑示例所需的参考图不存在，程序会自动在 `images/reference_input.png` 生成一张测试图片。

## 接口说明

当前只提供一个接口：

- `POST /v1/chat/completions`

### 文生图请求示例

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-4o-image",
    "stream": false,
    "messages": [
      {
        "role": "user",
        "content": "Generate a polished illustrated poster of a small orange cat riding a bicycle through a rainy neon alley at dusk."
      }
    ]
  }'
```

### 图片编辑请求格式

当 `messages[].content` 是数组时，服务会读取：

- `{"type": "text", "text": "..."}`
- `{"type": "image_url", "image_url": {"url": "..."}}`

也就是说，你可以传入 OpenAI 常见的多模态消息格式：

```json
{
  "model": "gpt-4o-image",
  "stream": false,
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Turn this into a retro travel poster."
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,..."
          }
        }
      ]
    }
  ]
}
```

## 响应格式

响应结构保持为 OpenAI 风格，但 `content` 不是纯文本说明，而是内嵌图片的 Markdown：

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1710000000,
  "model": "gpt-4o-image",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "![image](data:image/png;base64,...)"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

如果你想把响应中的图片落盘，可以参考 `example.py` 里的 `extract_image_bytes()` 和 `save_output_image()`。

## 认证机制

程序启动后不会主动登录，而是直接使用本机已有的 Codex 认证状态：

- 首次使用时，如果 `authens/*.json` 不存在，会从 `~/.codex/auth.json` 复制一份到 `authens/auth_state.json`
- 实际请求时会通过 glob 发现 `authens/` 下所有 `.json` 文件，并按 round-robin 方式选择认证文件
- 每次图片请求最多会轮转重试 `3` 次认证文件
- 如果上游返回 `401`，会尝试用 `refresh_token` 刷新 access token
- 刷新后的 token 会回写到当前使用的认证文件

这意味着：

- `~/.codex/auth.json` 需要先存在
- `authens/*.json` 是这个项目自己的本地认证副本集合

## 配置

代码会读取 `~/.codex/config.toml` 中的部分字段。

当前支持：

```toml
model = "gpt-5.4"
base_url = "https://api.openai.com/v1"
```

说明：

- `model` 未配置时默认使用 `gpt-5.4`
- `base_url` 未配置时，会根据认证模式自动选择默认地址
- 如果 `auth_mode` 是 `chatgpt`、`chatgpt_auth_tokens` 或 `agent_identity`，默认走 `https://chatgpt.com/backend-api/codex`
- 否则默认走 `https://api.openai.com/v1`

模型映射规则：

- 请求里的 `model` 字段不会参与上游模型选择
- 服务始终使用 `~/.codex/config.toml` 里的 `model`，未配置时默认使用 `gpt-5.4`
- 响应里的 `model` 会原样回显请求值，用于兼容 OpenAI 风格客户端

## 目录说明

```text
.
├── api.py              # 上游请求构造与 SSE 解析
├── auth.py             # 认证文件加载与 token 刷新
├── config.py           # 路径、默认值、常量
├── example.py          # 本地示例客户端
├── router.py           # OpenAI 兼容路由
├── server.py           # FastAPI 入口
├── utils.py            # 图片与配置辅助函数
├── authens/            # 项目内认证副本
├── images/             # 服务端生成图片输出目录
└── example_outputs/    # 示例脚本输出目录
```

## 当前限制

- 不支持流式返回，`stream: true` 会直接报错
- 只会从最后一条 `user` 消息中提取输入
- 响应里的 `usage` 目前固定为 `0`
- 返回结果是 Markdown data URL，不是公网可访问的图片链接
- 当前接口主要面向图片生成场景，不是通用聊天代理

## 常见问题

### 1. 返回 `No user prompt found`

说明请求里没有可解析的用户输入。请确认：

- `messages` 里存在 `role: "user"` 的消息
- 文本消息不是空字符串
- 多模态消息里至少包含文本或 `image_url`

### 2. 返回 `stream is not supported`

这个服务当前只支持：

```json
{ "stream": false }
```

### 3. 返回认证或刷新失败

请先检查：

- `~/.codex/auth.json` 是否存在
- 里面是否包含 `tokens.access_token`
- 如果需要自动刷新，是否包含 `tokens.refresh_token`

## 开发说明

项目使用 `uv` 管理依赖，核心依赖包括：

- `fastapi`
- `httpx`
- `pydantic`
- `uvicorn`

如果你改了依赖，可以重新锁定：

```bash
uv add <package>
uv sync
```
