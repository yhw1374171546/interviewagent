"""
面经库 + 轻量检索 (RAG 数据源)
==============================
一份「真实面经/参考答案」库，配合检索器做 RAG：

    - 面试结束生成参考答案时，检索与题目最相关的面经条目
    - 把「题库 expected_points 关键词兜底」升级为「真实面经内容」
    - 演示 LLM 应用必问的 RAG 能力（检索增强生成）

检索器设计: 零 LLM 依赖、零向量库依赖（ChromaDB 未装也能跑）——
用「关键词 Jaccard + 字符 n-gram 相似度」打分，纯 Python 实现，可离线测试。
安装 chromadb 后可以平替为向量检索（接口不变）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class QaEntry:
    """一条面经（问题 + 参考答案 + 检索标签）"""
    id: str
    question: str
    answer: str
    tags: list[str] = field(default_factory=list)


# ── 面经库（高频考点，答案均为面试级精简回答） ─────────────────

QA_ENTRIES: list[QaEntry] = [
    QaEntry(
        id="QA001", question="解释 Python 的 GIL（全局解释器锁）",
        tags=["python", "gil", "并发", "多线程"],
        answer="GIL 是 CPython 的全局解释器锁，保证同一时刻只有一个线程执行 Python 字节码。"
               "根因是 CPython 的内存管理（引用计数）不是线程安全的。CPU 密集型任务多线程会被 GIL 串行化，"
               "应改用 multiprocessing 或多进程；IO 密集型任务线程会释放 GIL，asyncio 协程更轻量。",
    ),
    QaEntry(
        id="QA002", question="为什么 MySQL 用 B+ 树做索引，而不是红黑树或 Hash",
        tags=["mysql", "索引", "b+树", "数据库"],
        answer="B+ 树矮胖、扇出大，单次磁盘 IO 能读出更多键，减少磁盘 IO 次数；"
               "叶子节点有序链表天然支持范围查询；内部节点只存键不存数据，可容纳更多键。"
               "Hash 只支持等值查询，不支持范围/排序；红黑树太高，磁盘 IO 次数多。",
    ),
    QaEntry(
        id="QA003", question="Redis 为什么这么快",
        tags=["redis", "缓存", "io多路复用", "性能"],
        answer="① 纯内存操作，无磁盘 IO；② 单线程避免锁竞争和上下文切换；"
               "③ IO 多路复用（epoll）支撑高并发连接；④ 高效数据结构（跳表/压缩列表/快速列表）；"
               "⑤ 渐进式 rehash 避免大键阻塞。",
    ),
    QaEntry(
        id="QA004", question="TCP 三次握手和四次挥手",
        tags=["tcp", "网络", "握手"],
        answer="三次握手：SYN → SYN+ACK → ACK，确认双方收发能力并同步初始序号。"
               "四次挥手：FIN → ACK → FIN → ACK，因为 TCP 是全双工，两端各自独立关闭发送方向。"
               "TIME_WAIT（2MSL）保证最后一个 ACK 可达、让旧报文在网络中消亡。",
    ),
    QaEntry(
        id="QA005", question="设计一个限流系统，常见限流算法有哪些",
        tags=["系统设计", "限流", "分布式"],
        answer="令牌桶：按速率生成令牌，请求消耗令牌，允许突发；漏桶：恒定速率流出，平滑流量；"
               "滑动窗口：按窗口内计数，比固定窗口更精确。分布式场景用 Redis + Lua 原子操作"
               "（INCR + EXPIRE）实现计数器/滑动窗口，保证原子性和一致性。",
    ),
    QaEntry(
        id="QA006", question="K8s 中服务频繁重启怎么排查",
        tags=["kubernetes", "k8s", "故障排查", "运维"],
        answer="先 kubectl describe pod 看事件（OOMKilled/拉取失败/健康检查失败），"
               "再 kubectl logs -f 看应用日志；检查就绪/存活探针配置是否过严、资源 limits 是否导致 OOM、"
               "镜像/配置是否有问题。按「事件 → 日志 → 配置 → 资源」四步排查。",
    ),
    QaEntry(
        id="QA007", question="Java HashMap 底层实现，1.8 为什么引入红黑树",
        tags=["java", "hashmap", "数据结构"],
        answer="数组 + 链表（1.8 后链表过长转红黑树）。hash 扰动降低碰撞，"
               "扩容时 rehash 到新数组。链表过长（≥8）转红黑树，把最坏查找从 O(n) 降到 O(log n)，"
               "防 hash 碰撞攻击导致的退化。线程不安全，并发场景用 ConcurrentHashMap。",
    ),
    QaEntry(
        id="QA008", question="JVM 内存模型，堆和栈存什么",
        tags=["java", "jvm", "内存"],
        answer="堆：对象实例和数组（GC 主战场），分新生代/老年代；"
               "虚拟机栈：每个线程一个栈，存局部变量/操作数栈/方法调用帧；"
               "方法区（元空间）：类元信息/常量池/静态变量；还有程序计数器和本地方法栈。",
    ),
    QaEntry(
        id="QA009", question="Kafka 消息不丢失怎么保证",
        tags=["kafka", "消息队列", "可靠性"],
        answer="生产端：ack=all + 重试，同步等待分区副本确认；"
               "Broker：副本机制（ISR）+ 消息落盘；消费端：手动提交 offset（先处理后提交），"
               "关闭自动提交避免消息未处理就提交。三端配合才能端到端不丢。",
    ),
    QaEntry(
        id="QA010", question="RAG（检索增强生成）的完整流程",
        tags=["rag", "llm", "大模型", "embedding"],
        answer="① 文档切分（Chunking，按语义边界/固定大小）；② Embedding 转向量入库（向量数据库）；"
               "③ 查询时把问题 Embedding 化，Top-K 检索最相似片段；④ 片段拼进 Prompt 给 LLM 生成答案。"
               "解决 LLM 知识过时/幻觉/私有知识问题，切分粒度影响召回质量。",
    ),
    QaEntry(
        id="QA011", question="雪花算法（Snowflake）原理，时钟回拨怎么办",
        tags=["分布式id", "雪花", "系统设计"],
        answer="64 位：1 位符号 + 41 位时间戳 + 10 位机器 ID + 12 位序列号，单机每毫秒可生成 4096 个 ID，"
               "趋势递增。时钟回拨会导致 ID 重复：回拨小时等时钟追上（或短时间等待），"
               "回拨大则拒绝服务或用备用方案（如号段模式 Leaf）。",
    ),
    QaEntry(
        id="QA012", question="进程、线程、协程的区别",
        tags=["操作系统", "进程", "线程", "协程"],
        answer="进程是资源分配单位，线程是 CPU 调度单位，协程是用户态调度。"
               "线程共享进程内存，切换由内核调度（有上下文切换开销）；协程切换在用户态，"
               "开销极小，适合 IO 密集；进程隔离性强、成本高。",
    ),
]


# ── 轻量检索器 ─────────────────────────────────────────────────

# 最低相关度阈值：过滤 n-gram 单个字符对重合带来的微小分数噪音
MIN_SCORE = 0.05


def _tokenize(text: str) -> set[str]:
    """分词：英文 token + 中文连续串（与评估器同思路）"""
    return set(re.findall(r"[a-zA-Z][a-zA-Z0-9+]*|[一-鿿]{2,}", text.lower()))


def _char_ngrams(text: str, n: int = 2) -> set[str]:
    text = text.lower()
    return {text[i:i + n] for i in range(max(0, len(text) - n + 1))}


class QaRetriever:
    """
    轻量面经检索器（关键词 Jaccard + 字符 n-gram，零依赖可离线）。

    检索增强生成（RAG）的「检索」环节——安装 chromadb 后可平替为向量检索，
    接口保持一致。
    """

    def __init__(self, entries: list[QaEntry] | None = None):
        self.entries = entries or QA_ENTRIES

    def retrieve(self, query: str, top_k: int = 3) -> list[dict]:
        """返回与 query 最相关的面经条目（含 score），无相关时返回空列表"""
        q_tokens = _tokenize(query)
        q_grams = _char_ngrams(query)
        if not q_tokens and not q_grams:
            return []

        scored = []
        for e in self.entries:
            score = self._score(query, q_tokens, q_grams, e)
            if score >= MIN_SCORE:
                scored.append((score, e))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"id": e.id, "question": e.question, "answer": e.answer,
             "tags": e.tags, "score": round(s, 3)}
            for s, e in scored[:top_k]
        ]

    def _score(self, query: str, q_tokens: set[str], q_grams: set[str], entry: QaEntry) -> float:
        """打分 = 0.6 × 词 Jaccard + 0.4 × 字符 n-gram Jaccard"""
        e_tokens = _tokenize(entry.question) | {t.lower() for t in entry.tags}
        union_tokens = q_tokens | e_tokens
        token_sim = len(q_tokens & e_tokens) / len(union_tokens) if union_tokens else 0.0

        e_grams = _char_ngrams(entry.question) | _char_ngrams(" ".join(entry.tags))
        union_grams = q_grams | e_grams
        gram_sim = len(q_grams & e_grams) / len(union_grams) if union_grams else 0.0

        return 0.6 * token_sim + 0.4 * gram_sim
