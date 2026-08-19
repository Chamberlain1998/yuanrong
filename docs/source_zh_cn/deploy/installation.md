# 安装指南

openYuanrong 目前支持在 Linux x86_64 及 aarch64 (ARM) 上安装。不同的开发环境安装依赖如下：

- 安装 openYuanrong 并开发 Python 应用：`python<=3.11,>=3.9` 。
- 开发 Java 应用：`java 8/17/21` 。
- 开发 C++ 应用：`gcc>=10.3.0 且 stdc++>=14` 。

(install-yuanrong-with-pip)=

## 安装 openYuanrong

使用 pip 从 PyPI 上安装 openYuanrong 最新官方版本，内含 openYuanrong SDK 及命令行工具 yr。

```bash
# 支持以下版本
# openyuanrong-0.10.0-[py3-none/cp39-cp39/cp310-cp310/cp311-cp311]-manylinux_2_34_[x86_64/aarch64].whl
# 以在Python3.9及X86_64环境安装为例，最小安装包含以下whl包
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong-0.10.0-py3-none-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_runtime-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_datasystem-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_functionsystem-0.10.0-py3-none-manylinux_2_34_x86_64.whl
# 需要使用C++ SDK时安装
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_cpp_sdk-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
# 需要开发函数服务时安装
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_faas-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
# 需要使用Dashboard能力时安装
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/openyuanrong_dashboard-0.10.0-cp39-cp39-manylinux_2_34_x86_64.whl
# 需要使用agentruntime能力时安装
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/agent_dx_cli-0.10.0-py3-none-any.whl
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/agent_dx_sdk-0.10.0-py3-none-any.whl
# 需要使用ray-adapter能力时安装
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.10.0/openeuler/x86_64/ray_adapter-0.10.0-py3-none-any.whl
```

### 每日版本

您也可以通过以下链接安装每日构建的版本。这些版本经过自动化测试，但未经过完整的发布流程和测试，可能包含未解决的问题。

每日构建版本：[下载链接](https://build-logs.openeuler.openatom.cn:38080/temp-archived/openeuler/openYuanrong/yr_daily/)

### 源码编译版本

您可能希望从源码编译 openYuanrong 版本，以满足更多自定义场景，请参考章节：[源码编译 openYuanrong](../contributor_guide/source_code_build.md)。

## 使用 openYuanrong SDK

:::{admonition} SDK 会自动初始化环境
:class: note

[Driver](#glossary-driver) 中调用 yr.init() 接口（不配置 openYuanrong 集群的地址），运行在非 openYuanrong 节点上时，SDK 会尝试拉起一个 openYuanrong 临时环境，该环境在进程退出时**自动销毁**。该机制在调试时可能很有用，但会导致程序运行时间变长。

:::

### Python SDK

[安装 openYuanrong](install-yuanrong-with-pip)后，即可在当前环境中使用，应用开发请参考[开发指南](../multi_language_function_programming_interface/development_guide/index.md)。

(install-yuanrong-cpp-sdk)=

### C++ SDK

[安装 openYuanrong](install-yuanrong-with-pip)后，参考如下命令查看 C++ SDK 路径，应用开发请参考[开发指南](../multi_language_function_programming_interface/development_guide/index.md)。

```console
[xxx]# python3 -c "import yr;print(yr.__path__[0])"
/usr/local/lib/python3.9/site-packages/yr

[xxx]# ls /usr/local/lib/python3.9/site-packages/yr/cpp/
bin  include  lib  VERSION
```

(install-yuanrong-java-sdk)=

### Java SDK

参考[下载 openYuanrong](../reference/releases.md)获取版本的全量包 `openyuanrong-x.x.x.tar.gz`，Java SDK 在 `openyuanrong/runtime/sdk/java` 路径下。其中 `yr-api-sdk-xxx.jar` 为单机程序分布式并行化 SDK，`faas-function-sdk-xxx.jar` 为函数服务 SDK，应用开发请参考[开发指南](../multi_language_function_programming_interface/development_guide/index.md)。

如果您通过 Maven 管理 Java 项目，可参考如下命令安装 openYuanrong Java SDK，并在项目 `pom.xml` 文件中引入依赖。

```shell
# 替换 yr-api-sdk-1.0.0.jar 为您实际的 SDK 版本包
mvn install:install-file -Dfile=./yr-api-sdk-1.0.0.jar -DartifactId=yr-api-sdk \
    -DgroupId=org.yuanrong -Dversion=1.0.0 -Dpackaging=jar -DpomFile=./pom.xml

# 替换 faas-function-sdk-1.0.0.jar 为您实际的 SDK 版本包
mvn install:install-file -Dfile=./faas-function-sdk-1.0.0.jar -DartifactId=faas-function-sdk \
    -DgroupId=org.yuanrong -Dversion=1.0.0 -Dpackaging=jar -DpomFile=./pom.xml
```

修改项目 `pom.xml` 文件，引入 openYuanrong 依赖。

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

## 使用 openYuanrong 命令行工具

详见[openYuanrong 命令行工具](../multi_language_function_programming_interface/api/yr_command_line_tool/index.md)。
