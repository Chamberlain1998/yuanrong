# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
import os
import logging
import datetime
from pathlib import Path

sys.path.append(os.path.abspath('.'))
from icons import ICONS
from blog_data import BLOG_DATA

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

if os.getenv("BUILD_WITH_PACKAGE", "").lower() == "false":
    sys.path.insert(0, str(Path("..", "api", "python").resolve()))
else:
    logging.info("The installed openYuanrong package will be used to generate the Python API doc")


ENV_YR_GIT_COMMIT_ID = os.environ.get("YR_DOC_GIT_COMMIT_ID", "")
ENV_BUILD_VERSION = os.environ.get("BUILD_VERSION", "")

build_time = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(
    hours=8
)
current_time_str = build_time.strftime("%Y-%m-%d %H:%M:%S")

project = "openYuanrong"
copyright = f"{build_time.year}, openEuler openYuanrong"
author = "openYuanrong with CC BY 4.0 LICENSE"
language = "zh_CN"

logging.info(
    f"""Doc build configs:
ENV_YR_GIT_COMMIT_ID: {ENV_YR_GIT_COMMIT_ID}
ENV_BUILD_VERSION: {ENV_BUILD_VERSION}
current_date: {current_time_str}
project: {project}
copyright: {copyright}
author: {author}
"""
)

templates_path = ["_templates"]
exclude_patterns = [
    "_build",
    "README.md",
    "sample_code",
    "observability/trace_optimization.md",

    ""
]

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx_sitemap",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_design",
    "sphinx_copybutton",
    "sphinx_togglebutton",
    "myst_parser",
    "breathe",
    "sphinxcontrib.openapi",  # 添加 sphinxcontrib-openapi 扩展
]
# Enable Chinese search: Sphinx uses jieba for word-level tokenization in the index.
html_search_language = "zh"
sitemap_url_scheme = "{link}"
sitemap_show_lastmod = True
sitemap_excludes = [
    "search.html",
    "genindex.html",
    "_modules/**",
]
autoclass_content = "both"
copybutton_exclude = ".linenos, .gp, .go"


# -----------------------------------------------------------------------------
#   FOR PYTHON API GENEREATE
# -----------------------------------------------------------------------------
autodoc_mock_imports = ["acl", "requests", "fastapi", "numpy"]

autosummary_generate = True
autosummary_generate_overwrite = True  # 覆盖已生成的文件
autosummary_ignore_module_all = False  # 不忽略 __all__ 的限制
autosummary_imported_members = True


# -----------------------------------------------------------------------------
#   HTML templates
# -----------------------------------------------------------------------------
html_logo = "../images/logo-small.png"
html_theme = "sphinx_book_theme"

html_static_path = ["../_static"]
html_favicon = "../_static/favicon.ico"
html_extra_path = ["robots.txt"]
html_css_files = [
    "custom.css",
    "css/dismissable-banner.css",
    "css/custom-dropdown.css",
]
html_js_files = [
    "language-switcher.js",
    "js/dismissable-banner.js",
    "search_cjk_dict.js",
]
# IMPORTANT: CJK search segmentation requires build.sh, NOT bare sphinx-build.
# search_cjk_split.js is appended to searchtools.js by build.sh at build
# time, avoiding script loading order issues (html_js_files load before
# searchtools.js in the HTML template). Running sphinx-build directly
# without build.sh will cause CJK segmentation to be missing. Always use:
#   ./build.sh -v <version>
#   确保末尾加上斜杠
html_baseurl = "https://docs.openyuanrong.org/zh-cn/latest/"
html_theme_options = {
    "show_navbar_depth": 1,
    "max_navbar_depth": 7,
    "collapse_navigation": True,
    "home_page_in_toc": True,
    "check_switcher": False,
    "announcement": (
        "🚀 <b>openYuanrong v0.10.0</b> 已发布 — Agent 提供企业级语义一致的故障恢复和高并发能力"
        " &nbsp;·&nbsp; "
        "<a href='https://gitcode.com/openeuler/yuanrong/releases'>查看详情 →</a>"
        "<button type='button' id='close-banner' aria-label='关闭横幅'>&times;</button>"
    ),
    "extra_footer": """
        Built with
        <a href="https://www.sphinx-doc.org/en/master/">Sphinx</a>
        using a
        <a href="https://github.com/executablebooks/sphinx-book-theme">theme</a>
        provided by
        <a href="https://github.com/executablebooks">Executable Books Project</a>.
    """,
    "navbar_start": [
        "navbar-logo",
        "navbar-nav",
         ],
    "navbar_end": [
        "language-switcher",
        "version-switcher",
        ],
    "switcher": {
        "json_url": "https://docs.openyuanrong.org/versions.json",
        "version_match": os.getenv("BUILD_VERSION", "latest"),
    },
    "logo": {
        "image_light": "_static/image-light.png",
        "image_dark": "_static/image-dark.png"
    },
    "secondary_sidebar_items": {
        "**": ["page-toc"],
        "index": []
    },
}

