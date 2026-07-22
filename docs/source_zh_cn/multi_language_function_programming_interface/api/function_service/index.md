# 函数服务 API

```{eval-rst}
.. toctree::
   :glob:
   :maxdepth: 1
   :hidden:
   
   Python/python_sdk
   Cpp/cpp_sdk
   Java/java_sdk
   function_invocation
   stream_invocation
   session_interrupt
   session_delete
   register_function

   update_function
   release_function
   get_function
   get_function_by_name
   get_function_by_version
   delete_function
   create_reserve_instance
   update_reserve_instance
   get_reserve_instance
   delete_reserve_instance

   create_agent_instance
   delete_agent_instance
   agent_invoke_channels
```

本节向您介绍函数服务 API，用于开发、管理 Serverless 服务类应用。

## 函数开发 SDK

- [Python SDK](./Python/python_sdk.rst)
- [C++ SDK](./Cpp/cpp_sdk.rst)
- [Java SDK](./Java/java_sdk.md)

## 服务调用 REST API

- [调用服务](./function_invocation.md)
- [订阅流服务](./stream_invocation.md)

## 会话管理 REST API

- [中断会话](./session_interrupt.md)
- [删除会话](./session_delete.md)

## 函数管理 REST API

- [注册函数](./register_function.md)
- [更新函数](./update_function.md)
- [发布函数](./release_function.md)
- [查询函数的指定版本](./get_function.md)
- [查询函数的所有版本](./get_function_by_name.md)
- [查询函数列表](./get_function_by_version.md)
- [创建预留实例配置](./create_reserve_instance.md)
- [更新预留实例配置](./update_reserve_instance.md)
- [查询预留实例配置](./get_reserve_instance.md)
- [删除预留实例配置](./delete_reserve_instance.md)

## Agent 实例管理

- [创建 Agent 实例](./create_agent_instance.md)
- [删除 Agent 实例](./delete_agent_instance.md)
- [Agent 实例协议调用通道](./agent_invoke_channels.md)
