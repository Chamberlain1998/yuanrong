# CLI - the adx command

`adx` 是 Agent Distributed Executor（agent-dx）的命令行工具。Agent 本质上就是 openYuanrong 的函数，`adx` 把函数的注册、调用封装成对底层 openYuanrong 函数服务接口的调用。

安装 `adx` 命令行工具：

```bash
pip install agent_dx_cli
```

## 命令

| 命令 | 说明 |
| ---- | ---- |
| [`deploy`](./adx_deploy.md) | 通过 meta service 组件注册一个 agent（函数）。 |
| [`exec`](./adx_exec.md) | 调用 agent（函数），并以 SSE 流式输出返回结果；未传 `--args` 时进入交互模式。 |

## 全局选项

| 选项 | 说明 |
| ---- | ---- |
| `--jwt-token` | JWT 鉴权令牌；也可通过环境变量 `YR_JWT_TOKEN` 提供，会通过 `X-Auth` 请求头发送。建议优先使用环境变量，避免令牌泄露到进程列表或 shell 历史记录中。 |
| `-v, --verbose` | 开启 DEBUG 日志，会在请求发送前打印请求详情（method、url、headers、body）。`X-Auth` 等敏感请求头的值会被替换为 `<redacted>`，避免凭据泄露。 |
| `--version` | 输出 `adx` 版本并退出。 |
| `-h, --help` | 查看帮助信息。 |

```{eval-rst}
.. toctree::
  :hidden:

  adx_deploy
  adx_exec
```

## 退出码

| 退出码 | 含义 |
| ------ | ---- |
| `0` | 成功 |
| `1` | 服务端失败（HTTP 非 2xx，或响应 `code != 0`） |
| `2` | 参数错误（JSON 非法、文件不存在、缺少必选参数） |
| `3` | 网络错误（连不上、超时） |
