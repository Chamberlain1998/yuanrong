Python SDK
================

yuanrong.agentruntime.AgentExecutor
-------------------------------------

.. py:class:: yuanrong.agentruntime.AgentExecutor

    基类：``ABC``

    Agent 开发者必须继承的抽象基类，定义了 Agent 的生命周期方法。

    **方法**：

    .. list-table::
       :header-rows: 0
       :widths: 30 70

       * - :ref:`init <agent_init>`
         - 初始化 Agent，在 SessionContext 绑定时调用一次。
       * - :ref:`execute <agent_execute>`
         - 处理一次调用请求，返回 ``Complete`` 或 ``InputRequired``。

.. toctree::
    :maxdepth: 1
    :hidden:

    yuanrong.agentruntime.AgentExecutor.init
    yuanrong.agentruntime.AgentExecutor.execute

yuanrong.agentruntime.SessionContext
-------------------------------------

.. py:class:: yuanrong.agentruntime.SessionContext(session_context_id: str, event_log: EventLog)

    基类：``object``

    Agent 的会话上下文，在 ``init()`` 阶段传入，用于获取会话 ID 和事件日志。

    参数：
        - **session_context_id** – 会话上下文 ID。
        - **event_log** – EventLog 实例。

    **属性**：

    .. list-table::
       :header-rows: 0
       :widths: 30 70

       * - :ref:`id <session_context_id>`
         - 获取会话上下文 ID。
       * - :ref:`event_log <session_context_event_log>`
         - 获取 EventLog 实例。``get()`` 可在 ``init()`` 和 ``execute()`` 阶段使用；``append()`` 仅可在 ``execute()`` 的活跃请求期间调用。

.. toctree::
    :maxdepth: 1
    :hidden:

    yuanrong.agentruntime.SessionContext.id
    yuanrong.agentruntime.SessionContext.event_log

yuanrong.agentruntime.RequestContext
-------------------------------------

.. py:class:: yuanrong.agentruntime.RequestContext(session_context: SessionContext, turn_id: str, message: Any, output: OutputWriter)

    基类：``object``

    单次调用请求的上下文，在 ``execute()`` 阶段传入，包含输入、输出、Turn ID 等信息。

    构造函数接收原始 ``message``，框架内部自动将其包装为 ``RequestInput`` 对象，通过 ``input`` 属性访问。

    **属性**：

    .. list-table::
       :header-rows: 0
       :widths: 30 70

       * - :ref:`session_context <request_context_session_context>`
         - 获取当前请求所属的 SessionContext。
       * - :ref:`turn_id <request_context_turn_id>`
         - 获取当前 Turn 的 ID。
       * - :ref:`input <request_context_input>`
         - 获取请求输入，类型为 ``RequestInput``。
       * - :ref:`output <request_context_output>`
         - 获取输出写入器，类型为 ``OutputWriter``。
       * - :ref:`is_active <request_context_is_active>`
         - 检查当前请求是否仍处于活跃状态。

.. toctree::
    :maxdepth: 1
    :hidden:

    yuanrong.agentruntime.RequestContext.session_context
    yuanrong.agentruntime.RequestContext.turn_id
    yuanrong.agentruntime.RequestContext.input
    yuanrong.agentruntime.RequestContext.output
    yuanrong.agentruntime.RequestContext.is_active

yuanrong.agentruntime.RequestInput
-------------------------------------

.. py:class:: yuanrong.agentruntime.RequestInput(message: Any)

    基类：``object``

    请求输入的封装，为 ``frozen`` dataclass。

    参数：
        - **message** – 调用方传入的原始消息。

    **属性**：

    .. list-table::
       :header-rows: 0
       :widths: 30 70

       * - :ref:`message <request_input_message>`
         - 获取调用方传入的原始消息。

.. toctree::
    :maxdepth: 1
    :hidden:

    yuanrong.agentruntime.RequestInput.message

yuanrong.agentruntime.OutputWriter
-------------------------------------

.. py:class:: yuanrong.agentruntime.OutputWriter

    基类：``object``

    流式输出写入器，用于在 ``execute()`` 中向客户端发送中间结果。写入的数据会先持久化再发送到 SSE 流。

    **方法**：

    .. list-table::
       :header-rows: 0
       :widths: 30 70

       * - :ref:`write <output_writer_write>`
         - 向 SSE 流写入一条中间结果。

