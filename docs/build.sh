#!/bin/bash
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
set -e
source /etc/profile.d/*.sh

readonly SCRIPT_NAME=$(basename "$0")

show_help() {
cat << EOF
Usage: $SCRIPT_NAME -v VERSION [-P] [-h]

Configure and build openYuanrong documentation.

Options:
  -v VERSION    (Optional) Specify the version string. Defaults to "latest".
  -P            (Optional) Use the installed package instead of building the runtime from source.
  -h            Display this help message and exit.

Examples:
  ./$SCRIPT_NAME -v 1.0.0
  ./$SCRIPT_NAME -v 1.0.0 -P
EOF
}

BUILD_VERSION="latest"
BUILD_WITH_PACKAGE="false"

while getopts "hv:P" opt; do
  case $opt in
    h)
      show_help
      exit 0
      ;;
    v)
      BUILD_VERSION="$OPTARG"
      ;;
    P)
      BUILD_WITH_PACKAGE="true"
      ;;
    \? | :)
      echo "Try '$SCRIPT_NAME -h' for more information." >&2
      exit 1
      ;;
  esac
done

# Export environment variables
export BUILD_VERSION
export BUILD_WITH_PACKAGE

BASE_DIR=$(dirname "$(readlink -f "$0")")
OUTPUT_DIR=${BASE_DIR}/../output

# Patch Sphinx searchtools.js to fix CJK search limitations.
# 1. Remove digit filter: Sphinx skips pure-digit query terms (e.g. "8080").
# 2. Bypass Porter Stemmer for CJK: stemmer destroys Chinese characters.
# 3. Relax length filter for CJK: allow single/double-char CJK terms in
#    partial matching and filteredTermCount.
# 4. Append CJK segmentation logic to ensure it loads after searchtools.js.
# Each patch includes an idempotency guard: if already patched, skip.
function patch_searchtools() {
  local JS_FILE="$1"
  local STATIC_DIR="$(dirname "$JS_FILE")"
  # Remove digit filter (multiline: || spans two lines)
  grep -q 'queryTerm\.match.*\\d' "$JS_FILE" \
    && sed -i '/||$/{N;s/||\n\s*queryTerm\.match(\/\^\\d+\$\/)//;}' "$JS_FILE"
  # Bypass stemmer for CJK terms
  grep -q 'queryTermLower) ? queryTermLower' "$JS_FILE" \
    || sed -i 's/let word = stemmer\.stemWord(queryTermLower);/let word = \/[\\u4e00-\\u9fff]\/.test(queryTermLower) ? queryTermLower : stemmer.stemWord(queryTermLower);/' "$JS_FILE"
  # Relax length filter for CJK terms.
  # Use CJK_LEN_PATCH sentinel for idempotency.
  # Use /* ... */ block comment to avoid line-end // swallowing closing
  # parens/semicolons on arrow-function expressions (e.g. .filter(...);).
  # Use # as sed delimiter to avoid escaping / and conflict with || in replacement.
  if ! grep -q 'CJK_LEN_PATCH' "$JS_FILE"; then
    sed -i 's#if (word\.length > 2) {#if (word.length > 2 || /[\u4e00-\u9fff]/.test(word)) { /* CJK_LEN_PATCH */#' "$JS_FILE"
    sed -i 's#(term) => term\.length > 2#(term) => term.length > 2 || /[\u4e00-\u9fff]/.test(term) /* CJK_LEN_PATCH */#' "$JS_FILE"
  fi
  # Append CJK segmentation logic to searchtools.js.
  # This avoids script loading order issues: html_js_files scripts load in <head>
  # before searchtools.js (which loads at </body>). Appending directly ensures
  # the splitQuery override runs after searchtools.js defines it.
  # Idempotency: build.sh writes the CJK_SPLIT_APPENDED sentinel itself,
  # so the check does not depend on the source file's content.
  local SPLIT_SRC="$STATIC_DIR/search_cjk_split.js"
  if [[ ! -f "$SPLIT_SRC" ]]; then
    echo "ERROR: $SPLIT_SRC not found, CJK segmentation cannot be injected" >&2
    return 1
  fi
  grep -q 'CJK_SPLIT_APPENDED' "$JS_FILE" \
    || { echo "/* CJK_SPLIT_APPENDED */" >> "$JS_FILE"; cat "$SPLIT_SRC" >> "$JS_FILE"; }
}

# Add noindex meta tag to all HTML files in a directory (for non-latest versions).
# This prevents Google from indexing outdated documentation.
function add_noindex() {
  local DIR="$1"
  find "$DIR" -name "*.html" -not -path "*/_static/*" -not -path "*/_modules/*" -not -path "*/_sources/*" | while read -r file; do
    if ! grep -q 'name="robots"' "$file"; then
      # Insert noindex meta tag right after <head> (preserving original attributes)
      sed -i 's/<head[^>]*>/&\n    <meta name="robots" content="noindex, nofollow">/' "$file"
    fi
  done
}

