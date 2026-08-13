# exec

调用 agent（函数），并以 SSE 流式输出返回结果；未传 `--args` 时进入交互模式。

## 用法

```shell
adx exec [OPTIONS]
```

## 参数

* `--agent`：要调用的 agent，格式为 `0@namespace@funcname[:version]`；CLI 会校验格式，不合法会报错。
* `--server`：frontend 地址，格式为 `host:port`，例如 `127.0.0.1:31180`（默认 http，无需加 `http://` 前缀）；也可显式传入 `https://host:port` 使用 HTTPS。
* `--session-ctx`：agent 会话上下文，最长 63 个字符；传入才会带 `X-Session-Context` 请求头，交互模式会自动生成默认值。
* `--session-id`：实例会话 ID，最长 63 个字符；可选，不传则不发送 `X-Instance-Session` 请求头，交互模式未传入时自动生成。
* `--session-ttl`：实例会话 TTL，默认 90；交互模式默认 600 秒，命令行显式传入时需同时传入 `--session-id`。
* `--concurrency`：实例会话并发数，默认 1；命令行显式传入时需同时传入 `--session-id`。
* `--args`：handler 入参，JSON 字符串；不传则进入交互模式。

## 说明

* 只有 `--agent` 和 `--server` 必选，其余均可选。
* `--session-ttl` / `--concurrency` 必须配合 `--session-id` 使用；若只传了它们而没传 `--session-id`，`adx` 会报错并直接退出（退出码 2），**不发送任何请求**。
* 传入 `--args` 时执行一次性调用，请求体原样使用该 JSON 字符串。
* 未传 `--args` 时进入交互模式；每轮用户输入会通过标准 JSON 序列化自动包装为 `{"message":"用户输入"}` 后发起一次调用，特殊字符（引号、反斜杠、换行等）会被正确转义。
* 交互模式下若未传 `--session-ctx`，会自动生成一个会话上下文，并在每次调用中携带同一个 `X-Session-Context` 请求头；若已传入，则使用用户提供的值。
* 交互模式下若未传 `--session-id`，会自动生成一个 InstanceSession ID。同一 SessionCtx 的每次普通消息都会携带同一个 `X-Instance-Session`；未指定 `--session-ttl` 时使用 600 秒。
* 通过 `/sessions`、`/fork` 或 `/new` 切换 SessionCtx 后，CLI 会使用原 SessionCtx 的 InstanceSession ID 发送 `sessionTTL` 为 0 的释放调用，并为新 SessionCtx 生成新的 InstanceSession ID。
* 至少发起过一次普通消息后，`/quit` 或输入结束会额外发送一条 `sessionTTL` 为 0、body 为 `{}` 的调用，使该 InstanceSession 立即过期。释放调用失败时仅打印警告日志，不影响退出；服务端保证幂等和最终清理。
* 交互模式输入 `/quit` 退出。
* 返回结果为 SSE 流，`adx` 会边接收边持续输出，直到服务端发送结束标记。

## 交互 SessionCtx 管理

交互模式下可以使用以下命令管理当前 Agent 的 SessionCtx（Linux）：

* `/sessions`：列出当前 Agent 的 SessionCtx。Linux TTY 下可用上下方向键选择，Enter 切换，Esc 或 `q` 取消。
* `/history`：查询当前 SessionCtx 最近的 Turn 输入、输出和状态。
* `/fork <turn-id> <new-session-ctx-id>`：从已完成 Turn 创建指定的新 SessionCtx，成功后自动切换。
* `/delete <session-ctx-id>`：删除非当前 SessionCtx。删除当前会话前，需先通过 `/new` 或 `/sessions` 切换。
* `/new [session-ctx-id]`：仅切换 CLI 当前 SessionCtx；首条普通消息才会创建服务端会话。

Linux TTY 的交互输入支持斜杠命令补全：输入 `/` 或命令前缀会显示候选，使用上下方向键选择，按 `Tab` 或 `Enter` 将候选填入输入行；填入后再次按 `Enter` 执行。拼写接近但未前缀匹配的命令会显示最多三个相近候选。

`SessionCtx ID` 与 `Turn ID` 最长为 63 个字符。`/sessions` 默认显示当前 Agent 最近更新的前 50 个会话；非 TTY 环境仅打印列表，可使用 `/new <session-ctx-id>` 切换。

`/fork` 的目标 SessionCtx ID 必须显式提供，并且不能与当前 SessionCtx ID 相同。请求超时后重试时应继续使用相同的源 SessionCtx、Turn ID 和目标 SessionCtx ID。

## 样例

```shell
adx exec --agent <AGENT> --server 127.0.0.1:31180
```

```shell
adx exec --agent <AGENT> --server 127.0.0.1:31180 --args '{"message":"你好"}'
```

```shell
adx exec --agent <AGENT> --server 127.0.0.1:31180 \
        --session-ctx ctx1 --session-id id1 --session-ttl 90 --concurrency 1 \
        --args '{"param1":"你好"}'
```

```console
$ adx exec --agent 0@default@demo --server 127.0.0.1:31180 --session-ctx research-main
[research-main] > /history
[research-main] > /fork turn-0001 research-alt
[research-alt] > 忽略之前的结论,改为检查依赖安全问题
[research-alt] > /delete research-main
```
