# 开发 Agent

基于函数服务能力，openYuanrong 提供了 agent-dx Python SDK 简化 Agent 开发，开发的 Agent 以 Serverless 服务方式运行。

使用 SDK 开发的 Agent 函数定义如下。

- agent.py：Agent 模块名固定为 `agent`。
- class Agent(AgentExecutor)：Agent 类名固定为 `Agent`，定义了 Agent 的行为。必须实现 `init` 和 `execute` 方法。
   - `async def init(self, session_context)`：初始化 Agent，参数为会话上下文，返回值为 None。
   - `async def execute(self, ctx)`：执行 Agent，参数为上下文，必须显式返回 Agent 执行结果，类型为 `Complete` 或 `InputRequired`。

一个完整的 Agent 函数示例如下：

```python
from yuanrong.agentruntime import AgentExecutor, Complete, InputRequired

class Agent(AgentExecutor):
    async def init(self, session_context):
        self.events_at_init = await session_context.event_log.get()

    async def execute(self, ctx):
        events_before = await ctx.session_context.event_log.get()
        message = ctx.input.message
        print(message)
        await ctx.output.write(
            {
                "kind": "progress",
                "turnId": ctx.turn_id,
                "sessionContextId": ctx.session_context.id,
            }
        )
        await ctx.session_context.event_log.append(
            ctx,
            "agent.message.observed",
            {"message": message},
        )

        if message.get("message") == "confirm":
            return Complete(
                {
                    "status": "completed",
                    "turnId": ctx.turn_id,
                    "eventsBefore": len(events_before),
                }
            )
        return InputRequired(
            {
                "status": "input_required",
                "turnId": ctx.turn_id,
                "eventsBefore": len(events_before),
            }
        )
```

## 注册 Agent

开发的 Agent 可以通过 [adx deploy](../api/adx_command_line_tool/adx_deploy.md) 命令行工具注册到 openYuanrong 集群。注册配置项详见[注册函数](../../multi_language_function_programming_interface/api/function_service/register_function.md) API 中的描述。

其中以下字段为固定配置：

- `handler` 字段：固定为 `yuanrong.agentruntime.bootstrap.handler`。
- `extendedHandler.initializer` 字段：固定为 `yuanrong.agentruntime.bootstrap.initialize`。
- `kind` 字段：固定为 `faas`。
- `enableSessionCtx` 字段：固定为 `true`。
  
其他字段根据实际情况配置。

一个参考配置如下：

```json
{
  "name": "0@agent@demo",
  "runtime": "python3.11",
  "handler": "yuanrong.agentruntime.bootstrap.handler",
  "kind": "faas",
  "cpu": 600,
  "memory": 512,
  "timeout": 120,
  "extendedHandler": {
    "initializer": "yuanrong.agentruntime.bootstrap.initialize"
  },
  "extendedTimeout": {
    "initializer": 60
  },
  "minInstance": "0",
  "maxInstance": "1",
  "concurrentNum": "1",
  "storageType": "local",
  "codePath": "/your/agent/code/path",
  "enableSessionCtx": true
}
```

注册 Agent 前确保部署的 openYuanrong 集群支持函数服务、流式返回等配置，主节点部署示例如下：

```shell
yr start --master \
-s 'mode.master.frontend=true' \
-s 'mode.master.function_scheduler=true' \
-s 'mode.master.meta_service=true' \
-s 'frontend.args.enableEvent=true' \
-s 'values.lite_scheduler.enable=true' \
-s 'values.lite_scheduler.enable_all_tenants=true' \
-s 'values.lite_scheduler.acquire_wait_timeout_ms=10000'
```

## 构建部署包

Agent 部署包既要包含 Agent 函数代码，也要包含 agent-dx SDK 库。您可以使用 `pip` 安装 agent-dx SDK 到注册 Agent 时指定的 `codePath` 目录。

```shell
pip install agent_dx_sdk --target /your/agent/code/path
```

部署包结构如下。

```text
/your/agent/code/path/
├── agent.py
├── yuanrong/
│   └── agentruntime/
│       └── ...
```

## 调用 Agent

参考 [adx exec](../api/adx_command_line_tool/adx_exec.md) 命令了解如何调用 Agent。

## Agent 日志

和函数服务一样，Agent 函数的各方法往标准输出 stdout 打印的日志会被 openYuanrong 收集存储，当前支持使用编程语言的日志输出函数打印日志，例如 `print`、`logging` 等。
