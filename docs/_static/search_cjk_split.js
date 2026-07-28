/**
 * search_cjk_split.js — CJK search query segmentation for Sphinx
 *
 * Overrides Sphinx's default splitQuery to segment continuous CJK text
 * using maximum forward matching against SEARCH_CJK_DICT (the dictionary
 * of jieba-indexed terms generated at build time by search_cjk_dict.js).
 *
 * Load order requirement: SEARCH_CJK_DICT must be defined before this
 * script runs. In production, build.sh appends this script to the end
 * of searchtools.js, which loads at </body> — after search_cjk_dict.js
 * (included via html_js_files in <head>). A runtime guard checks for
 * SEARCH_CJK_DICT and disables CJK segmentation if it is missing.
 *
 * Example: "分布式计算引擎" → ["分布式", "计算", "引擎"]
 * Each segmented word matches jieba-indexed terms exactly (score 5),
 * instead of being treated as one giant token that fails to match.
 *
 * Key design: CJK portions of the query are segmented directly from the
 * raw input, bypassing originalSplitQuery (which uses jieba cut_for_search
 * and produces sub-words like "分布式"→["分布式","分布","式"]). This ensures
 * only dictionary-matched words appear in search results, avoiding false
 * matches on sub-word fragments.
 *
 * This script is appended to searchtools.js by build.sh at build time,
 * ensuring it loads after searchtools.js defines splitQuery.
 */

(function () {
  "use strict";

  if (typeof splitQuery === "undefined") {
    console.warn("search_cjk_split: splitQuery not found, CJK segmentation disabled");
    return;
  }
  if (typeof SEARCH_CJK_DICT === "undefined") {
    console.warn("search_cjk_split: SEARCH_CJK_DICT not loaded, CJK segmentation disabled");
    return;
  }

  var originalSplitQuery = splitQuery;

  var CJK_CHAR = /[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]/;
  var dictSet = new Set(SEARCH_CJK_DICT);
  var maxLen = 0;
  for (var i = 0; i < SEARCH_CJK_DICT.length; i++) {
    if (SEARCH_CJK_DICT[i].length > maxLen) maxLen = SEARCH_CJK_DICT[i].length;
  }

  splitQuery = function (query) {
    // First, extract continuous CJK segments directly from the raw query.
    // This bypasses originalSplitQuery's jieba cut_for_search, which would
    // produce sub-words (e.g. "分布式"→["分布式","分布","式"]) that cause
    // false matches on fragment terms like "分布" or "式".
    var cjkParts = extractCJKFromRaw(query);
    var result = [];

    for (var i = 0; i < cjkParts.length; i++) {
      var part = cjkParts[i];
      if (CJK_CHAR.test(part)) {
        // CJK segment: apply dictionary-based maximum forward matching.
        result.push.apply(result, segmentCJK(part));
      } else {
        // Non-CJK segment (English, numbers, punctuation, etc.):
        // delegate to originalSplitQuery for proper tokenization.
        var nonCjkTokens = originalSplitQuery(part);
        for (var j = 0; j < nonCjkTokens.length; j++) {
          if (nonCjkTokens[j]) result.push(nonCjkTokens[j]);
        }
      }
    }
    return result;
  };

  // Split raw query into alternating CJK and non-CJK segments.
  // "分布式计算引擎" → ["分布式计算引擎"]
  // "中文API文档" → ["中文", "API", "文档"]
  // "openYuanrong 分布式计算" → ["openYuanrong ", "分布式计算"]
  function extractCJKFromRaw(text) {
    var parts = [];
    var current = "";
    var isCJK = text.length > 0 && CJK_CHAR.test(text.charAt(0));
    for (var i = 0; i < text.length; i++) {
      var ch = text.charAt(i);
      var chIsCJK = CJK_CHAR.test(ch);
      if (chIsCJK !== isCJK) {
        if (current) parts.push(current);
        current = ch;
        isCJK = chIsCJK;
      } else {
        current += ch;
      }
    }
    if (current) parts.push(current);
    return parts;
  }

  // Maximum forward matching against the dictionary.
  // "分布式计算引擎" → ["分布式", "计算", "引擎"]
  // Single chars not in the dict fall back to individual characters,
  // which Sphinx matches via term.indexOf(char) partial matching.
  function segmentCJK(text) {
    var chars = Array.from(text);
    var segments = [];
    var pos = 0;
    while (pos < chars.length) {
      var matched = false;
      for (var len = Math.min(maxLen, chars.length - pos); len >= 2; len--) {
        var word = chars.slice(pos, pos + len).join("");
        if (dictSet.has(word)) {
          segments.push(word);
          pos += len;
          matched = true;
          break;
        }
      }
      if (!matched) {
        segments.push(chars[pos]);
        pos++;
      }
    }
    return segments;
  }
})();
