.. _config_bypass_datasystem:

yr.Config.bypass_datasystem
------------------------------------

.. py:attribute:: config.bypass_datasystem
   :type: bool | None
   :value: None

   当前进程的默认 invoke 传输策略。``None`` 使用集群探测值；``True`` 使用内联传输；``False`` 在 DataSystem 可用时使用普通传输。该属性不决定 DataSystem API 是否可用。no-DS 集群设置 ``False`` 会导致 ``yr.init`` 拒绝非法配置。

   bypass 调用的请求和响应分别限制为 100 MiB 的聚合序列化大小，超过限制返回 ``ERR_PARAM_INVALID``。
