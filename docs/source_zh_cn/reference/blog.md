# 博客

欢迎阅读 openYuanrong 团队的技术博客！这里汇集了我们关于分布式计算、Serverless 架构、AI 基础设施等领域的深度技术分享。

## 文章分类

- [入门](blog-quickstart)
- [核心架构](blog-architecture)
- [函数系统](blog-functionsystem)
- [数据系统](blog-datasystem)
- [Agent](blog-agent)
- [论文](blog-paper)

(blog-quickstart)=

## 入门

<div class="blog-card" style="background: var(--color-canvas-default, white); border: 1px solid var(--color-border-default, #e1e4e8); border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; margin: 20px 0;" data-theme-light-bg="white" data-theme-dark-bg="#1e1e1e" data-theme-light-border="#e1e4e8" data-theme-dark-border="#404040">
<div style="font-size: 1.3em; font-weight: bold; margin-bottom: 10px; color: var(--color-accent-fg, #0366d6);">
📖 <a href="https://www.openeuler.openatom.cn/zh/blog/20260317-openYuanrong_07/20260317-openYuanrong_07.html" style="color: inherit; text-decoration: none;">openYuanrong 快速入门</a>
</div>
<p style="color: var(--color-fg-muted, #586069); margin: 10px 0; line-height: 1.6;">
快速上手 openYuanrong，开启您的分布式计算之旅！本文将带您快速了解 openYuanrong 的核心概念和基本使用方法，通过简单的示例帮助您快速入门。
</p>
<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; font-size: 0.85em;">
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">📅 2026-03-17</span>
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">⏱️ 8 分钟</span>
<span style="background: var(--color-success-subtle, #e1f4e1); color: var(--color-success-fg, #22863a); padding: 4px 8px; border-radius: 12px;">🏷️ 入门指南</span>
</div>
</div>

---

(blog-architecture)=

## 核心架构

<div class="blog-card" style="background: var(--color-canvas-default, white); border: 1px solid var(--color-border-default, #e1e4e8); border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; margin: 20px 0;" data-theme-light-bg="white" data-theme-dark-bg="#1e1e1e" data-theme-light-border="#e1e4e8" data-theme-dark-border="#404040">
<div style="font-size: 1.3em; font-weight: bold; margin-bottom: 10px; color: var(--color-accent-fg, #0366d6);">
📖 <a href="https://www.openeuler.openatom.cn/zh/blog/20260128-openYuanrong/20260128-openYuanrong.html" style="color: inherit; text-decoration: none;">把集群变"单机"（上）——openYuanrong 核心技术理念解析</a>
</div>
<p style="color: var(--color-fg-muted, #586069); margin: 10px 0; line-height: 1.6;">
深入解析 openYuanrong 的核心技术理念，了解如何将复杂的分布式集群简化为单机编程体验。
</p>
<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; font-size: 0.85em;">
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">📅 2026-01-28</span>
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">⏱️ 12 分钟</span>
<span style="background: var(--color-success-subtle, #e1f4e1); color: var(--color-success-fg, #22863a); padding: 4px 8px; border-radius: 12px;">🏷️ 架构设计</span>
</div>
</div>

<div class="blog-card" style="background: var(--color-canvas-default, white); border: 1px solid var(--color-border-default, #e1e4e8); border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; margin: 20px 0;" data-theme-light-bg="white" data-theme-dark-bg="#1e1e1e" data-theme-light-border="#e1e4e8" data-theme-dark-border="#404040">
<div style="font-size: 1.3em; font-weight: bold; margin-bottom: 10px; color: var(--color-accent-fg, #0366d6);">
📖 <a href="https://www.openeuler.openatom.cn/zh/blog/20260131-openYuanrong_02/20260131-openYuanrong_02.html" style="color: inherit; text-decoration: none;">把集群变"单机"（下）——openYuanrong 核心架构设计解析</a>
</div>
<p style="color: var(--color-fg-muted, #586069); margin: 10px 0; line-height: 1.6;">
继续深入探讨 openYuanrong 的核心架构设计，揭示其如何实现高性能分布式计算。
</p>
<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; font-size: 0.85em;">
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">📅 2026-01-31</span>
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">⏱️ 15 分钟</span>
<span style="background: var(--color-success-subtle, #e1f4e1); color: var(--color-success-fg, #22863a); padding: 4px 8px; border-radius: 12px;">🏷️ 架构设计</span>
</div>
</div>