.. toctree::
    :maxdepth: 1
    :hidden:

    yuanrong.agentruntime.OutputWriter.write

yuanrong.agentruntime.EventLog
-------------------------------------

.. py:class:: yuanrong.agentruntime.EventLog

    基类：``object``

    事件日志的读写接口，用于在 ``init()`` 和 ``execute()`` 阶段读取历史事件或追加自定义事件。

    **方法**：

    .. list-table::
       :header-rows: 0
       :widths: 30 70

       * - :ref:`get <event_log_get>`
         - 获取事件日志列表。
       * - :ref:`append <event_log_append>`
         - 追加一条自定义事件到日志。

.. toctree::
    :maxdepth: 1
    :hidden:

    yuanrong.agentruntime.EventLog.get
    yuanrong.agentruntime.EventLog.append

yuanrong.agentruntime.Event
-------------------------------------

.. py:class:: yuanrong.agentruntime.Event

    基类：``object``

    事件日志中的单条事件记录，为 ``frozen`` dataclass。由框架内部创建（``EventLog.get()`` 返回、``EventLog.append()`` 返回），用户无需自行构造。

    **属性**：

    .. list-table::
       :header-rows: 0
       :widths: 30 70

       * - **session_context_id**
         - 会话上下文 ID。
       * - **turn_id**
         - Turn ID。
       * - **seq**
         - 事件序号。
       * - **event_id**
         - 事件唯一 ID。
       * - **source**
         - 事件来源，``"PLATFORM"`` 或 ``"SDK"``。
       * - **type**
         - 事件类型。
       * - **data**
         - 事件数据。
       * - **schema_version**
         - Schema 版本。
       * - **created_at**
         - 事件创建时间。

yuanrong.agentruntime.Complete
-------------------------------------

.. py:class:: yuanrong.agentruntime.Complete(value: Any)

    基类：``object``

    表示 Agent 执行完成，为 ``frozen`` dataclass。作为 ``execute()`` 的返回值，表示当前 Turn 已结束。

    参数：
        - **value** – Agent 返回的结果数据。

yuanrong.agentruntime.InputRequired
-------------------------------------

.. py:class:: yuanrong.agentruntime.InputRequired(value: Any)

    基类：``object``

    表示 Agent 需要额外输入才能继续，为 ``frozen`` dataclass。作为 ``execute()`` 的返回值，表示当前 Turn 暂停，等待用户输入。

    参数：
        - **value** – Agent 返回的中间结果数据。

yuanrong.agentruntime.ExecutionResult
-------------------------------------

.. py:data:: yuanrong.agentruntime.ExecutionResult

    类型别名，等价于 ``Union[Complete, InputRequired]``。``execute()`` 的返回类型标注为此类型。

yuanrong.agentruntime.AgentRuntimeError
-----------------------------------------

.. py:class:: yuanrong.agentruntime.AgentRuntimeError(code: str, message: str)

    基类：``RuntimeError``

    Agent Runtime 的基础错误类型，包含稳定的机器可读错误码。

    参数：
        - **code** – 错误码。
        - **message** – 错误信息。

    **子类**：

    .. list-table::
       :header-rows: 0
       :widths: 30 70

       * - **AgentRuntimeNotConfigured**
         - Agent Runtime 未配置初始化器。
       * - **SessionContextBindingMismatch**
         - 调用标识与 Agent Runtime 实例绑定的 SessionContext 不匹配。
       * - **AgentExecutorLoadFailed**
         - 加载 Agent 类失败。
       * - **AgentInitFailed**
         - Agent ``init()`` 执行失败。
       * - **InvalidExecutionResult**
         - ``execute()`` 返回值不是 ``Complete`` 或 ``InputRequired``。
       * - **OutputNotActive**
         - 当前请求已不再活跃时调用 ``OutputWriter.write()``。
       * - **EventAppendNotActive**
         - 当前请求已不再活跃时调用 ``EventLog.append()``。
       * - **EventSerializationFailed**
         - 事件序列化失败。
       * - **DataSystemError**
         - DataSystem 初始化失败。
