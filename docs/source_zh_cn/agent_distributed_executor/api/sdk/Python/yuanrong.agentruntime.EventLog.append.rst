.. _event_log_append:

yuanrong.agentruntime.EventLog.append
-----------------------------------------

.. py:method:: EventLog.append(request_context: RequestContext, event_type: str, data: Any) -> Event
   :async:

    追加一条自定义事件到日志。仅在 ``execute()`` 期间且当前请求处于活跃状态时可用。

    事件类型不能为空，且不能使用平台保留前缀（``turn.``、``input.``、``output.``、``session.``、``runtime.``）。

    参数：
        - **request_context** (RequestContext) – 当前请求上下文。
        - **event_type** (str) – 事件类型，建议使用 ``agent.`` 前缀，例如 ``agent.message.observed``。
        - **data** (Any) – 事件数据。

    返回：
        新创建的 Event 实例。

    抛出：
        - **EventAppendNotActive** – 当前请求已不再活跃时抛出。
        - **ValueError** – 事件类型为空或使用了平台保留前缀时抛出。

    样例：
        >>> async def execute(self, ctx):
        >>>     await ctx.session_context.event_log.append(
        >>>         ctx,
        >>>         "agent.message.observed",
        >>>         {"message": ctx.input.message},
        >>>     )
