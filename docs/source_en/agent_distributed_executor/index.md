# Agent Distributed Executor

```{eval-rst}
.. toctree::
   :glob:
   :maxdepth: 1
   :hidden:

   development_guide/index.md
   api/index
```

Agent Distributed Executor (agent-dx) is a distributed execution substrate for agents, providing developer tools for agent registration, invocation, and session management. Agents run as Serverless services, stream execution results via SSE, and support both one-shot and interactive invocation.

## Key Capabilities

- **Agent Development SDK**: Provides the `AgentExecutor` abstract base class — developers define agent behavior by implementing just `init` and `execute`.
- **Session Context**: Manages agent session state via `SessionContext`, with cross-Turn event log read/write support.
- **Streaming Output**: Pushes intermediate results to clients in real time via `OutputWriter` during `execute()`.
- **Event Log**: Persists event records via `EventLog`, supporting cross-Turn state recovery and event sourcing.
- **Command-Line Tool**: The `adx` CLI provides agent registration (`adx deploy`) and invocation (`adx exec`) commands, with session management in interactive mode.

## Getting Started

### Install the SDK

Install the agent-dx SDK using `pip` (package name is `agent_dx_sdk`, import as `yuanrong.agentruntime` after installation):

```bash
pip install agent_dx_sdk
```

### Agent Function

When developing an Agent function with the SDK, the module name must be `agent`, the class name must be `Agent`, and it must inherit `AgentExecutor` and implement the `init` and `execute` methods. A simple example is as follows:

```python
from yuanrong.agentruntime import AgentExecutor, Complete, InputRequired

class Agent(AgentExecutor):
    # Called once when a SessionContext is bound, used to initialize Agent state.
    async def init(self, session_context):
        self.events_at_init = await session_context.event_log.get()

    # Called on each invocation. `ctx` contains input (`ctx.input`), output (`ctx.output`), Turn ID, and session context.
    # Returns `Complete` to end the current Turn, or `InputRequired` to request additional input.
    async def execute(self, ctx):
        message = ctx.input.message
        await ctx.output.write(
            {"kind": "progress", "turnId": ctx.turn_id}
        )
        if isinstance(message, dict) and message.get("message") == "confirm":
            return Complete({"status": "completed"})
        return InputRequired({"status": "input_required"})
```

## Next Steps

- [Develop an Agent](./development_guide/index.md): Learn the complete workflow for Agent development, including registration, deployment package building, and invocation.
- [Python SDK](./api/sdk/Python/agent_sdk.rst): View the complete Agent SDK API reference.
- [adx CLI](./api/adx_command_line_tool/index.md): View detailed parameters and usage of the command-line tool.
