.. _output_writer_write:

yuanrong.agentruntime.OutputWriter.write
---------------------------------------------

.. py:method:: OutputWriter.write(value: Any) -> None
   :async:

    向 SSE 流写入一条中间结果。写入的数据会先持久化到事件日志再发送到 SSE 流，确保不丢失。仅在 ``execute()`` 期间且当前请求处于活跃状态时可用。

    参数：
        - **value** (Any) – 要写入的数据，会被序列化后发送。

    抛出：
        - **OutputNotActive** – 当前请求已不再活跃时抛出。

    样例：
        >>> async def execute(self, ctx):
        >>>     await ctx.output.write({"kind": "progress", "turnId": ctx.turn_id})
        >>>     return Complete({"status": "completed"})
