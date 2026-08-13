.. _agent_init:

yuanrong.agentruntime.AgentExecutor.init
-----------------------------------------

.. py:method:: AgentExecutor.init(session_context: SessionContext) -> None
   :async:

    初始化或恢复 Agent 的业务状态。在 Agent Runtime 绑定 SessionContext 时调用一次，后续相同 SessionContext 的调用不会重复触发。

    参数：
        - **session_context** (SessionContext) – Agent 的会话上下文，包含会话 ID 和事件日志。

    返回：
        None。
