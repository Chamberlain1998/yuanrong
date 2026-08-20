# Contribution Documentation

Documentation issues you discover can be reported to us by raising issues, and we also welcome your direct contributions: fixing errors (even punctuation marks), providing clearer explanations, supplementing section content, or creating new documentation.

This section will guide you through the preparation work before getting started.

## Which Documents Can You Contribute To

openYuanrong documentation mainly includes the following categories:

- [Getting Started](../getting_started.md)
- [Installation and Deployment](../deploy/installation.md)
- [Key Concepts](../multi_language_function_programming_interface/key_concept.md)
- [Development Guide](../multi_language_function_programming_interface/development_guide/index.md)
- [Examples](../multi_language_function_programming_interface/examples/monte-carlo-pi.md)
- [FAQ](../FAQ/multi_language_functional_programming.md)
- [API Reference](../multi_language_function_programming_interface/api/distributed_programming/Python/index.rst)

The openYuanrong documentation source code is stored in the `docs` directory of the [yuanrong](https://atomgit.com/openeuler/yuanrong/tree/master){target="_blank"} repository, with Chinese documentation in `docs/source_zh_cn/` and English documentation in `docs/source_en`. You can refer to existing documentation content to supplement chapters in the corresponding documentation directories. The correspondence between first-level directories and code paths is as follows:

- Overview: overview.md
- Getting Started: getting_started.md
- Installation and Deployment: deploy
- Use Cases: use_cases
- Multi-language Function Programming Interface: multi_language_function_programming_interface
- More Usage: more_usage
- Observability: observability
- FAQ: FAQ
- Contributor Guide: contributor_guide
- Reference: reference
- Security: security.md

## Documentation Syntax and Build Tools

openYuanrong documentation is generated based on the [Sphinx](https://www.sphinx-doc.org/en/master/){target="_blank"} build system. You can use Sphinx's native [reStructuredText (rST) syntax](https://www.sphinx-doc.org/en/master/usage/restructuredtext/index.html){target="_blank"}, or use [Markdown syntax](https://markdown.com.cn/basic-syntax/){target="_blank"} (with [myst-parser](https://myst-parser.readthedocs.io/en/latest/){target="_blank"} extension) to write documentation.

Documentation content is mainly divided into two types:

- Getting Started, Tutorials, Cases: These documents are written using [basic markdown syntax](https://markdown.com.cn/basic-syntax/){target="_blank"} (referred to as `.md` below).

- API:

   - **English documentation** for Python API and C++ API uses [Sphinx's autodoc extension](https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html){target="_blank"} to automatically generate API documentation from source code. Note that Python English API is written in [Google style](https://www.sphinx-doc.org/en/master/usage/extensions/example_google.html){target="_blank"}, using sphinx and sphinx.autodoc to complete content construction and documentation embedding, no need to write additional documentation. C++ English API is written in [Doxygen style](https://www.doxygen.nl/manual/commands.html){target="_blank"}, using doxygen and [breathe](https://breathe.readthedocs.io/en/latest/quickstart.html){target="_blank"} to complete content construction and documentation embedding, requiring additional `.md` documentation.
   - **Chinese documentation** for Python API and C++ API is written using [reStructuredText (rST) syntax](https://www.sphinx-doc.org/en/master/usage/restructuredtext/index.html){target="_blank"}, with English automatically generated completely.
   - For the sake of API documentation consistency, currently Java API **both Chinese and English documentation** are written using `.md` documents.

## How to Write Documentation

### Writing API Documentation

- Modify Python API documentation:

   - If you want to modify a Python API English document, you can click the blue `[source]` button in the upper right corner of the Python API English page to jump to its location in the code repository, and find the corresponding `.py` file in the English document storage path [python English repo-1](https://atomgit.com/openeuler/yuanrong/tree/master/api/python){target="_blank"} or [python English repo-2](https://atomgit.com/openeuler/yuanrong/tree/master/api/python/yr){target="_blank"} to modify the corresponding location. Following the basic principle of consistency between Chinese and English, when you modify the Python API English, please go to the corresponding Chinese document storage path to modify the API's Chinese document.
   - If you want to modify a Python API Chinese document, you can find the `.rst` document with the same name as the API in the Chinese document storage path [python Chinese repo](https://atomgit.com/openeuler/yuanrong/tree/master/docs/source_zh_cn/multi_language_function_programming_interface/api/distributed_programming/Python){target="_blank"} to modify. Similarly, following the basic principle of consistency between Chinese and English, please synchronously modify the API's English document.
   - If you want to add a new Python API, for English documentation, please find the corresponding `.py` file in the English document storage path mentioned above and write the **source code** and English documentation conforming to [Google style](https://www.sphinx-doc.org/en/master/usage/extensions/example_google.html){target="_blank"} at the appropriate position; for Chinese documentation, create a `.rst` document with the same name as the API in the Chinese document storage path mentioned above for writing. **Finally**, please find the API type to which your interface belongs in the `index.rst` files: [English index](https://atomgit.com/openeuler/yuanrong/tree/master/docs/source_en/multi_language_function_programming_interface/api/distributed_programming/Python){target="_blank"}, [Chinese index](https://atomgit.com/openeuler/yuanrong/tree/master/docs/source_zh_cn/multi_language_function_programming_interface/api/distributed_programming/Python){target="_blank"}, and add your interface there. Refer to the following for adding method (taking Chinese `index.rst` as an example, two places need to be added):

     ```text
     .. toctree::
        :glob:
        :hidden:
        :maxdepth: 1

        yr.origin_API
        yr.new_API

     Certain Type API
     ---------

     .. list-table::
        :header-rows: 0
        :widths: 30 70

        * - :doc:`yr.origin_API`
           - Original API first sentence description.
        * - :doc:`yr.is_initialized`
           - New API first sentence description.
     ```

   - Reference:

     **Python API function** Chinese can refer to [yr.init.rst](https://atomgit.com/openeuler/yuanrong/blob/master/docs/source_zh_cn/multi_language_function_programming_interface/api/distributed_programming/Python/yr.init.rst){target="_blank"}; corresponding English can refer to `def init(conf: Config = None) -> ClientInfo:` in [apis.py](https://atomgit.com/openeuler/yuanrong/blob/master/api/python/yr/apis.py){target="_blank"}.

     **Python API class** Chinese can refer to [yr.AlarmInfo.rst](https://atomgit.com/openeuler/yuanrong/blob/master/docs/source_zh_cn/multi_language_function_programming_interface/api/distributed_programming/Python/yr.AlarmInfo.rst){target="_blank"}; Attributes Chinese can refer to [yr.AlarmInfo.starts_at.rst](https://atomgit.com/openeuler/yuanrong/blob/master/docs/source_zh_cn/multi_language_function_programming_interface/api/distributed_programming/Python/yr.AlarmInfo.starts_at.rst){target="_blank"}, Methods Chinese can refer to [yr.AlarmInfo.__init__.rst](https://atomgit.com/openeuler/yuanrong/blob/master/docs/source_zh_cn/multi_language_function_programming_interface/api/distributed_programming/Python/yr.AlarmInfo.__init__.rst){target="_blank"}, English can refer to `class AlarmInfo` in [runtime.py](https://atomgit.com/openeuler/yuanrong/blob/master/api/python/yr/runtime.py){target="_blank"}.

- Modify C++ API documentation:

   - If you want to modify a C++ API English document, you can find the corresponding `.h` file in the English document storage path [c++ English repo](https://atomgit.com/openeuler/yuanrong/tree/master/api/cpp/include/yr){target="_blank"} and its subdirectories to modify the corresponding location. Following the basic principle of consistency between Chinese and English, when you modify the C++ API English, please go to the corresponding Chinese document storage path to modify the API's Chinese document. Note that if you want to modify the API's sample code, you can find the `.cpp` file next to the `@snippet{trimleft}` macro definition (usually at the end of comments) in the source file, and modify it in the [sample code repo](https://atomgit.com/openeuler/yuanrong/tree/master/api/cpp/example){target="_blank"}. Following the basic principle of consistency between Chinese and English, when you modify the C++ API English, please go to the corresponding Chinese document storage path to modify the API's Chinese document.
   - If you want to modify a C++ API Chinese document, you can find the `.rst` document with the same name as the API in the [c++ Chinese document storage path](https://atomgit.com/openeuler/yuanrong/tree/master/docs/source_zh_cn/multi_language_function_programming_interface/api/distributed_programming/Cpp){target="_blank"} to modify. Similarly, following the basic principle of consistency between Chinese and English, please synchronously modify the API's English document.
   - If you want to add a new C++ API, for English documentation, please find the appropriate `.h` file in the English document storage path mentioned above, write the source code and English documentation conforming to [Doxygen style](https://www.doxygen.nl/manual/commands.html){target="_blank"} at the appropriate position, and create or find the appropriate `.cpp` file in the sample code repo mentioned above to add your sample code. Finally, create a `.md` document with the same name as the API in the sample code repo mentioned above, and use doxygen/breathe syntax such as `{doxygenfunction}`, `{doxygenclass}`, `{doxygenvariable}`, `{doxygenstruct}`, `{doxygenenum}`, `{doxygentypedef}` to add references to functions, classes, variables, structures, members, definitions, etc. Specific usage examples are as follows:

     ```text
         ```{doxygenfunction} Function_name
         ```
     ```

     For Chinese documentation, please create a `.rst` document with the same name as the API in the Chinese document storage path mentioned above for writing. Finally, please find the API type to which your interface belongs in [English index](https://atomgit.com/openeuler/yuanrong/tree/master/docs/source_en/multi_language_function_programming_interface/api/distributed_programming/Cpp/index.rst){target="_blank"}, [Chinese index](https://atomgit.com/openeuler/yuanrong/tree/master/docs/source_zh_cn/multi_language_function_programming_interface/api/distributed_programming/Cpp/index.rst){target="_blank"}, and add your interface there. Refer to the Python API addition method shown above for adding method.
   - Reference: C++ API English `.md` document can refer to [Get.md](https://atomgit.com/openeuler/yuanrong/tree/master/docs/source_en/multi_language_function_programming_interface/api/distributed_programming/Cpp/Get.md){target="_blank"}; source code can refer to

     ```C++
     template <typename T>
     std::shared_ptr<T> Get(const ObjectRef<T> &obj, int timeout = DEFAULT_GET_TIMEOUT_SEC);
     ```

     in [yr.h](https://atomgit.com/openeuler/yuanrong/tree/master/api/cpp/include/yr/yr.h){target="_blank"}; sample code can refer to [get_put_example.cpp](https://atomgit.com/openeuler/yuanrong/tree/master/api/cpp/example/get_put_example.cpp){target="_blank"}. Chinese can refer to [Get.rst](https://atomgit.com/openeuler/yuanrong/tree/master/docs/source_zh_cn/multi_language_function_programming_interface/api/distributed_programming/Cpp/Get.md){target="_blank"}.

- Modify Java API documentation:

   - If you want to modify a Java API English document, you can find the `.md` file with the same name as the API in the [Java English document storage path](https://atomgit.com/openeuler/yuanrong/tree/master/docs/source_en/multi_language_function_programming_interface/api/distributed_programming/Java){target="_blank"} to modify the corresponding location. Following the basic principle of consistency between Chinese and English, when you modify the Java API English, please go to the corresponding Chinese document storage path to modify the API's Chinese document.
   - If you want to modify a Java API Chinese document, you can find the `.md` document with the same name as the API in the [Java Chinese document storage path](https://atomgit.com/openeuler/yuanrong/tree/master/docs/source_zh_cn/multi_language_function_programming_interface/api/distributed_programming/Java){target="_blank"} to modify. Similarly, following the basic principle of consistency between Chinese and English, please synchronously modify the API's English document.
   - If you want to add a new Java API, for English documentation, please create a `.md` document with the same name as the API in the English document storage path mentioned above; for Chinese, create a `.md` document with the same name as the API in the Chinese document storage path mentioned above. Finally, please find the API type to which your interface belongs in [English index](https://atomgit.com/openeuler/yuanrong/tree/master/docs/source_en/multi_language_function_programming_interface/api/distributed_programming/Java/index.rst){target="_blank"}, [Chinese index](https://atomgit.com/openeuler/yuanrong/tree/master/docs/source_zh_cn/multi_language_function_programming_interface/api/distributed_programming/Java/index.rst){target="_blank"}, and add your interface there. Refer to the Python API addition method shown above for adding method.

### Writing Other Documentation

Except for API, other documentation (getting started, tutorials, case documentation) is written by modifying or adding `.md` files. `.md` files are divided into two types by function: directory and content, with directory files uniformly using the filename `index.md`. Generally, directory files need to provide an "introduction" description of the directory content, you can refer to [file directory index.md](https://atomgit.com/openeuler/yuanrong/blob/master/docs/source_en/deploy/index.md){target="_blank"}. Newly added `.md` files will only take effect after adding the filename to the `index.md` file, for example: the [Installation Guide](https://atomgit.com/openeuler/yuanrong/blob/master/docs/source_en/deploy/installation.md){target="_blank"} under the openYuanrong documentation "Installation and Deployment" directory, its filename is within the above file directory.

```text
......
.. toctree::
   :glob:
   :maxdepth: 1
   :hidden:

   installation
......
```

In addition to basic markdown syntax, common extended syntax can refer to examples in [Admonitions](https://myst-parser.readthedocs.io/en/latest/syntax/admonitions.html){target="_blank"}, [Source code and APIs](https://myst-parser.readthedocs.io/en/latest/syntax/code_and_apis.html){target="_blank"}, and [Cross-references](https://myst-parser.readthedocs.io/en/latest/syntax/cross-referencing.html){target="_blank"}.

## Development Tools

It is recommended to use [VS Code](https://code.visualstudio.com/){target="_blank"} tool, search and install the following two plugins in the Extensions bar:

- Markdown All in one: Can preview in real time. After installation, press Ctrl + Shift + P to call the main command box, select Markdown: Open Preview to the Side to open the preview effect
- markdownlint: Can check and help fix some syntax issues. Refer to [markdownlint](https://github.com/markdownlint/markdownlint/blob/main/docs/RULES.md){target="_blank"} for complete check rules.

## Documentation Build and Testing

### Install Dependencies

- Download and install [Doxygen](https://github.com/doxygen/doxygen){target="_blank"} (version 1.12.0 or above).
- Execute the following command in the code `docs` directory:

   ```bash
   pip install -r requirements_dev.txt
   ```

### Install openYuanrong SDK

English documentation for openYuanrong Python API is generated from the source code in the installation package, so you need to [install openYuanrong](install-yuanrong-with-pip) the latest version first to generate English API documentation. If you have added new interfaces and need to supplement documentation, you need to install the source code compiled version.

### Build Documentation

Execute the following command in the code `yuanrong/docs` directory:

```bash
bash build.sh -P
```

The generated files are in the `yuanrong/output/docs` directory. To rebuild, please delete the `yuanrong/docs/source_zh_cn/_build` and `yuanrong/docs/source_en/_build` directories first to avoid the impact of historical build files.

### Local Testing

In the build result `output` directory, execute the following command:

```bash
# Replace <port> with an available external port on the documentation build machine
python3 -m http.server <port> -d docs
```

Access in the browser: `http://<Your Documentation Build Host IP>:<port>/en/index.html`