html_sidebars = {
    "**": ["search-button-field.html", "sbt-sidebar-nav.html"]
}

# 使用自定义首页模板
html_additional_pages = {
    'index': 'custom-index.html'
}

# -----------------------------------------------------------------------------
#   Myst extensions
# -----------------------------------------------------------------------------
myst_enable_extensions = [
    "dollarmath",
    "amsmath",
    "deflist",
    "fieldlist",
    "html_admonition",
    "html_image",
    "colon_fence",
    "smartquotes",
    "replacements",
    "strikethrough",
    "substitution",
    "tasklist",
    "attrs_inline",
    "attrs_block",
]

# -----------------------------------------------------------------------------
#   breathe config
# -----------------------------------------------------------------------------
breathe_projects = {"openYuanrong": "./../.doxygendocs/xml"}
breathe_default_project = "openYuanrong"


html_context = {
    **ICONS,
    "blog_data": BLOG_DATA,
    "doc_language": "zh-cn",
    "base_url": html_baseurl,  # requires html_baseurl to be set above
    "metatags": """
            <meta name="author" content="openYuanrong Team">
            <meta name="keywords" content="openYuanrong, 分布式计算引擎, AI推理, Serverless">
        """
}


def setup(app):
    """构建时从 searchindex.js 提取 CJK 词表，输出为 JS 词典文件。

    前端 search_cjk_split.js 使用此词典做最大前向匹配分词，
    将连续中文查询（如"分布式计算引擎"）拆分为 ["分布式","计算","引擎"]，
    与 jieba 索引词精确匹配。字典文件由 build-finished 事件自动生成，
    覆盖 _static/search_cjk_dict.js 占位文件。
    """

    def _build_cjk_dict(app, exception):
        if exception:
            return
        outdir = app.outdir
        index_path = os.path.join(outdir, "searchindex.js")
        if not os.path.isfile(index_path):
            return

        import re
        import json
        with open(index_path, "r", encoding="utf-8") as f:
            raw = f.read()

        # 从 Search.setIndex({...}) 中提取 terms 字典
        # Sphinx format: "Search.setIndex({...});" or "Search.setIndex({...})"
        # Strip prefix and suffix before JSON parsing.
        prefix = "Search.setIndex("
        start = raw.find(prefix)
        if start == -1:
            logging.warning("Search: searchindex.js format unrecognized, CJK dict not generated")
            return
        json_str = raw[start + len(prefix):].rstrip()
        # Strip trailing ); or ), take outermost {} as JSON
        if json_str.endswith(");"):
            json_str = json_str[:-2]
        elif json_str.endswith(")"):
            json_str = json_str[:-1]
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logging.warning("Search: searchindex.js JSON parse failed, CJK dict not generated")
            return

        # 收集 ≥2 字的纯 CJK 索引词。
        # 仅收集纯汉字词（如 "分布式"、"计算"），不收集中英混合词（如 "AI推理"）。
        # 中英混合查询由前端 search_cjk_split.js 的 extractCJKParts 处理：
        # "AI推理" → ["AI", "推理"]，其中 "推理" 在词典中可精确匹配。
        # 单字不在词典中时，退化为部分匹配（如 "式" → term.match("式") 命中 "分布式"）。
        words = sorted(
            k for k in data.get("terms", {})
            if re.match(r"^[\u4e00-\u9fff]{2,}$", k)
        )

        # 写入 JS 词典文件（覆盖构建时的占位文件）
        dict_path = os.path.join(outdir, "_static", "search_cjk_dict.js")
        os.makedirs(os.path.dirname(dict_path), exist_ok=True)
        with open(dict_path, "w", encoding="utf-8") as f:
            f.write("var SEARCH_CJK_DICT = ")
            json.dump(words, f, ensure_ascii=False)
            f.write(";\n")

        logging.info(f"Search: generated CJK dict ({len(words)} words)")

    # search_cjk_split.js is appended to searchtools.js by build.sh at build
    # time, avoiding script loading order issues (html_js_files load before
    # searchtools.js in the HTML template).

    app.connect("build-finished", _build_cjk_dict)
