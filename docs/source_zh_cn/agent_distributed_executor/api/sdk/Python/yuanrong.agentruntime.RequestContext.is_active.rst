.. _request_context_is_active:

yuanrong.agentruntime.RequestContext.is_active
-------------------------------------------------

.. py:property:: RequestContext.is_active

    检查当前请求是否仍处于活跃状态。请求完成后（如 ``execute()`` 返回后）变为非活跃。

    返回：
        bool，True 表示当前请求仍处于活跃状态。
