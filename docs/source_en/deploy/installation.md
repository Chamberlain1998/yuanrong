# Installation Guide

openYuanrong currently supports installation on Linux x86_64 and aarch64 (ARM). Different development environments have the following dependencies:

- Install openYuanrong and develop Python applications: `python<=3.11,>=3.9`.
- Develop Java applications: `java 8/17/21`.
- Develop C++ applications: `gcc>=10.3.0 and stdc++>=14`.

(install-yuanrong-with-pip)=

## Install openYuanrong

Use pip to install the latest official version of openYuanrong from PyPI, which includes openYuanrong SDK and command-line tool yr.

```bash
# Supports the following versions
# openyuanrong-0.10.0-[py3-none/cp39-cp39/cp310-cp310/cp311-cp311]-manylinux_2_34_[x86_64/aarch64].whl
# Taking installation in Python3.9 and X86_64 environment as an example, minimum installation includes the following whl packages
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong-0.10.0-py3-none-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_runtime-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_datasystem-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_functionsystem-0.10.0-py3-none-manylinux_2_34_x86_64.whl
# Install when C++ SDK is needed
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_cpp_sdk-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
# Install when function service development is needed
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_faas-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
# Install when Dashboard capability is needed
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_dashboard-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
# Install when the agentruntime capability is needed
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/agent_dx_cli-0.10.0-py3-none-any.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/agent_dx_sdk-0.10.0-py3-none-any.whl
# Install when the ray-adapter capability is needed
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/ray_adapter-0.10.0-py3-none-any.whl
```

### Daily Builds

You can also install daily build versions via the following link. These versions have undergone automated testing but have not gone through the full release process and testing, and may contain unresolved issues.

Daily build versions: [Download Link](https://build-logs.openeuler.openatom.cn:38080/temp-archived/openeuler/openYuanrong/yr_daily/)

### Source Code Build

You may wish to compile openYuanrong from source code to meet more customization scenarios, please refer to: [Compile openYuanrong from Source Code](../contributor_guide/source_code_build.md).

## Use openYuanrong SDK

:::{admonition} SDK will automatically initialize the environment
:class: note

When calling yr.init() interface in [Driver](#glossary-driver) (without configuring openYuanrong cluster address), when running on a non-openYuanrong node, the SDK will attempt to start an openYuanrong temporary environment, which will be **automatically destroyed** when the process exits. This mechanism may be useful during debugging, but will cause the program to run longer.

:::

### Python SDK

After [installing openYuanrong](install-yuanrong-with-pip), you can use it in the current environment. For application development, refer to [Development Guide](../multi_language_function_programming_interface/development_guide/index.md).

(install-yuanrong-cpp-sdk)=

### C++ SDK

After [installing openYuanrong](install-yuanrong-with-pip), refer to the following commands to view the C++ SDK path. For application development, refer to [Development Guide](../multi_language_function_programming_interface/development_guide/index.md).

```console
[xxx]# python3 -c "import yr;print(yr.__path__[0])"
/usr/local/lib/python3.9/site-packages/yr

[xxx]# ls /usr/local/lib/python3.9/site-packages/yr/cpp/
bin  include  lib  VERSION
```

(install-yuanrong-java-sdk)=

### Java SDK

Refer to [Download openYuanrong](../reference/releases.md) to obtain the full package `openyuanrong-x.x.x.tar.gz` for the version. The Java SDK is located under the `openyuanrong/runtime/sdk/java` path. Among them, `yr-api-sdk-xxx.jar` is the single-machine program distributed parallelization SDK, `faas-function-sdk-xxx.jar` is the function service SDK. For application development, refer to [Development Guide](../multi_language_function_programming_interface/development_guide/index.md).

If you manage Java projects through Maven, refer to the following commands to install openYuanrong Java SDK, and introduce dependencies in the project `pom.xml` file.

```shell
# Replace yr-api-sdk-1.0.0.jar with your actual SDK version package
mvn install:install-file -Dfile=./yr-api-sdk-1.0.0.jar -DartifactId=yr-api-sdk \
    -DgroupId=org.yuanrong -Dversion=1.0.0 -Dpackaging=jar -DpomFile=./pom.xml

# Replace faas-function-sdk-1.0.0.jar with your actual SDK version package
mvn install:install-file -Dfile=./faas-function-sdk-1.0.0.jar -DartifactId=faas-function-sdk \
    -DgroupId=org.yuanrong -Dversion=1.0.0 -Dpackaging=jar -DpomFile=./pom.xml
```

Modify the project `pom.xml` file to introduce openYuanrong dependencies.

```xml
<dependencies>
    <dependency>
        <groupId>org.yuanrong</groupId>
        <artifactId>yr-api-sdk</artifactId>
        <version>1.0.0</version>
    </dependency>
    <dependency>
        <groupId>org.yuanrong</groupId>
        <artifactId>faas-function-sdk</artifactId>
        <version>1.0.0</version>
    </dependency>
</dependencies>
```

## Use openYuanrong Command-Line Tool

See [openYuanrong Command-Line Tool](../multi_language_function_programming_interface/api/yr_command_line_tool/index.md).
