# deploy

通过 meta_service 注册一个 agent（函数）到 openYuanrong 集群。

## 用法

```shell
adx deploy [OPTIONS]
```

## 参数

* `-s, --spec`：函数定义，可以是一段 inline JSON 字符串，也可以是 JSON 文件路径（自动识别）；非法 JSON 或文件不存在均会报错。
* `--server`：meta_service 地址，格式为 `host:port`，例如 `127.0.0.1:31182`（默认 http，无需加 `http://` 前缀）；也可显式传入 `https://host:port` 使用 HTTPS；格式不合法会报错。

## 说明

* 支持通过全局参数 `--jwt-token` 或环境变量 `YR_JWT_TOKEN` 提供 JWT 鉴权令牌，通过 `X-Auth` 请求头发送。建议优先使用环境变量，避免令牌泄露到进程列表或 shell 历史记录中。
* `-s/--spec` 会做格式校验：若值是已存在的文件则按文件读取并解析 JSON；否则按 inline JSON 解析（即文件路径优先）。两者都不满足（既非合法 JSON，也不是存在的文件路径）时报错退出（退出码 2）。
* `--server` 必须是 `host:port` 形式（缺端口或端口非法会报错）。默认使用 HTTP；若需 HTTPS，可显式传入 `https://host:port`。
* 函数定义中若未设置 `enableSessionCtx` 字段，会自动注入默认值 `true`；若已显式设置（`true` 或 `false`），则以用户设置为准。
* 注册成功后会打印公开 agent 名称，格式为 `0@namespace@funcname`，可直接用于 `adx exec --agent`。

## 退出码

| 退出码 | 含义 |
| ------ | ---- |
| `0` | 成功 |
| `1` | 服务端失败（HTTP 非 2xx，或响应 `code != 0`） |
| `2` | 参数错误（JSON 非法、文件不存在、`--server` 格式错误、缺少必选参数） |
| `3` | 网络错误（连不上、超时） |

## 样例

```shell
adx deploy -s ./agent.json --server 127.0.0.1:31182
```

```shell
adx deploy -s '{"name":"0@faaspy@demo","runtime":"python3.11","handler":"demo.handler"}' \
      --server 127.0.0.1:31182
```
