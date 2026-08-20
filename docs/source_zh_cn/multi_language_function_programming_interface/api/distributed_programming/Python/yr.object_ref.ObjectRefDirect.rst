yr.object_ref.ObjectRefDirect
================================

.. py:class:: yr.object_ref.ObjectRefDirect(object_id: str, task_id=None, exception=None)

   基类：:class:`yr.object_ref.ObjectRef`

   direct invoke 的本地 future 引用。结果通过 Libruntime 回调完成，不访问 DataSystem，也不执行 IncreaseRef/DecreaseRef。可传给 ``yr.get`` 和 ``yr.wait``。

   no-DS 模式不能把该引用作为新 invoke 的参数继续传递。direct invoke 的请求和响应分别受 100 MiB 聚合序列化限制。