<div class="blog-card" style="background: var(--color-canvas-default, white); border: 1px solid var(--color-border-default, #e1e4e8); border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; margin: 20px 0;" data-theme-light-bg="white" data-theme-dark-bg="#1e1e1e" data-theme-light-border="#e1e4e8" data-theme-dark-border="#404040">
<div style="font-size: 1.3em; font-weight: bold; margin-bottom: 10px; color: var(--color-accent-fg, #0366d6);">
📖 <a href="https://www.openeuler.openatom.cn/zh/blog/20260316-openYuanrong_06/20260316-openYuanrong_06.html" style="color: inherit; text-decoration: none;">破解分布式计算多租困局：openYuanrong 多租隔离</a>
</div>
<p style="color: var(--color-fg-muted, #586069); margin: 10px 0; line-height: 1.6;">
openYuanrong 作为一个通用的 Serverless 分布式计算引擎，在构建之初就充分考虑了多租隔离。支持多租户共享同一套集群，同时通过 DaemonSet 形式部署 FunctionProxy 和 DataWorker 组件，实现节点级别资源共享，有效加速租户工作负载的冷启动效率。
</p>
<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; font-size: 0.85em;">
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">📅 2026-03-16</span>
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">⏱️ 13 分钟</span>
<span style="background: var(--color-success-subtle, #e1f4e1); color: var(--color-success-fg, #22863a); padding: 4px 8px; border-radius: 12px;">🏷️ 架构设计</span>
</div>
</div>

---

(blog-functionsystem)=

## 函数系统

<div class="blog-card" style="background: var(--color-canvas-default, white); border: 1px solid var(--color-border-default, #e1e4e8); border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; margin: 20px 0;" data-theme-light-bg="white" data-theme-dark-bg="#1e1e1e" data-theme-light-border="#e1e4e8" data-theme-dark-border="#404040">
<div style="font-size: 1.3em; font-weight: bold; margin-bottom: 10px; color: var(--color-accent-fg, #0366d6);">
📖 <a href="https://www.openeuler.openatom.cn/zh/blog/20260225-openYuanrong_03/20260225-openYuanrong_03.html" style="color: inherit; text-decoration: none;">openYuanrong 函数系统：大规模分布式动态函数调度</a>
</div>
<p style="color: var(--color-fg-muted, #586069); margin: 10px 0; line-height: 1.6;">
深入了解 openYuanrong 如何实现大规模分布式环境下的动态函数调度，支持函数实例的极速弹性扩缩和跨节点迁移。
</p>
<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; font-size: 0.85em;">
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">📅 2026-02-25</span>
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">⏱️ 10 分钟</span>
<span style="background: var(--color-success-subtle, #e1f4e1); color: var(--color-success-fg, #22863a); padding: 4px 8px; border-radius: 12px;">🏷️ 函数系统</span>
</div>
</div>

---

(blog-datasystem)=

## 数据系统

