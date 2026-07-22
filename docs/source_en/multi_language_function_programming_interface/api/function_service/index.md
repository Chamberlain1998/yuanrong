# Function Service API

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

This section introduces the Function Service API for developing, managing, and invoking Serverless service applications.

## Function Development SDK

- [Python SDK](./Python/python_sdk.rst)
- [C++ SDK](./Cpp/cpp_sdk.md)
- [Java SDK](./Java/java_sdk.md)

## Service Invocation REST API

- [Invoke Service](./function_invocation.md)
- [Subscribe to Stream Service](./stream_invocation.md)

## Session Management REST API

- [Interrupt Session](./session_interrupt.md)
- [Delete Session](./session_delete.md)

## Function Management REST API

- [Register Function](./register_function.md)
- [Update Function](./update_function.md)
- [Publish Function](./release_function.md)
- [Query Specific Version of Function](./get_function.md)
- [Query All Versions of Function](./get_function_by_name.md)
- [Query Function List](./get_function_by_version.md)
- [Create Reserved Instance Configuration](./create_reserve_instance.md)
- [Update Reserved Instance Configuration](./update_reserve_instance.md)
- [Query Reserved Instance Configuration](./get_reserve_instance.md)
- [Delete Reserved Instance Configuration](./delete_reserve_instance.md)

## Agent Instance Management

- [Create Agent Instance](./create_agent_instance.md)
- [Delete Agent Instance](./delete_agent_instance.md)
- [Agent Instance Protocol Invocation Channels](./agent_invoke_channels.md)
