.. _event_log_get:

yuanrong.agentruntime.EventLog.get
--------------------------------------

.. py:method:: EventLog.get(*, after_seq: int = 0, limit: int | None = None) -> List[Event]
   :async:

    获取事件日志列表。可在 ``init()`` 和 ``execute()`` 中调用。

    参数：
        - **after_seq** (int) – 获取序号大于此值的事件，默认为 0（从最早开始）。必须为非负整数。
        - **limit** (int | None) – 最多返回的事件数量，None 表示不限制。必须为非负整数。

    返回：
        事件列表，按序号升序排列。

    样例：
        >>> async def init(self, session_context):
        >>>     events = await session_context.event_log.get()
        >>>     for e in events:
        >>>         print(e.type, e.data)