<div class="blog-card" style="background: var(--color-canvas-default, white); border: 1px solid var(--color-border-default, #e1e4e8); border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; margin: 20px 0;" data-theme-light-bg="white" data-theme-dark-bg="#1e1e1e" data-theme-light-border="#e1e4e8" data-theme-dark-border="#404040">
<div style="font-size: 1.3em; font-weight: bold; margin-bottom: 10px; color: var(--color-accent-fg, #0366d6);">
📖 <a href="https://www.openeuler.openatom.cn/zh/blog/20260226-openYuanrong_04/20260226-openYuanrong_04.html" style="color: inherit; text-decoration: none;">openYuanrong 数据系统：近计算高性能分布式内存缓存</a>
</div>
<p style="color: var(--color-fg-muted, #586069); margin: 10px 0; line-height: 1.6;">
了解 openYuanrong 数据系统如何通过异构分布式多级缓存实现高性能数据共享。
</p>
<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; font-size: 0.85em;">
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">📅 2026-02-26</span>
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">⏱️ 11 分钟</span>
<span style="background: var(--color-success-subtle, #e1f4e1); color: var(--color-success-fg, #22863a); padding: 4px 8px; border-radius: 12px;">🏷️ 数据系统</span>
</div>
</div>

<div class="blog-card" style="background: var(--color-canvas-default, white); border: 1px solid var(--color-border-default, #e1e4e8); border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; margin: 20px 0;" data-theme-light-bg="white" data-theme-dark-bg="#1e1e1e" data-theme-light-border="#e1e4e8" data-theme-dark-border="#404040">
<div style="font-size: 1.3em; font-weight: bold; margin-bottom: 10px; color: var(--color-accent-fg, #0366d6);">
📖 <a href="https://www.openeuler.openatom.cn/zh/blog/20260316-openYuanrong_05/20260316-openYuanrong_05.html" style="color: inherit; text-decoration: none;">openYuanrong 数据流：近计算高性能分布式数据流转</a>
</div>
<p style="color: var(--color-fg-muted, #586069); margin: 10px 0; line-height: 1.6;">
探索 openYuanrong 数据流如何消除传统消息中间件中的冗余拷贝与中转开销。
</p>
<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; font-size: 0.85em;">
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">📅 2026-03-16</span>
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">⏱️ 9 分钟</span>
<span style="background: var(--color-success-subtle, #e1f4e1); color: var(--color-success-fg, #22863a); padding: 4px 8px; border-radius: 12px;">🏷️ 数据系统</span>
</div>
</div>

<div class="blog-card" style="background: var(--color-canvas-default, white); border: 1px solid var(--color-border-default, #e1e4e8); border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; margin: 20px 0;" data-theme-light-bg="white" data-theme-dark-bg="#1e1e1e" data-theme-light-border="#e1e4e8" data-theme-dark-border="#404040">
<div style="font-size: 1.3em; font-weight: bold; margin-bottom: 10px; color: var(--color-accent-fg, #0366d6);">
📖 <a href="https://www.openeuler.openatom.cn/zh/blog/20260407-openYuanrong_08/20260407-openYuanrong_08.html" style="color: inherit; text-decoration: none;">突破样本传输瓶颈：openYuanrong 作为 TransferQueue KV 后端加速 veRL 强化学习</a>
</div>
<p style="color: var(--color-fg-muted, #586069); margin: 10px 0; line-height: 1.6;">
深入了解 openYuanrong 如何作为 TransferQueue 的 KV 后端，依托面向昇腾平台的分布式异构对象多级缓存能力，加速 veRL 的样本传输链路，实现数据传输效率提升 3～4 倍，RL 端到端性能提升 40%。
</p>
<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; font-size: 0.85em;">
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">📅 2026-04-07</span>
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">⏱️ 14 分钟</span>
<span style="background: var(--color-success-subtle, #e1f4e1); color: var(--color-success-fg, #22863a); padding: 4px 8px; border-radius: 12px;">🏷️ 数据系统</span>
</div>
</div>

---

(blog-agent)=

## Agent

