.. _invoke_bypass_datasystem:

yr.InvokeOptions.bypass_datasystem
--------------------------------------

.. py:attribute:: InvokeOptions.bypass_datasystem
   :type: bool
   :value: False

   ``True`` 为本次调用显式开启 DataSystem bypass，参数和返回值通过 FunctionSystem 消息内联传输，返回 ``ObjectRefDirect``。``False`` 不会关闭当前进程已经生效的默认 bypass。

   请求和响应分别限制为 100 MiB 的聚合序列化大小。超过限制返回 ``ERR_PARAM_INVALID``，数据不会被截断。
