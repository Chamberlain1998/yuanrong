# Getting Started

openYuanrong Serverless Distributed Computing Engine, 
It features a unified distributed kernel that powers a full spectrum of distributed workloads including Agents, reinforcement learning (RL), and model inference, laying an enterprise-grade distributed infrastructure foundation for the Agent era. It provides multi-language function programming interfaces to simplify distributed application development with a single-machine programming experience, and offers distributed dynamic scheduling and data sharing capabilities to achieve high-performance execution of distributed applications and efficient cluster resource utilization.

<img src="../images/introduction_en.png" width="50%">

openYuanrong consists of multi-language function runtime, function system, and data system, supporting flexible independent or combined usage on demand.

- **Multi-language Function Runtime**: Provides distributed function programming, supports Python, Java, and C++ languages, achieving high-performance distributed execution similar to single-machine programming.
- **Function System**: Provides large-scale distributed dynamic scheduling, supports rapid elastic scaling and cross-node migration of function instances, achieving efficient utilization of cluster resources.
- **Data System**: Provides heterogeneous distributed multi-level caching, supports Object and Stream semantics, achieving high-performance data sharing and transmission between function instances.

**Function** is the core concept abstraction of openYuanrong and is the basic unit of distributed scheduling and execution. Compared with traditional Serverless function concepts, openYuanrong functions are more general, supporting dynamic creation during execution, long-running, asynchronous calls between each other, stateful, etc., and can express running instances of arbitrary distributed applications, playing a role similar to processes in single-machine OS. [Learn more about openYuanrong's conceptual abstractions](multi_language_function_programming_interface/key_concept.md).

## Choose Your Getting Started Guide

Choose the getting started guide on demand and start using openYuanrong.

- Develop distributed applications: [Multi-language Function Programming Interface Quick Start](quickstart-multilingual-functional-programming-interface)
- Installation and deployment: [Installation and Deployment Quick Start](quickstart-deployment)
- Debug and monitor applications: [Observability Quick Start](quickstart-observability)

(quickstart-multilingual-functional-programming-interface)=

## Multi-language Function Programming Interface Quick Start

Using the multi-language function programming interface, only a few lines of code are needed to convert conventional Python, C++, and Java functions and classes into openYuanrong [stateless functions](key-concept-stateless-function) and [stateful functions](key-concept-statefull-function), easily developing distributed job applications. Or directly develop Serverless service applications with function granularity.

### Develop Distributed Jobs

Functions developed using Python, C++, and Java in single-machine programs can be converted into openYuanrong stateless functions for distributed parallel execution, and classes can be converted into openYuanrong stateful functions for distributed stateful computation, thus making it as easy to develop distributed jobs as developing single-machine programs.

::::::{dropdown} Parallelize Python, C++, Java Functions Using openYuanrong Stateless Functions
:color: secondary
:chevron: down-up
:icon: chevron-down

:::{Note}

To run the following examples, you need to install openYuanrong first, with the following environment requirements:

- Install openYuanrong and run Python examples: `python<=3.11,>=3.9`.
- Run Java examples: `java 8/17/21`.
- Run C++ examples: `gcc>=10.3.0 and stdc++>=14`.

```bash
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong-0.10.0-py3-none-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_runtime-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_datasystem-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_functionsystem-0.10.0-py3-none-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_cpp_sdk-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
```

:::

:::::{tab-set}
::::{tab-item} Python

Import the openYuanrong SDK and call `yr.init()` to initialize. Use `yr.invoke` to decorate a function, declaring that the function can run on the remote. When calling, use `.invoke()` to trigger remote execution, the return result is a reference to a data object, and you need to use `yr.get()` to get the value. At the end of the program, use `yr.finalize()` to clean up the runtime context.

```python
# example.py
import yr

# Define stateless function
@yr.invoke
def say_hello(name):
    return 'hello, ' + name


# Init once
yr.init()

# Parallel asynchronous call of stateless functions
results_ref = [say_hello.invoke('yuanrong') for i in range(3)]
print(yr.get(results_ref))

# Release environment resources
yr.finalize()
```

Run the program.

```bash
python example.py
# ['hello, yuanrong', 'hello, yuanrong', 'hello, yuanrong']
```

::::
::::{tab-item} C++

Include the header file `yr/yr.h` and call `YR::Init()` to initialize. Use `YR_INVOKE()` macro to declare that the function can run on the remote. When calling, use `YR::Function().Invoke()` to trigger remote execution, the return result is a reference to a data object, and you need to use `YR::Get()` to get the value. At the end of the program, use `YR::Finalize()` to clean up the runtime context.

```cpp
#include <iostream>
#include "yr/yr.h"

// Define stateless function
std::string SayHello(std::string name)
{
    return "hello, " + name;
}
YR_INVOKE(SayHello)


int main(int argc, char *argv[])
{
    // Init once
    YR::Init(YR::Config{}, argc, argv);

    // Parallel asynchronous call of stateless functions
    std::vector<YR::ObjectRef<std::string>> results_ref;
    for (int i = 0; i < 3; i++) {
        auto result_ref = YR::Function(SayHello).Invoke(std::string("yuanrong"));
        results_ref.emplace_back(result_ref);
    }

    for (auto result : YR::Get(results_ref)) {
        std::cout << *result << std::endl;
    }

    // Release environment resources
    YR::Finalize();
    return 0;
}
```

Refer to [Stateless Function Example Project](example-project-stateless-function) to run this program.

::::
::::{tab-item} java

Call `YR.init()` to initialize openYuanrong. Use `YR.function().invoke()` to trigger Java static methods to run on the remote, the return result is a reference to a data object, and you need to use `YR.get()` to get the value. At the end of the program, use `YR::Finalize()` to clean up the runtime context.

```java
// Greeter.java
package org.yuanrong.example;

public class Greeter {
    // Define stateless function
    public static String sayHello(String name) {
        return "hello, " + name;
    }
}
```

```java
// Main.java
package org.yuanrong.example;

import org.yuanrong.Config;
import org.yuanrong.api.YR;
import org.yuanrong.runtime.client.ObjectRef;
import org.yuanrong.exception.YRException;

import java.util.HashMap;
import java.util.ArrayList;
import java.util.List;

public class Main {
    public static void main(String[] args) throws YRException {
        // Init once
        YR.init(new Config());

        // Parallel asynchronous call of stateless functions
        List<ObjectRef> resultsRef = new ArrayList<>();
        for (int i = 0; i < 3; i++) {
            ObjectRef resultRef = YR.function(Greeter::sayHello).invoke("yuanrong");
            resultsRef.add(resultRef);
        }

        for (ObjectRef resultRef : resultsRef) {
            System.out.println(YR.get(resultRef, 30));
        }

        // Parallel asynchronous call of stateless functions
        YR.Finalize();
    }
}
```

Refer to [Stateless Function Example Project](example-project-stateless-function) to run this program.

::::
:::::

::::::

Stateless functions are suitable for handling applications that do not need to maintain state. For applications that need to maintain state, you can use openYuanrong stateful functions.

::::::{dropdown} Parallelize Python, C++, Java Classes Using openYuanrong Stateful Functions
:color: secondary
:chevron: down-up
:icon: chevron-down

openYuanrong provides stateful functions that allow you to parallelize class instances. When you instantiate a stateful function, openYuanrong starts a remote instance of the class in the cluster. Then the stateful function can perform remote method calls and maintain its own internal state.

:::{Note}

To run the following examples, you need to install openYuanrong first, with the following environment requirements:

- Install openYuanrong and run Python examples: `python<=3.11,>=3.9`.
- Run Java examples: `java 8/17/21`.
- Run C++ examples: `gcc>=10.3.0 and stdc++>=14`.

```bash
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong-0.10.0-py3-none-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_runtime-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_datasystem-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_functionsystem-0.10.0-py3-none-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_cpp_sdk-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
```

:::

:::::{tab-set}
::::{tab-item} Python

```python
# example.py
import yr

# Define stateful function
@yr.instance
class Object:
    def __init__(self):
        self.value = 0

    def save(self, value):
        self.value = value

    def get(self):
        return self.value


# Init once
yr.init()

# Create three stateful function instances
objs = [Object.invoke() for i in range(3)]

# Parallel asynchronous call of stateful functions
[obj.save.invoke(9) for obj in objs]
results_ref = [obj.get.invoke() for obj in objs]
print(yr.get(results_ref))

# Destroy stateful function instances
[obj.terminate() for obj in objs]

# Release environment resources
yr.finalize()
```

Run the program, output as follows.

```bash
python example.py
# [9, 9, 9]
```

::::
::::{tab-item} C++

```cpp
#include <iostream>
#include "yr/yr.h"

// Define stateful function
class Object {
public:
    Object() {}
    Object(int value) { this->value = value; }

    static Object *FactoryCreate(int value) {
        return new Object(value);
    }

    void Save(int value) {
        this->value = value;
    }

    int Get() {
        return value;
    }

    YR_STATE(value);

private:
    int value;
};
YR_INVOKE(Object::FactoryCreate, &Object::Save, &Object::Get);


int main(int argc, char *argv[])
{
    // Init once
    YR::Init(YR::Config{}, argc, argv);

    // Parallel asynchronous call of stateful functions
    std::vector<YR::NamedInstance<Object>> objects;
    for (int i = 0; i < 3; i++) {
        auto obj = YR::Instance(Object::FactoryCreate).Invoke(0);
        objects.emplace_back(obj);
    }

    // Parallel asynchronous call of stateful functions
    std::vector<YR::ObjectRef<int>> results_ref;
    for (auto obj : objects) {
        obj.Function(&Object::Save).Invoke(9);
        auto result_ref = obj.Function(&Object::Get).Invoke();
        results_ref.emplace_back(result_ref);
    }

    for (auto result : YR::Get(results_ref)) {
        std::cout << *result << std::endl;
    }

    // Destroy stateful function instances
    for (auto obj : objects) {
        obj.Terminate();
    }

    // Release environment resources
    YR::Finalize();
    return 0;
}
```

Refer to [Stateful Function Example Project](example-project-stateful-function) to run this program.

::::
::::{tab-item} java

```java
// Object.java
package org.yuanrong.example;

// Define stateful function
public class Object {
    private int value = 0;

    public Object(int value) {
        this.value = value;
    }

    public void save(int value) {
        this.value = value;
    }

    public int get() {
        return this.value;
    }
}
```

```java
// Main.java
package org.yuanrong.example;

import org.yuanrong.Config;
import org.yuanrong.api.YR;
import org.yuanrong.runtime.client.ObjectRef;
import org.yuanrong.call.InstanceHandler;
import org.yuanrong.exception.YRException;

import java.util.HashMap;
import java.util.ArrayList;
import java.util.List;

public class Main {
    public static void main(String[] args) throws YRException {
        // Init once
        YR.init(new Config());

        // Create three stateful function instances
        List<InstanceHandler> objects = new ArrayList<>();
        for (int i = 0; i < 3; i++) {
            InstanceHandler obj = YR.instance(Object::new).invoke(0);
            objects.add(obj);
        }

        // Parallel asynchronous call of stateful functions
        List<ObjectRef> resultsRef = new ArrayList<>();
        for (InstanceHandler obj : objects) {
            obj.function(Object::save).invoke(9);
            ObjectRef resultRef = obj.function(Object::get).invoke();
            resultsRef.add(resultRef);
        }

        for (ObjectRef resultRef : resultsRef) {
            System.out.println(YR.get(resultRef, 30));
        }

        // Destroy stateful function instances
        for (InstanceHandler obj : objects) {
            obj.terminate();
        }

        // Release environment resources
        YR.Finalize();
    }
}
```

Refer to [Stateful Function Example Project](example-project-stateful-function) to run this program.

::::
:::::
::::::

### Develop Serverless Services

openYuanrong provides function service capabilities, supporting hosting functions in the cluster, running as a service, and triggering calls through HTTP requests. Function services define function signatures in different development languages as request entry points, and implementing the function allows deployment as a Serverless service.

::::::{dropdown} Develop Serverless Services Using Python, C++, Java
:color: secondary
:chevron: down-up
:icon: chevron-down

:::{Note}

To run the following examples, you need to install openYuanrong first, with the following environment requirements:

- Install openYuanrong and run Python examples: `python<=3.11,>=3.9`.
- Run Java examples: `java 8/17/21`.
- Run C++ examples: `gcc>=10.3.0 and stdc++>=14`.

```bash
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong-0.10.0-py3-none-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_runtime-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_datasystem-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_functionsystem-0.10.0-py3-none-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_cpp_sdk-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_faas-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
```

:::

:::::{tab-set}
::::{tab-item} Python

```python
# handler is the function execution entry point, triggered for each request.
# event is the data passed by the HTTP request (Header, Body, etc.).
# context is the runtime context provided by openYuanrong, containing function, execution environment, and other information.
def handler(event, context):
    print("received request,event content:", event)

    response = ""
    try:
        response = "hello " + event.get("name")
    except Exception as e:
        print(e)
        response = "please enter your name,for example:{'name':'yuanrong'}"

    return response
```

Refer to [Function Service Example Project](example-project-function-service) to run this program.

::::
::::{tab-item} C++

```cpp
#include <string>
#include <nlohmann/json.hpp>

#include "Runtime.h"
#include "Function.h"
#include "yr/yr.h"

// HandleRequest is the function execution entry point, triggered for each request.
// event is the data passed by the HTTP request (Header, Body, etc.).
// context is the runtime context provided by openYuanrong, containing function, execution environment, and other information.
std::string HandleRequest(const std::string &event, Function::Context &context) {
    std::cout << "received request,event content:" << event << std::endl;

    std::string response = "";
    try {
        nlohmann::json jsonData = nlohmann::json::parse(event);
        // Read JSON data and output
        std::string name = jsonData["name"];
        response += "hello ";
        response += name;
    } catch (const std::exception& e) {
        std::cout << "JSON parsing error:" << e.what() << std::endl;
        response = "please enter your name,for example:{'name':'yuanrong'}";
    }
    return response;
}

int main(int argc, char *argv[])
{
    Function::Runtime rt;
    rt.RegisterHandler(HandleRequest);
    rt.Start(argc, argv);
    return 0;
}
```

Refer to [Function Service Example Project](example-project-function-service) to run this program.

::::
::::{tab-item} java

```java
package com.myapp.demo;

import org.yuanrong.services.runtime.Context;
import com.google.gson.JsonObject;

public class Demo {
    // handler is the function execution entry point, triggered for each request.
    // event is the data passed by the HTTP request (Header, Body, etc.).
    // context is the runtime context provided by openYuanrong, containing function, execution environment, and other information.
    public String handler(JsonObject event, Context context) {
        System.out.println("received request,event content:" + event);

        String response = "";
        try {
            String name = event.get("name").getAsString();
            response = "hello " + name;
        } catch(Exception e) {
            e.printStackTrace();
            response = "please enter your name,for example:{'name':'yuanrong'}";
        }
        return response;
    }
}
```

Refer to [Function Service Example Project](example-project-function-service) to run this program.

::::
:::::
::::::

Learn more about [Multi-language Function Programming Interface](multi_language_function_programming_interface/index.md).

(quickstart-deployment)=

## Installation and Deployment Quick Start

- openYuanrong supports deployment and execution on a single host for learning and development, and can be seamlessly extended to large clusters in production: [Learn More](deploy/index.md).

(quickstart-observability)=

## Observability Quick Start

- openYuanrong provides observability tools to monitor clusters and debug applications. These tools can help you understand application performance and identify bottlenecks: [Learn More](observability/index.md).
