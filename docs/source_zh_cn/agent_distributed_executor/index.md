# Agent 分布式执行器

```{eval-rst}
.. toctree::
   :glob:
   :maxdepth: 1
   :hidden:

   development_guide/index.md
   api/index
```

Agent 分布式执行器（agent-dx）是面向 Agent 的分布式执行底座，提供 Agent 注册、调用、会话管理等开发者工具。Agent 以 Serverless 服务方式运行，通过 SSE 流式输出执行结果，支持一次性调用和交互式调用。

## 关键能力

- **Agent 开发 SDK**：提供 `AgentExecutor` 抽象基类，开发者只需实现 `init` 和 `execute` 两个方法即可定义 Agent 行为。
- **会话上下文**：通过 `SessionContext` 管理 Agent 的会话状态，支持跨 Turn 的事件日志读写。
- **流式输出**：通过 `OutputWriter` 在 `execute()` 中向客户端实时推送中间结果，实现流式响应。
- **事件日志**：通过 `EventLog` 持久化事件记录，支持跨 Turn 的状态恢复和事件溯源。
- **命令行工具**：`adx` CLI 提供 Agent 注册（`adx deploy`）和调用（`adx exec`）命令，支持交互模式下的会话管理。

## 入门

### 安装 SDK

使用 `pip` 安装 agent-dx SDK（包名为 `agent_dx_sdk`，安装后通过 `yuanrong.agentruntime` 导入）：

```bash
pip install agent_dx_sdk
```

### Agent 函数

使用 SDK 开发 Agent 函数时，模块名固定为 `agent`，类名固定为 `Agent`，需继承 `AgentExecutor` 并实现 `init` 和 `execute` 方法，一个简单的示例如下：

```python
from yuanrong.agentruntime import AgentExecutor, Complete, InputRequired

class Agent(AgentExecutor):
    # 在 SessionContext 绑定时调用一次，用于初始化 Agent 状态。
    async def init(self, session_context):
        self.events_at_init = await session_context.event_log.get()

    # 每次调用时执行，`ctx` 包含输入（`ctx.input`）、输出（`ctx.output`）、Turn ID 和会话上下文。
    # 返回 `Complete` 表示当前 Turn 结束，返回 `InputRequired` 表示需要额外输入。
    async def execute(self, ctx):
        message = ctx.input.message
        await ctx.output.write(
            {"kind": "progress", "turnId": ctx.turn_id}
        )
        if isinstance(message, dict) and message.get("message") == "confirm":
            return Complete({"status": "completed"})
        return InputRequired({"status": "input_required"})
```

## 下一步

- [开发 Agent](./development_guide/index.md)：了解 Agent 开发的完整流程，包括注册、构建部署包和调用。
- [Python SDK](./api/sdk/Python/agent_sdk.rst)：查看 Agent SDK 的完整 API 参考。
- [adx CLI](./api/adx_command_line_tool/index.md)：查看命令行工具的详细参数和用法。
