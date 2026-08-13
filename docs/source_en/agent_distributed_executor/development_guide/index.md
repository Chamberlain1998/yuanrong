# Developing an Agent

Built on top of the function service capability, openYuanrong provides the agent-dx Python SDK to simplify Agent development. Agents developed with the SDK run as Serverless services.

The Agent function definition using the SDK is as follows.

- agent.py: The Agent module name must be `agent`.
- class Agent(AgentExecutor): The Agent class name must be `Agent`, which defines the Agent's behavior. You must implement the `init` and `execute` methods.
   - `async def init(self, session_context)`: Initialize the Agent. The parameter is the session context, and the return value is None.
   - `async def execute(self, ctx)`: Execute the Agent. The parameter is the context. Must explicitly return an Agent execution result of type `Complete` or `InputRequired`.

A complete Agent function example:

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

## Register an Agent

The developed Agent can be registered to the openYuanrong cluster using the [adx deploy](../api/adx_command_line_tool/adx_deploy.md) command. For configuration details, see [Register Function](../../multi_language_function_programming_interface/api/function_service/register_function.md).

The following fields are fixed configuration:

- `handler` field: Fixed to `yuanrong.agentruntime.bootstrap.handler`.
- `extendedHandler.initializer` field: Fixed to `yuanrong.agentruntime.bootstrap.initialize`.
- `kind` field: Fixed to `faas`.
- `enableSessionCtx` field: Fixed to `true`.

Other fields are configured according to actual requirements.

A reference configuration:

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

Before registering an Agent, ensure the deployed openYuanrong cluster supports function service, streaming response, and other configurations. A master node deployment example:

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

## Build the Deployment Package

The Agent deployment package must include both the Agent function code and the agent-dx SDK library. You can use `pip` to install the agent-dx SDK into the `codePath` directory specified during Agent registration.

```shell
pip install agent_dx_sdk --target /your/agent/code/path
```

The deployment package structure:

```text
/your/agent/code/path/
├── agent.py
├── yuanrong/
│   └── agentruntime/
│       └── ...
```

## Invoke an Agent

See the [adx exec](../api/adx_command_line_tool/adx_exec.md) command for how to invoke an Agent.

## Agent Logging

Like function services, logs printed by Agent methods to stdout are collected and stored by openYuanrong. Currently, logging output functions of the programming language are supported, such as `print`, `logging`, etc.