function build_zh_cn() {
  pushd "${BASE_DIR}"/source_zh_cn
  make html
  # disable configuration：SPHINXOPTS="-W --keep-going -n", enable it after all alarms are cleared.
  popd

  patch_searchtools "${BASE_DIR}"/source_zh_cn/_build/html/_static/searchtools.js

  if [ "$BUILD_VERSION" = "latest" ]; then
    rm -rf "${OUTPUT_DIR}"/docs/zh-cn/latest && mkdir -p "${OUTPUT_DIR}"/docs/zh-cn/latest
    cp -rf "${BASE_DIR}"/source_zh_cn/_build/html/* "${OUTPUT_DIR}"/docs/zh-cn/latest
    # sphinx_sitemap does not include html_additional_pages (custom-index.html).
    # Add the homepage index.html to the sitemap manually (inside <urlset>).
    SITEMAP="${OUTPUT_DIR}"/docs/zh-cn/latest/sitemap.xml
    if [ -f "$SITEMAP" ]; then
      BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
      sed -i "/<urlset.*>/a<url><loc>https://docs.openyuanrong.org/zh-cn/${BUILD_VERSION}/index.html</loc><lastmod>${BUILD_DATE}</lastmod></url>" "$SITEMAP"
    fi
  else
    rm -rf "${OUTPUT_DIR}"/docs/zh-cn/${BUILD_VERSION} && mkdir -p "${OUTPUT_DIR}"/docs/zh-cn/${BUILD_VERSION}
    cp -rf "${BASE_DIR}"/source_zh_cn/_build/html/* "${OUTPUT_DIR}"/docs/zh-cn/${BUILD_VERSION}
    # Non-latest versions should not be indexed by search engines.
    add_noindex "${OUTPUT_DIR}"/docs/zh-cn/${BUILD_VERSION}
    # Remove sitemap so search engines won't discover these pages.
    rm -f "${OUTPUT_DIR}"/docs/zh-cn/${BUILD_VERSION}/sitemap.xml
  fi
}

function build_en() {
  pushd "${BASE_DIR}"/source_en
  make html
  # disable configuration：SPHINXOPTS="-W --keep-going -n", enable it after all alarms are cleared.
  popd

  patch_searchtools "${BASE_DIR}"/source_en/_build/html/_static/searchtools.js

  if [ "$BUILD_VERSION" = "latest" ]; then
    rm -rf "${OUTPUT_DIR}"/docs/en/latest && mkdir -p "${OUTPUT_DIR}"/docs/en/latest
    cp -rf "${BASE_DIR}"/source_en/_build/html/* "${OUTPUT_DIR}"/docs/en/latest
    # sphinx_sitemap does not include html_additional_pages (custom-index.html).
    # Add the homepage index.html to the sitemap manually (inside <urlset>).
    SITEMAP="${OUTPUT_DIR}"/docs/en/latest/sitemap.xml
    if [ -f "$SITEMAP" ]; then
      BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
      sed -i "/<urlset.*>/a<url><loc>https://docs.openyuanrong.org/en/${BUILD_VERSION}/index.html</loc><lastmod>${BUILD_DATE}</lastmod></url>" "$SITEMAP"
    fi
  else
    rm -rf "${OUTPUT_DIR}"/docs/en/${BUILD_VERSION} && mkdir -p "${OUTPUT_DIR}"/docs/en/${BUILD_VERSION}
    cp -rf "${BASE_DIR}"/source_en/_build/html/* "${OUTPUT_DIR}"/docs/en/${BUILD_VERSION}
    # Non-latest versions should not be indexed by search engines.
    add_noindex "${OUTPUT_DIR}"/docs/en/${BUILD_VERSION}
    # Remove sitemap so search engines won't discover these pages.
    rm -f "${OUTPUT_DIR}"/docs/en/${BUILD_VERSION}/sitemap.xml
  fi
}

function generate_sitemap_index() {
  # Generate sitemap index at root level referencing both language sitemaps
  cat > "${OUTPUT_DIR}"/docs/sitemap.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://docs.openyuanrong.org/zh-cn/latest/sitemap.xml</loc>
  </sitemap>
  <sitemap>
    <loc>https://docs.openyuanrong.org/en/latest/sitemap.xml</loc>
  </sitemap>
</sitemapindex>
EOF

  # Generate robots.txt at root level
  cat > "${OUTPUT_DIR}"/docs/robots.txt << 'EOF'
User-agent: *
Allow: /zh-cn/latest
Allow: /en/latest
Disallow: /zh-cn/
Disallow: /en/
Disallow: */search.html

Sitemap: https://docs.openyuanrong.org/sitemap.xml
EOF
}

function doc_build() {
  pip install -r "${BASE_DIR}"/requirements_dev.txt
  build_zh_cn
  build_en
  generate_sitemap_index
}

doc_build
