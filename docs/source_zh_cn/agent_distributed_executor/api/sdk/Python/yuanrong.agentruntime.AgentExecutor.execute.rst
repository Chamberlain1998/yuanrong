.. _agent_execute:

yuanrong.agentruntime.AgentExecutor.execute
---------------------------------------------

.. py:method:: AgentExecutor.execute(request_context: RequestContext) -> ExecutionResult
   :async:

    处理一次调用请求。Agent Runtime 在每次收到调用时调用此方法。

    参数：
        - **request_context** (RequestContext) – 当前请求的上下文，包含输入、输出、Turn ID 和 SessionContext。

    返回：
        ExecutionResult，必须为 ``Complete`` 或 ``InputRequired``。返回 ``Complete`` 表示当前 Turn 结束；返回 ``InputRequired`` 表示需要额外输入才能继续。

    样例：
        >>> async def execute(self, ctx):
        >>>     message = ctx.input.message
        >>>     if message.get("action") == "confirm":
        >>>         return Complete({"status": "completed"})
        >>>     return InputRequired({"status": "input_required"})