<div class="blog-card" style="background: var(--color-canvas-default, white); border: 1px solid var(--color-border-default, #e1e4e8); border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; margin: 20px 0;" data-theme-light-bg="white" data-theme-dark-bg="#1e1e1e" data-theme-light-border="#e1e4e8" data-theme-dark-border="#404040">
<div style="font-size: 1.3em; font-weight: bold; margin-bottom: 10px; color: var(--color-accent-fg, #0366d6);">
📖 <a href="https://www.openeuler.openatom.cn/zh/blog/20260803-openYuanrong-Agent/20260803-openYuanrong-Agent.html" style="color: inherit; text-decoration: none;">openYuanrong 打造 Agent 时代的企业级分布式底座</a>
</div>
<p style="color: var(--color-fg-muted, #586069); margin: 10px 0; line-height: 1.6;">
在 openYuanrong 最近发布的 0.8.0 和 0.9.0 两个大版本中，openYuanrong 大幅增强了面向 Agent 企业级应用的分布式能力，连同此前已具备的分布式内核基础能力，致力于打造 Agent 时代的企业级分布式底座。
</p>
<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; font-size: 0.85em;">
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">📅 2026-07-31</span>
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">⏱️ 15 分钟</span>
<span style="background: var(--color-success-subtle, #e1f4e1); color: var(--color-success-fg, #22863a); padding: 4px 8px; border-radius: 12px;">🏷️ Agent</span>
</div>
</div>

<div class="blog-card" style="background: var(--color-canvas-default, white); border: 1px solid var(--color-border-default, #e1e4e8); border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; margin: 20px 0;" data-theme-light-bg="white" data-theme-dark-bg="#1e1e1e" data-theme-light-border="#e1e4e8" data-theme-dark-border="#404040">
<div style="font-size: 1.3em; font-weight: bold; margin-bottom: 10px; color: var(--color-accent-fg, #0366d6);">
📖 <a href="https://www.openeuler.openatom.cn/zh/blog/20260525-openYuanrong_09/20260525-openYuanrong_09.html" style="color: inherit; text-decoration: none;">基于openYuanrong的企业级JiuwenSwarm实践探索</a>
</div>
<p style="color: var(--color-fg-muted, #586069); margin: 10px 0; line-height: 1.6;">
随着 AI Agent 应用从个人助理走向企业级生产部署，如何让 JiuwenSwarm 在保持现有编程体验的前提下，突破单机限制、具备企业级的弹性与可靠性，是 openYuanrong 要解决的核心工程问题。本文探讨如何基于 openYuanrong 对 JiuwenSwarm 进行分布式化改造，实现 AI Agent 从单机部署向集群化运行的平滑演进。
</p>
<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; font-size: 0.85em;">
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">📅 2026-05-25</span>
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">⏱️ 12 分钟</span>
<span style="background: var(--color-success-subtle, #e1f4e1); color: var(--color-success-fg, #22863a); padding: 4px 8px; border-radius: 12px;">🏷️ Agent</span>
</div>
</div>

<div class="blog-card" style="background: var(--color-canvas-default, white); border: 1px solid var(--color-border-default, #e1e4e8); border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; margin: 20px 0;" data-theme-light-bg="white" data-theme-dark-bg="#1e1e1e" data-theme-light-border="#e1e4e8" data-theme-dark-border="#404040">
<div style="font-size: 1.3em; font-weight: bold; margin-bottom: 10px; color: var(--color-accent-fg, #0366d6);">
📖 <a href="https://www.infoq.cn/article/qYQfpT8BaIPEkbeSXwzu" style="color: inherit; text-decoration: none;">Agent 时代需要怎样的分布式基础设施</a>
</div>
<p style="color: var(--color-fg-muted, #586069); margin: 10px 0; line-height: 1.6;">
Agent 应用的非确定性带来了高动态、不安全、长会话等独特运行特征和挑战，传统 K8s 容器微服务技术体系难以满足。openYuanrong 以类单机 OS 的分布式内核理念，天然支持 Agent 实例自动弹性、安全沙箱隔离、会话亲和调度与分布式容错，为 Agent 企业级大规模应用提供匹配的分布式基础设施。
</p>
<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; font-size: 0.85em;">
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">📅 2026-05-08</span>
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">⏱️ 18 分钟</span>
<span style="background: var(--color-success-subtle, #e1f4e1); color: var(--color-success-fg, #22863a); padding: 4px 8px; border-radius: 12px;">🏷️ Agent</span>
</div>
</div>

