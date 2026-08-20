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
language = "en"

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
    "multi_language_function_programming_interface/api/distributed_programming/Python/yr.StatefulInstanceCreator.*",
    "multi_language_function_programming_interface/api/distributed_programming/Python/yr.StatefulInstance.*",
    "multi_language_function_programming_interface/api/distributed_programming/Python/yr.StatelessFunction.*",

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
]
#   确保末尾加上斜杠
html_baseurl = "https://docs.openyuanrong.org/en/latest/"
html_theme_options = {
    "show_navbar_depth": 1,
    "max_navbar_depth": 7,
    "collapse_navigation": True,
    "home_page_in_toc": True,
    "check_switcher": False,
    "announcement": (
        "🚀 <b>openYuanrong v0.10.0</b> released — Agent provides enterprise‑grade capabilities for "
        "semantic consistency, fault recovery, and high concurrency"
        " distributed runtime now available &nbsp;·&nbsp; "
        "<a href='https://gitcode.com/openeuler/yuanrong/releases'>Learn more →</a>"
        "<button type='button' id='close-banner' aria-label='Close banner'>&times;</button>"
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
        "json_url": "https://docs.openyuanrong.org/en/versions.json",
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
breathe_projects = {"openYuanrong": "../../.doxygendocs/xml"}
breathe_default_project = "openYuanrong"


html_context = {
    **ICONS,
    "blog_data": BLOG_DATA,
    "doc_language": "en",
    "base_url": html_baseurl,  # requires html_baseurl to be set above
    "metatags": """
            <meta name="author" content="openYuanrong Team">
            <meta name="keywords" content="openYuanrong, distributed computing engine, AI inference, Serverless">
        """
}