<div class="blog-card" style="background: var(--color-canvas-default, white); border: 1px solid var(--color-border-default, #e1e4e8); border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; margin: 20px 0;" data-theme-light-bg="white" data-theme-dark-bg="#1e1e1e" data-theme-light-border="#e1e4e8" data-theme-dark-border="#404040">
<div style="font-size: 1.3em; font-weight: bold; margin-bottom: 10px; color: var(--color-accent-fg, #0366d6);">
📖 <a href="https://openjiuwen.com/blogs/blog-artical?id=61" style="color: inherit; text-decoration: none;">openJiuwen × openYuanrong：打通 Agent Runtime 到 RL 训练的完整闭环</a>
</div>
<p style="color: var(--color-fg-muted, #586069); margin: 10px 0; line-height: 1.6;">
openJiuwen 联合 openYuanrong，打通了 Agent Runtime、openYuanrong、RL 训练框架（verl）与昇腾算力之间的全链路。Agent 每次真实运行产生的轨迹会被结构化为可学习样本，直接进入 GRPO 训练循环，在昇腾环境上完成推理和训练，实现从"运行轨迹"到"策略更新"的自动化闭环路径。
</p>
<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; font-size: 0.85em;">
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">📅 2026-04-08</span>
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">⏱️ 15 分钟</span>
<span style="background: var(--color-success-subtle, #e1f4e1); color: var(--color-success-fg, #22863a); padding: 4px 8px; border-radius: 12px;">🏷️ Agent</span>
</div>
</div>

---

(blog-paper)=

## 论文

<div class="blog-card" style="background: var(--color-canvas-default, white); border: 1px solid var(--color-border-default, #e1e4e8); border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; margin: 20px 0;" data-theme-light-bg="white" data-theme-dark-bg="#1e1e1e" data-theme-light-border="#e1e4e8" data-theme-dark-border="#404040">
<div style="font-size: 1.3em; font-weight: bold; margin-bottom: 10px; color: var(--color-accent-fg, #0366d6);">
📖 <a href="https://dl.acm.org/doi/10.1145/3651890.3672216" style="color: inherit; text-decoration: none;">YuanRong: A Production General-purpose Serverless System for Distributed Applications in the Cloud</a>
</div>
<p style="color: var(--color-fg-muted, #586069); margin: 10px 0; line-height: 1.6;">
We design, implement, and evaluate YuanRong, the first production general-purpose serverless platform with a unified programming interface, multi-language runtime, and a distributed computing kernel for cloud-based applications.
</p>
<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; font-size: 0.85em;">
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">📅 2024-08-04</span>
<span style="background: var(--color-accent-subtle, #f1f8ff); color: var(--color-accent-fg, #0366d6); padding: 4px 8px; border-radius: 12px;">⏱️ 30 分钟</span>
<span style="background: var(--color-success-subtle, #e1f4e1); color: var(--color-success-fg, #22863a); padding: 4px 8px; border-radius: 12px;">🏷️ 论文</span>
</div>
</div>

---

<script>
// 深色模式适配
(function() {
    function updateTheme() {
        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        document.querySelectorAll('.blog-card').forEach(function(card) {
            if (isDark) {
                card.style.background = card.getAttribute('data-theme-dark-bg') || '#1e1e1e';
                card.style.borderColor = card.getAttribute('data-theme-dark-border') || '#404040';
            } else {
                card.style.background = card.getAttribute('data-theme-light-bg') || 'white';
                card.style.borderColor = card.getAttribute('data-theme-light-border') || '#e1e4e8';
            }
        });
    }
    
    // 初始检查
    updateTheme();
    
    // 监听主题变化
    const observer = new MutationObserver(updateTheme);
    observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['data-theme']
    });
})();
</script>
