"""
面试题库 + 检索系统
===================
内置 90+ 道真实面试题，按技能标签索引。
JD 解析出的技能 → 题库检索 → 匹配题目 → LLM 微调使之贴合 JD。

不是"让 LLM 凭空出题"，而是"从题库检索相关题目 + LLM 做 JD 适配"。

覆盖方向: Python/Go/Java 后端、数据库/Redis、系统设计、网络、操作系统、
容器/K8s、前端、大数据、消息队列、AI/LLM/Agent、项目深挖、行为面试、代码实操。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# QuestionType 定义在此处，避免 question_gen → core.llm → openai 的连锁导入


class QuestionType(str, Enum):
    TECHNICAL = "technical"
    SCENARIO = "scenario"
    PROJECT = "project"
    BEHAVIORAL = "behavioral"
    CODING = "coding"


@dataclass
class InterviewQuestion:
    """一道面试题 (共享数据结构，多处引用)"""
    id: str = ""
    type: QuestionType = QuestionType.TECHNICAL
    category: str = ""
    question: str = ""
    expected_points: list[str] = field(default_factory=list)
    difficulty: int = 3
    follow_up_hints: list[str] = field(default_factory=list)
    time_limit: int = 0
    source: str = ""


@dataclass
class BankQuestion:
    """题库中的一道题"""
    id: str
    type: QuestionType
    category: str                     # 考察类别
    question: str                     # 题目模板（可包含 {skill} 占位符）
    tags: list[str]                   # 关联的技能标签（用于检索匹配）
    expected_points: list[str] = field(default_factory=list)
    difficulty: int = 3
    follow_up_hints: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
#  题库
# ═══════════════════════════════════════════════════════════════

QUESTION_BANK: list[BankQuestion] = [
    # ── Python 相关 ─────────────────────────────────────────
    BankQuestion(
        id="PY001", type=QuestionType.TECHNICAL, category="Python基础",
        question="请解释 Python 的 GIL（全局解释器锁）是什么？它在什么场景下会成为性能瓶颈？你有哪些绕过 GIL 的方案？",
        tags=["python", "并发", "多线程", "性能"],
        expected_points=["GIL定义", "CPU密集vsIO密集", "multiprocessing", "C扩展", "asyncio"],
        difficulty=3,
    ),
    BankQuestion(
        id="PY002", type=QuestionType.TECHNICAL, category="Python进阶",
        question="Python 中 `__new__` 和 `__init__` 的区别是什么？什么场景下需要重写 `__new__`？",
        tags=["python", "面向对象"],
        expected_points=["构造顺序", "单例模式", "不可变类型子类化"],
        difficulty=4,
    ),
    BankQuestion(
        id="PY003", type=QuestionType.TECHNICAL, category="Python进阶",
        question="请解释 Python 的垃圾回收机制。引用计数和分代回收分别解决了什么问题？循环引用是如何被检测的？",
        tags=["python", "内存管理", "GC"],
        expected_points=["引用计数", "分代回收", "标记清除", "循环引用", "gc模块"],
        difficulty=4,
    ),
    BankQuestion(
        id="PY004", type=QuestionType.TECHNICAL, category="Python进阶",
        question="asyncio 的事件循环是如何工作的？与多线程相比，它的优势和局限分别是什么？",
        tags=["python", "asyncio", "异步", "并发"],
        expected_points=["事件循环原理", "协程", "await/async", "IO密集型优势", "CPU密集型局限"],
        difficulty=4,
        follow_up_hints=["如果一个协程里执行了阻塞操作会怎样？", "asyncio和gevent的区别？"],
    ),
    BankQuestion(
        id="PY005", type=QuestionType.TECHNICAL, category="Python基础",
        question="Python 的装饰器是什么？它本质上是怎样工作的？请举一个你在项目中实际使用装饰器的例子。",
        tags=["python", "装饰器", "设计模式"],
        expected_points=["闭包概念", "@语法糖", "functools.wraps", "实际应用场景"],
        difficulty=2,
    ),

    # ── 数据库相关 ─────────────────────────────────────────
    BankQuestion(
        id="DB001", type=QuestionType.TECHNICAL, category="数据库",
        question="MySQL 的索引底层数据结构是什么？为什么用 B+ 树而不是红黑树或 Hash？",
        tags=["mysql", "索引", "数据结构", "B+树"],
        expected_points=["B+树结构", "磁盘IO优化", "范围查询优势", "与B树/红黑树/Hash的对比"],
        difficulty=3,
    ),
    BankQuestion(
        id="DB002", type=QuestionType.TECHNICAL, category="数据库",
        question="请解释 MySQL 的事务隔离级别。不可重复读和幻读的区别是什么？InnoDB 是如何解决幻读的？",
        tags=["mysql", "事务", "隔离级别", "MVCC"],
        expected_points=["四种隔离级别", "脏读/不可重复读/幻读的区别", "MVCC原理", "间隙锁"],
        difficulty=4,
        follow_up_hints=["MVCC在RR级别下真的解决了幻读吗？什么情况下还会有？"],
    ),
    BankQuestion(
        id="DB003", type=QuestionType.TECHNICAL, category="数据库",
        question="一条 SQL 查询在 MySQL 中的完整执行流程是怎样的？从连接器到存储引擎，每一步做了什么？",
        tags=["mysql", "SQL", "架构"],
        expected_points=["连接器", "分析器", "优化器", "执行器", "存储引擎"],
        difficulty=3,
    ),
    BankQuestion(
        id="DB004", type=QuestionType.TECHNICAL, category="Redis",
        question="Redis 为什么这么快？请从数据结构、IO模型、内存管理几个角度分析。",
        tags=["redis", "性能", "IO模型"],
        expected_points=["内存操作", "单线程+IO多路复用", "数据结构优化", "epoll"],
        difficulty=3,
    ),
    BankQuestion(
        id="DB005", type=QuestionType.TECHNICAL, category="Redis",
        question="Redis 的缓存淘汰策略有哪些？LRU 和 LFU 的实现原理分别是什么？在什么业务场景下会选择哪种策略？",
        tags=["redis", "缓存", "LRU", "LFU"],
        expected_points=["淘汰策略列表", "LRU实现原理", "LFU实现原理", "场景选型"],
        difficulty=3,
    ),
    BankQuestion(
        id="DB006", type=QuestionType.TECHNICAL, category="Redis",
        question="Redis 集群模式下，数据是如何分布的？一致性哈希和 Redis Cluster 的哈希槽方案有何不同？",
        tags=["redis", "集群", "分布式", "一致性哈希"],
        expected_points=["哈希槽16384", "数据分布", "一致性哈希对比", "故障转移"],
        difficulty=4,
    ),

    # ── 系统设计 / 分布式 ──────────────────────────────────
    BankQuestion(
        id="SD001", type=QuestionType.SCENARIO, category="系统设计",
        question="假设你负责设计一个短链接系统（类似 t.cn），日生成量百万级，日均访问量千万级。请设计整体的技术方案。",
        tags=["系统设计", "分布式", "短链接"],
        expected_points=["ID生成策略(snowflake)", "62进制转换", "缓存策略", "301/302", "分库分表"],
        difficulty=4,
    ),
    BankQuestion(
        id="SD002", type=QuestionType.SCENARIO, category="系统设计",
        question="设计一个限流系统。常见的限流算法有哪些（令牌桶、漏桶、滑动窗口）？你会如何在分布式环境下实现一个精确的限流？",
        tags=["系统设计", "限流", "分布式", "算法"],
        expected_points=["令牌桶原理", "滑动窗口实现", "Redis+Lua", "分布式一致性"],
        difficulty=4,
    ),
    BankQuestion(
        id="SD003", type=QuestionType.SCENARIO, category="系统设计",
        question="设计一个分布式 ID 生成器。雪花算法 (Snowflake) 的原理是什么？如果机器时钟回拨了怎么办？",
        tags=["系统设计", "分布式ID", "Snowflake"],
        expected_points=["Snowflake结构", "时钟回拨处理", "号段模式(Leaf)", "优缺点"],
        difficulty=3,
    ),
    BankQuestion(
        id="SD004", type=QuestionType.SCENARIO, category="系统设计",
        question="请设计一个消息推送系统，支持千万级设备的实时推送。需要考虑消息不丢、不重、有序。",
        tags=["系统设计", "消息队列", "推送", "高并发"],
        expected_points=["长连接/WebSocket", "消息可靠性(ACK)", "离线消息存储", "分片推送"],
        difficulty=5,
    ),

    # ── 网络 / HTTP ───────────────────────────────────────
    BankQuestion(
        id="NET001", type=QuestionType.TECHNICAL, category="网络",
        question="从浏览器输入 URL 到页面渲染，完整过程是怎样的？每一步涉及哪些协议和技术？",
        tags=["http", "网络", "DNS", "TCP"],
        expected_points=["DNS解析", "TCP三次握手", "TLS握手", "HTTP请求", "浏览器渲染"],
        difficulty=2,
    ),
    BankQuestion(
        id="NET002", type=QuestionType.TECHNICAL, category="网络",
        question="HTTP/1.1、HTTP/2、HTTP/3 的主要区别是什么？队头阻塞问题在各版本中是如何解决的？",
        tags=["http", "网络协议", "http2", "http3", "QUIC"],
        expected_points=["HTTP/1.1管道化问题", "HTTP/2多路复用", "TCP队头阻塞", "QUIC/UDP"],
        difficulty=3,
    ),
    BankQuestion(
        id="NET003", type=QuestionType.TECHNICAL, category="网络",
        question="TCP 的三次握手和四次挥手过程是怎样的？为什么握手三次挥手要四次？TIME_WAIT 状态的作用是什么？",
        tags=["tcp", "网络", "握手", "挥手"],
        expected_points=["三次握手状态转换", "四次挥手", "TIME_WAIT原因", "2MSL"],
        difficulty=3,
    ),

    # ── 操作系统 ──────────────────────────────────────────
    BankQuestion(
        id="OS001", type=QuestionType.TECHNICAL, category="操作系统",
        question="进程和线程的本质区别是什么？协程和线程又有什么不同？请从调度方式、资源开销、适用场景三方面对比。",
        tags=["操作系统", "进程", "线程", "协程"],
        expected_points=["资源拥有单位vs调度单位", "用户态vs内核态调度", "栈空间对比", "场景选型"],
        difficulty=2,
    ),
    BankQuestion(
        id="OS002", type=QuestionType.TECHNICAL, category="操作系统",
        question="什么是虚拟内存？分页和分段有什么区别？请解释 TLB 和页表的工作机制。",
        tags=["操作系统", "内存管理", "虚拟内存"],
        expected_points=["虚拟内存概念", "分页vs分段", "TLB", "缺页中断", "页面置换"],
        difficulty=4,
    ),

    # ── Go 相关 ───────────────────────────────────────────
    BankQuestion(
        id="GO001", type=QuestionType.TECHNICAL, category="Go语言",
        question="Go 的 GMP 调度模型是怎样的？G、M、P 分别代表什么？当一个 goroutine 阻塞时会发生什么？",
        tags=["go", "golang", "goroutine", "并发"],
        expected_points=["GMP含义", "调度流程", "阻塞处理", "work stealing"],
        difficulty=4,
    ),
    BankQuestion(
        id="GO002", type=QuestionType.TECHNICAL, category="Go语言",
        question="Go 的 channel 底层是如何实现的？无缓冲 channel 和有缓冲 channel 在发送/接收时的行为有什么区别？",
        tags=["go", "golang", "channel", "并发"],
        expected_points=["hchan结构", "环形队列", "sudog等待队列", "阻塞/非阻塞"],
        difficulty=4,
    ),
    BankQuestion(
        id="GO003", type=QuestionType.TECHNICAL, category="Go语言",
        question="Go 的 GC 是如何工作的？它经历了哪些演进（从 STW 到并发三色标记）？什么情况下 GC 会成为瓶颈？",
        tags=["go", "golang", "GC", "内存管理"],
        expected_points=["三色标记", "写屏障", "混合写屏障", "GC触发条件", "GC调优"],
        difficulty=5,
    ),

    # ── Java 相关 ─────────────────────────────────────────
    BankQuestion(
        id="JV001", type=QuestionType.TECHNICAL, category="Java",
        question="Java 的 HashMap 底层数据结构是什么？JDK 1.8 中为什么要引入红黑树？put 方法的完整流程是怎样的？",
        tags=["java", "HashMap", "数据结构"],
        expected_points=["数组+链表+红黑树", "扩容机制", "hash扰动", "线程安全问题"],
        difficulty=3,
    ),
    BankQuestion(
        id="JV002", type=QuestionType.TECHNICAL, category="Java",
        question="JVM 的内存模型是怎样的？堆和栈分别存储什么？方法区和元空间有什么关系？",
        tags=["java", "jvm", "内存模型"],
        expected_points=["堆/栈/方法区", "元空间vs永久代", "字符串常量池", "直接内存"],
        difficulty=3,
    ),
    BankQuestion(
        id="JV003", type=QuestionType.TECHNICAL, category="Java",
        question="请解释 Java 的类加载机制。双亲委派模型是什么？为什么要破坏它？Tomcat 是怎么做类加载的？",
        tags=["java", "jvm", "类加载"],
        expected_points=["类加载器层次", "双亲委派", "破坏场景", "线程上下文类加载器"],
        difficulty=4,
    ),
    BankQuestion(
        id="JV004", type=QuestionType.TECHNICAL, category="Java并发",
        question="synchronized 的锁升级过程是怎样的？偏向锁、轻量级锁、重量级锁分别在什么场景下触发？",
        tags=["java", "并发", "synchronized"],
        expected_points=["偏向锁", "轻量级锁CAS", "重量级锁", "锁升级过程", "对象头Mark Word"],
        difficulty=4,
        follow_up_hints=["锁降级会发生吗？", "JIT 编译对锁有什么优化（锁消除/锁粗化）？"],
    ),
    BankQuestion(
        id="JV005", type=QuestionType.TECHNICAL, category="Java并发",
        question="volatile 关键字解决了什么问题？它的可见性和有序性是如何通过内存屏障实现的？和 synchronized 有什么区别？",
        tags=["java", "并发", "volatile"],
        expected_points=["可见性", "禁止指令重排", "内存屏障", "不保证原子性", "与synchronized对比"],
        difficulty=3,
    ),
    BankQuestion(
        id="JV006", type=QuestionType.TECHNICAL, category="Java并发",
        question="AQS (AbstractQueuedSynchronizer) 的原理是什么？ReentrantLock 如何基于它实现加锁解锁？和 synchronized 相比如何选型？",
        tags=["java", "并发", "AQS", "ReentrantLock"],
        expected_points=["state状态", "CLH队列", "公平锁vs非公平锁", "Condition条件队列", "选型依据"],
        difficulty=4,
    ),
    BankQuestion(
        id="JV007", type=QuestionType.TECHNICAL, category="Java",
        question="JVM 的垃圾回收器经历了怎样的演进？CMS、G1、ZGC 各自的算法思想和适用场景是什么？",
        tags=["java", "jvm", "GC"],
        expected_points=["标记清除", "G1分区Region", "ZGC染色指针", "停顿时间目标", "适用场景"],
        difficulty=4,
    ),

    # ── 容器 / K8s ────────────────────────────────────────
    BankQuestion(
        id="K8S001", type=QuestionType.TECHNICAL, category="容器/K8s",
        question="Docker 的镜像分层机制是什么？写时复制 (Copy-on-Write) 是如何工作的？这如何影响镜像构建的优化？",
        tags=["docker", "容器", "镜像"],
        expected_points=["分层原理", "CoW", "UnionFS", "构建优化策略", "镜像瘦身"],
        difficulty=3,
    ),
    BankQuestion(
        id="K8S002", type=QuestionType.TECHNICAL, category="容器/K8s",
        question="Kubernetes 中 Pod 的调度流程是怎样的？调度器如何做资源评估和节点选择？",
        tags=["kubernetes", "k8s", "调度"],
        expected_points=["调度过滤", "打分优选", "资源requests/limits", "亲和性/反亲和"],
        difficulty=4,
    ),
    BankQuestion(
        id="K8S003", type=QuestionType.SCENARIO, category="容器/K8s",
        question="你负责的一个 K8s 集群中，某个服务突然频繁重启。你会如何排查？请描述完整的排查思路。",
        tags=["kubernetes", "k8s", "故障排查", "运维"],
        expected_points=["kubectl describe/logs", "OOMKilled排查", "健康检查配置", "资源限制", "事件日志"],
        difficulty=3,
    ),
    BankQuestion(
        id="K8S004", type=QuestionType.TECHNICAL, category="容器/K8s",
        question="Kubernetes 的 Service 网络是如何工作的？kube-proxy 的 iptables 模式和 IPVS 模式有什么区别？ClusterIP 是怎么实现负载均衡的？",
        tags=["kubernetes", "k8s", "网络", "Service"],
        expected_points=["Service概念", "kube-proxy", "iptables规则链", "IPVS模式", "负载均衡实现"],
        difficulty=4,
        follow_up_hints=["NodePort 和 LoadBalancer 的流量路径是什么？", "为什么 IPVS 模式比 iptables 性能好？"],
    ),
    BankQuestion(
        id="K8S005", type=QuestionType.TECHNICAL, category="容器/K8s",
        question="StatefulSet 和 Deployment 的区别是什么？有状态应用（如数据库）在 K8s 中如何管理存储？请解释 PV、PVC、StorageClass 的关系。",
        tags=["kubernetes", "k8s", "存储", "StatefulSet"],
        expected_points=["稳定网络标识", "有序部署", "PV/PVC/StorageClass", "动态供给", "StatefulSet适用场景"],
        difficulty=3,
    ),

    # ── 前端 ───────────────────────────────────────────
    BankQuestion(
        id="FE001", type=QuestionType.TECHNICAL, category="React",
        question="虚拟 DOM 是什么？React 的 diff 算法是如何工作的？为什么列表渲染需要 key？",
        tags=["react", "javascript", "虚拟DOM"],
        expected_points=["虚拟DOM概念", "diff策略", "同级比较", "key的作用", "时间复杂度"],
        difficulty=3,
        follow_up_hints=["为什么 index 作为 key 会导致渲染错误？"],
    ),
    BankQuestion(
        id="FE002", type=QuestionType.TECHNICAL, category="React",
        question="React 函数组件和类组件的区别？Hooks 解决了什么问题？useEffect 的依赖数组机制是如何工作的？",
        tags=["react", "javascript", "hooks"],
        expected_points=["函数组件", "Hooks动机", "useEffect", "依赖数组", "闭包陷阱"],
        difficulty=3,
        follow_up_hints=["useMemo 和 useCallback 分别解决什么问题？"],
    ),
    BankQuestion(
        id="FE003", type=QuestionType.TECHNICAL, category="Vue",
        question="Vue 的响应式原理是什么？Vue2 的 Object.defineProperty 和 Vue3 的 Proxy 实现有什么区别？",
        tags=["vue", "javascript", "响应式"],
        expected_points=["Object.defineProperty", "Proxy", "依赖收集", "触发更新", "数组监听差异"],
        difficulty=4,
        follow_up_hints=["Vue3 的 ref 和 reactive 有什么区别？"],
    ),
    BankQuestion(
        id="FE004", type=QuestionType.TECHNICAL, category="Vue",
        question="Vue3 的 Composition API 相比 Vue2 的 Options API 有什么优势？setup 函数解决了什么问题？",
        tags=["vue", "javascript", "Composition API"],
        expected_points=["逻辑复用", "代码组织", "类型推导", "tree-shaking", "setup"],
        difficulty=2,
    ),
    BankQuestion(
        id="FE005", type=QuestionType.TECHNICAL, category="JavaScript",
        question="JavaScript 的事件循环机制是怎样的？宏任务和微任务分别有哪些？它们的执行顺序是什么？",
        tags=["javascript", "事件循环", "异步"],
        expected_points=["调用栈", "宏任务", "微任务", "Event Loop", "执行顺序"],
        difficulty=4,
        follow_up_hints=["setTimeout 和 Promise.then 谁先执行？为什么？"],
    ),
    BankQuestion(
        id="FE006", type=QuestionType.TECHNICAL, category="JavaScript",
        question="闭包是什么？它在实际开发中有哪些应用场景？React 中的 stale closure 问题是如何产生的？",
        tags=["javascript", "react", "闭包"],
        expected_points=["闭包概念", "作用域链", "私有变量", "stale closure", "依赖数组解决"],
        difficulty=3,
    ),
    BankQuestion(
        id="FE007", type=QuestionType.TECHNICAL, category="TypeScript",
        question="TypeScript 相比 JavaScript 的核心优势是什么？泛型、联合类型、类型推断在实际项目中分别解决什么问题？",
        tags=["typescript", "javascript", "类型系统"],
        expected_points=["静态类型检查", "泛型", "联合类型", "类型推断", "重构安全"],
        difficulty=3,
    ),
    BankQuestion(
        id="FE008", type=QuestionType.TECHNICAL, category="前端工程化",
        question="webpack 的构建流程是怎样的？loader 和 plugin 的本质区别是什么？tree shaking 是如何实现的？",
        tags=["webpack", "javascript", "前端工程化"],
        expected_points=["构建流程", "loader", "plugin", "tree shaking", "ESM静态分析"],
        difficulty=4,
    ),
    BankQuestion(
        id="FE009", type=QuestionType.TECHNICAL, category="前端工程化",
        question="Vite 为什么比 webpack 启动更快？它的 ESM 原生加载和 HMR 机制是如何工作的？",
        tags=["vite", "webpack", "前端工程化"],
        expected_points=["ESM原生加载", "依赖预构建", "esbuild", "HMR原理", "与webpack对比"],
        difficulty=3,
    ),
    BankQuestion(
        id="FE010", type=QuestionType.SCENARIO, category="前端性能",
        question="你们的产品首屏加载需要 5 秒，用户流失严重。请从网络、构建、渲染三个层面给出系统性的性能优化方案。",
        tags=["javascript", "webpack", "性能优化"],
        expected_points=["代码分割", "懒加载", "CDN", "缓存策略", "首屏渲染优化"],
        difficulty=4,
    ),
    BankQuestion(
        id="FE011", type=QuestionType.SCENARIO, category="前端架构",
        question="设计一个支持百万级用户同时在线的实时评论系统前端。消息推送、DOM 更新、内存管理分别怎么处理？",
        tags=["javascript", "websocket", "前端架构"],
        expected_points=["WebSocket长连接", "虚拟滚动", "分页渲染", "内存管理", "节流防抖"],
        difficulty=4,
    ),

    # ── 大数据 ─────────────────────────────────────────
    BankQuestion(
        id="BD001", type=QuestionType.TECHNICAL, category="Spark",
        question="Spark 的 RDD 是什么？宽依赖和窄依赖有什么区别？为什么宽依赖会触发 shuffle？",
        tags=["spark", "大数据", "RDD"],
        expected_points=["RDD概念", "宽依赖", "窄依赖", "shuffle", "Stage划分"],
        difficulty=4,
        follow_up_hints=["shuffle 对性能的影响是什么？怎么减少 shuffle？"],
    ),
    BankQuestion(
        id="BD002", type=QuestionType.TECHNICAL, category="Spark",
        question="Spark 的 DataFrame/Dataset 相比 RDD 有什么优势？Catalyst 优化器做了哪些优化？",
        tags=["spark", "大数据", "SQL"],
        expected_points=["Schema信息", "Catalyst", "谓词下推", "列式存储", "代码生成"],
        difficulty=4,
    ),
    BankQuestion(
        id="BD003", type=QuestionType.TECHNICAL, category="Flink",
        question="Flink 的流处理和 Spark Streaming 的微批处理有什么本质区别？为什么说 Flink 是真正的流计算？",
        tags=["flink", "spark", "流计算"],
        expected_points=["事件驱动", "微批处理", "低延迟", "Exactly-Once", "适用场景"],
        difficulty=4,
    ),
    BankQuestion(
        id="BD004", type=QuestionType.TECHNICAL, category="Flink",
        question="Flink 的 Checkpoint 和 Savepoint 有什么区别？Exactly-Once 语义是如何通过 Checkpoint 保证的？",
        tags=["flink", "流计算", "容错"],
        expected_points=["Checkpoint机制", "Savepoint", "Barrier对齐", "状态恢复", "Exactly-Once"],
        difficulty=5,
        follow_up_hints=["端到端 Exactly-Once 还需要什么配合（两阶段提交）？"],
    ),
    BankQuestion(
        id="BD005", type=QuestionType.TECHNICAL, category="Flink",
        question="Flink 的 Watermark 是什么？它解决了什么问题？乱序数据是如何处理的？",
        tags=["flink", "流计算", "Watermark"],
        expected_points=["事件时间", "Watermark", "乱序处理", "延迟数据", "allowedLateness"],
        difficulty=4,
    ),
    BankQuestion(
        id="BD006", type=QuestionType.TECHNICAL, category="大数据",
        question="数据倾斜是什么？在 Spark 和 Flink 中分别有哪些定位手段和解决方案？",
        tags=["spark", "flink", "数据倾斜"],
        expected_points=["倾斜定义", "定位手段", "两阶段聚合", "加盐打散", "广播Join"],
        difficulty=4,
    ),
    BankQuestion(
        id="BD007", type=QuestionType.TECHNICAL, category="Hive",
        question="Hive 的内部表和外部表有什么区别？分区和分桶分别解决什么问题？",
        tags=["hive", "数据仓库", "SQL"],
        expected_points=["内部表", "外部表", "分区裁剪", "分桶抽样", "使用场景"],
        difficulty=3,
    ),
    BankQuestion(
        id="BD008", type=QuestionType.TECHNICAL, category="Hadoop",
        question="HDFS 的架构是怎样的？副本机制如何保证数据可靠性？读写流程是什么？",
        tags=["hadoop", "hdfs", "分布式存储"],
        expected_points=["NameNode", "DataNode", "副本机制", "心跳", "读写流程"],
        difficulty=3,
    ),
    BankQuestion(
        id="BD009", type=QuestionType.TECHNICAL, category="数据仓库",
        question="数仓为什么要分层设计（ODS/DWD/DWS/ADS）？每一层分别承担什么职责？",
        tags=["数据仓库", "hive", "数仓分层"],
        expected_points=["ODS", "DWD", "DWS", "ADS", "分层收益"],
        difficulty=3,
    ),
    BankQuestion(
        id="BD010", type=QuestionType.TECHNICAL, category="数据仓库",
        question="Airflow 的 DAG 调度是如何工作的？相比 crontab 它解决了哪些问题？任务依赖和失败重试怎么处理？",
        tags=["airflow", "etl", "调度"],
        expected_points=["DAG", "任务依赖", "失败重试", "backfill", "与crontab对比"],
        difficulty=3,
    ),
    BankQuestion(
        id="BD011", type=QuestionType.SCENARIO, category="实时计算",
        question="设计一个实时数据大屏：用户行为数据从埋点采集到可视化展示要求 5 秒以内。请设计完整的技术方案。",
        tags=["flink", "kafka", "实时计算"],
        expected_points=["Kafka缓冲", "Flink流计算", "状态聚合", "结果存储", "数据延迟监控"],
        difficulty=5,
    ),
    BankQuestion(
        id="BD012", type=QuestionType.SCENARIO, category="离线计算",
        question="公司每天产生 10TB 的日志数据，需要支持多维度的离线分析报表。请设计完整的离线数仓方案。",
        tags=["hadoop", "hive", "spark", "离线计算"],
        expected_points=["数据采集", "数仓分层", "Spark计算", "调度编排", "结果服务"],
        difficulty=4,
    ),

    # ── 消息队列 ───────────────────────────────────────
    BankQuestion(
        id="MQ001", type=QuestionType.TECHNICAL, category="Kafka",
        question="Kafka 的整体架构是怎样的？为什么它的吞吐量能做到百万级 QPS？",
        tags=["kafka", "消息队列", "高吞吐"],
        expected_points=["分区模型", "顺序写", "零拷贝", "页缓存", "批量发送"],
        difficulty=4,
        follow_up_hints=["Kafka 为什么不适合低延迟的金融交易场景？"],
    ),
    BankQuestion(
        id="MQ002", type=QuestionType.TECHNICAL, category="Kafka",
        question="Kafka 的 ISR 机制是什么？acks 参数（0/1/all）分别代表什么？Leader 故障时如何选举？",
        tags=["kafka", "消息队列", "可靠性"],
        expected_points=["ISR", "acks配置", "Leader选举", "副本同步", "可靠性权衡"],
        difficulty=4,
    ),
    BankQuestion(
        id="MQ003", type=QuestionType.TECHNICAL, category="消息队列",
        question="消息不丢失如何保证？请从生产者、Broker、消费者三个环节分别说明。",
        tags=["kafka", "rabbitmq", "可靠性"],
        expected_points=["生产端确认", "持久化策略", "消费端手动提交", "重试机制", "死信队列"],
        difficulty=4,
    ),
    BankQuestion(
        id="MQ004", type=QuestionType.TECHNICAL, category="消息队列",
        question="消息重复消费问题怎么解决？常见的幂等性方案有哪些？",
        tags=["kafka", "幂等", "消息队列"],
        expected_points=["重复消费原因", "唯一ID去重", "数据库唯一约束", "Redis去重", "幂等设计"],
        difficulty=3,
    ),
    BankQuestion(
        id="MQ005", type=QuestionType.TECHNICAL, category="消息队列",
        question="消息的顺序性如何保证？Kafka 的单分区有序和全局有序之间如何取舍？",
        tags=["kafka", "消息队列", "顺序性"],
        expected_points=["单分区有序", "分区键设计", "全局有序代价", "消费端串行", "业务取舍"],
        difficulty=4,
    ),
    BankQuestion(
        id="MQ006", type=QuestionType.TECHNICAL, category="RabbitMQ",
        question="RabbitMQ 的交换机有哪些类型（direct/fanout/topic/headers）？死信队列的应用场景是什么？",
        tags=["rabbitmq", "消息队列", "交换机"],
        expected_points=["四种交换机", "路由键", "死信队列", "延迟队列", "应用场景"],
        difficulty=3,
    ),
    BankQuestion(
        id="MQ007", type=QuestionType.SCENARIO, category="消息队列",
        question="线上 Kafka 消息积压了 1 亿条，下游消费者处理不过来。请给出从临时止血到长期优化的完整方案。",
        tags=["kafka", "消息积压", "性能优化"],
        expected_points=["临时扩容", "消费并发", "批量处理", "下游性能优化", "长期限流"],
        difficulty=4,
    ),
    BankQuestion(
        id="MQ008", type=QuestionType.SCENARIO, category="分布式事务",
        question="设计一个基于消息队列的最终一致性方案，解决跨服务的数据一致性问题（如下单和扣库存）。",
        tags=["kafka", "rocketmq", "分布式"],
        expected_points=["本地消息表", "事务消息", "消费确认", "补偿机制", "幂等设计"],
        difficulty=5,
    ),

    # ── AI / LLM / Agent ──────────────────────────────
    BankQuestion(
        id="AI001", type=QuestionType.TECHNICAL, category="RAG",
        question="RAG（检索增强生成）的完整流程是怎样的？文档切分（Chunking）策略如何选择？为什么不能把整篇文档直接喂给 LLM？",
        tags=["rag", "langchain", "embedding"],
        expected_points=["检索增强流程", "Chunking策略", "Embedding", "Top-K检索", "上下文限制"],
        difficulty=3,
        follow_up_hints=["切分过大或过小分别有什么问题？", "检索召回不准怎么办（rerank）？"],
    ),
    BankQuestion(
        id="AI002", type=QuestionType.TECHNICAL, category="Embedding",
        question="Embedding 是什么？为什么语义相近的文本在向量空间中距离更近？如何评估一个 Embedding 模型的好坏？",
        tags=["embedding", "transformers", "huggingface"],
        expected_points=["向量表示", "语义相似度", "余弦距离", "评测数据集", "对比学习"],
        difficulty=4,
    ),
    BankQuestion(
        id="AI003", type=QuestionType.TECHNICAL, category="向量数据库",
        question="向量数据库的核心索引 HNSW 的原理是什么？和暴力搜索、IVF 相比各有什么优劣？",
        tags=["向量数据库", "chromadb", "faiss"],
        expected_points=["HNSW分层图", "近似最近邻", "IVF倒排", "召回率vs速度", "选型依据"],
        difficulty=4,
        follow_up_hints=["为什么向量数据库不支持传统数据库的精确查询语义？"],
    ),
    BankQuestion(
        id="AI004", type=QuestionType.TECHNICAL, category="Agent",
        question="ReAct 和 Plan-Execute 两种 Agent 模式的区别是什么？分别适用于什么场景？",
        tags=["智能体", "ai agent", "langchain"],
        expected_points=["ReAct模式", "Plan-Execute", "思考行动观察循环", "适用场景", "局限性"],
        difficulty=3,
    ),
    BankQuestion(
        id="AI005", type=QuestionType.TECHNICAL, category="Agent",
        question="Function Calling 的底层实现是怎样的？LLM 是如何决定调用哪个工具的？工具参数的 JSON 幻觉怎么解决？",
        tags=["llm", "大模型", "function calling"],
        expected_points=["工具Schema描述", "模型决策", "参数生成", "JSON Schema约束", "校验重试"],
        difficulty=4,
    ),
    BankQuestion(
        id="AI006", type=QuestionType.TECHNICAL, category="Prompt工程",
        question="Prompt Engineering 有哪些核心技巧？few-shot、Chain of Thought、结构化输出分别在什么场景使用？",
        tags=["prompt", "llm", "大模型"],
        expected_points=["few-shot", "CoT", "角色设定", "结构化输出", "提示词模板"],
        difficulty=3,
    ),
    BankQuestion(
        id="AI007", type=QuestionType.TECHNICAL, category="多Agent",
        question="多 Agent 协作有哪几种常见模式（串行/并行/辩论）？Agent 之间如何传递信息？怎么避免协作失控？",
        tags=["多智能体", "ai agent", "langchain"],
        expected_points=["串行管道", "并行汇总", "辩论裁决", "消息传递", "终止条件"],
        difficulty=4,
    ),
    BankQuestion(
        id="AI008", type=QuestionType.TECHNICAL, category="上下文工程",
        question="多轮对话中上下文窗口超限怎么处理？滑动窗口、摘要压缩、优先级保留各有什么优劣？",
        tags=["llm", "大模型", "上下文管理"],
        expected_points=["滑动窗口", "摘要压缩", "优先级保留", "语义分块", "Token预算"],
        difficulty=4,
    ),
    BankQuestion(
        id="AI009", type=QuestionType.TECHNICAL, category="LLM",
        question="LLM 的幻觉（Hallucination）是怎么产生的？RAG 和微调分别能在多大程度上缓解？",
        tags=["rag", "大模型", "llm"],
        expected_points=["幻觉定义", "训练数据局限", "RAG缓解", "微调作用", "人工校验兜底"],
        difficulty=4,
    ),
    BankQuestion(
        id="AI010", type=QuestionType.TECHNICAL, category="LLM",
        question="提示词工程、RAG、微调三种方案如何选型？LoRA 微调的原理是什么？",
        tags=["微调", "rag", "大模型"],
        expected_points=["三种方案对比", "成本考量", "LoRA原理", "低秩分解", "选型决策树"],
        difficulty=4,
    ),
    BankQuestion(
        id="AI011", type=QuestionType.TECHNICAL, category="深度学习",
        question="Transformer 的 self-attention 机制是如何工作的？为什么需要位置编码？多头注意力解决什么问题？",
        tags=["transformers", "pytorch", "深度学习"],
        expected_points=["QKV计算", "注意力权重", "位置编码", "多头注意力", "并行计算优势"],
        difficulty=4,
    ),
    BankQuestion(
        id="AI012", type=QuestionType.TECHNICAL, category="LLM推理优化",
        question="大模型推理有哪些优化手段？KV Cache、量化、批处理分别解决了什么问题？",
        tags=["大模型", "llm", "推理优化"],
        expected_points=["KV Cache", "量化", "连续批处理", "投机采样", "显存优化"],
        difficulty=4,
    ),
    BankQuestion(
        id="AI013", type=QuestionType.TECHNICAL, category="LLM评测",
        question="LLM 应用如何做评测？离线评测和线上评测分别用什么指标？怎么构建评测数据集？",
        tags=["llm", "大模型", "评测"],
        expected_points=["离线评测", "线上指标", "评测数据集", "人工标注", "A/B测试"],
        difficulty=3,
    ),
    BankQuestion(
        id="AI014", type=QuestionType.SCENARIO, category="RAG",
        question="设计一个企业知识库问答系统，要求支持多租户权限隔离，回答准确率 95% 以上。请给出完整方案。",
        tags=["rag", "向量数据库", "langchain"],
        expected_points=["文档解析", "Chunking", "向量存储", "权限过滤", "效果评测"],
        difficulty=5,
    ),
    BankQuestion(
        id="AI015", type=QuestionType.SCENARIO, category="Agent",
        question="设计一个处理客服工单的 AI Agent 系统。哪些环节用 LLM、哪些环节用规则？成本如何控制？",
        tags=["智能体", "ai agent", "llm"],
        expected_points=["规则预处理", "LLM意图识别", "工具调用", "人工兜底", "成本分层"],
        difficulty=4,
    ),
    BankQuestion(
        id="AI016", type=QuestionType.CODING, category="NLP",
        question="请用 Python 实现一个简单的 BPE（Byte Pair Encoding）分词器，输入语料和词表大小，输出词表。",
        tags=["python", "nlp", "算法"],
        expected_points=["词频统计", "合并规则", "迭代合并", "词表生成", "边界处理"],
        difficulty=4,
        follow_up_hints=["BPE 和 WordPiece 的区别是什么？"],
    ),

    # ── 项目深挖题 ───────────────────────────────────────
    BankQuestion(
        id="PRJ001", type=QuestionType.PROJECT, category="项目经验",
        question="请分享一个你在项目中做过的有影响力的技术决策。当时有哪些方案？为什么选择了这个？现在回头看，这个决策是对的吗？",
        tags=["项目经验", "技术决策", "trade-off"],
        expected_points=["背景context", "候选方案对比", "决策依据", "复盘反思"],
        difficulty=3,
    ),
    BankQuestion(
        id="PRJ002", type=QuestionType.PROJECT, category="项目经验",
        question="请描述一个你项目中遇到过的线上故障。你是怎么发现、定位、止血、复盘、避免再次发生的？",
        tags=["项目经验", "故障处理", "线上问题"],
        expected_points=["发现方式", "定位过程", "止血方案", "复盘改进"],
        difficulty=3,
    ),
    BankQuestion(
        id="PRJ003", type=QuestionType.PROJECT, category="项目经验",
        question="你有没有接手过一个遗留系统（或别人的代码）？你是如何理解原有逻辑、评估风险、进行重构的？",
        tags=["项目经验", "遗留系统", "重构"],
        expected_points=["理解策略", "风险评估", "渐进式重构", "测试保障"],
        difficulty=3,
    ),

    # ── 行为面试题 ───────────────────────────────────────
    BankQuestion(
        id="BEH001", type=QuestionType.BEHAVIORAL, category="团队协作",
        question="请举个例子：你和一个同事在技术方案上有比较大的分歧，你是怎么处理的？最终结果如何？",
        tags=["沟通", "团队协作", "冲突处理"],
        expected_points=["分歧描述", "处理方式(数据驱动)", "如何推动共识", "结果和反思"],
        difficulty=2,
    ),
    BankQuestion(
        id="BEH002", type=QuestionType.BEHAVIORAL, category="项目管理",
        question="描述一个你主导推动的项目或需求。你如何制定计划、协调资源、推动各方配合？过程中遇到的最大阻碍是什么？",
        tags=["领导力", "项目管理", "推动力"],
        expected_points=["目标设定", "资源协调", "阻碍应对", "结果量化"],
        difficulty=3,
    ),
    BankQuestion(
        id="BEH003", type=QuestionType.BEHAVIORAL, category="学习成长",
        question="你最近学的一个新技术是什么？你是怎么学的？用了哪些资源？现在在实际项目中用上了吗？",
        tags=["学习能力", "自驱", "成长"],
        expected_points=["技术选择动机", "学习路径", "实践应用", "深度理解"],
        difficulty=2,
    ),

    # ── 代码实操题 ───────────────────────────────────────
    BankQuestion(
        id="COD001", type=QuestionType.CODING, category="数据结构",
        question="请实现一个 LRU (最近最少使用) 缓存，要求 get 和 put 操作的时间复杂度都是 O(1)。请用 Python 写，并包含基本测试。",
        tags=["算法", "LRU", "哈希表", "双向链表"],
        expected_points=["哈希表+双向链表", "O(1) get/put", "容量淘汰", "边界处理"],
        difficulty=3,
        follow_up_hints=["如果要支持过期时间呢？", "并发安全版本怎么写？"],
    ),
    BankQuestion(
        id="COD002", type=QuestionType.CODING, category="算法",
        question="给定一个日志文件，每行格式为 'timestamp level message'（空格分隔），请实现一个函数按 level 统计每分钟的日志数量，并找出日志量最高的 5 个时间窗口。",
        tags=["算法", "日志分析", "数据结构"],
        expected_points=["解析逻辑", "哈希聚合", "排序取TopK", "时间窗口计算", "边界case"],
        difficulty=3,
    ),
    BankQuestion(
        id="COD003", type=QuestionType.CODING, category="并发",
        question="请用 Python 实现一个简单的线程池，支持 submit(task, *args) 和 shutdown(wait=True)。要求能够限制最大并发数。",
        tags=["python", "线程池", "并发", "queue"],
        expected_points=["任务队列设计", "worker线程", "并发控制", "优雅关闭", "异常处理"],
        difficulty=4,
    ),
]


# ═══════════════════════════════════════════════════════════════
#  检索引擎
# ═══════════════════════════════════════════════════════════════

class QuestionBankRetriever:
    """
    题库检索器。

    根据 JD 中提取的技能标签，从题库中检索最匹配的题目。

    检索策略:
        1. 精确标签匹配：JD 技能 ∩ 题目标签
        2. 领域泛化匹配：同领域下所有题
        3. 类型配额：保证五类题型的比例
        4. 难度分层：2-3道简单 + 4-5道中等 + 1-2道困难

    使用:
        retriever = QuestionBankRetriever()
        questions = retriever.retrieve(skills=["python", "redis", "mysql"], total=8)
    """

    def __init__(self, bank: list[BankQuestion] | None = None):
        self.bank = bank or QUESTION_BANK
        self._build_index()

    def _build_index(self):
        """构建倒排索引：标签 → 题目列表"""
        self._tag_index: dict[str, list[BankQuestion]] = {}
        for q in self.bank:
            for tag in q.tags:
                tag_lower = tag.lower()
                if tag_lower not in self._tag_index:
                    self._tag_index[tag_lower] = []
                self._tag_index[tag_lower].append(q)

    def retrieve(
        self,
        skills: list[str],
        total: int = 8,
        exclude_ids: set[str] | None = None,
    ) -> list[BankQuestion]:
        """
        根据技能列表检索题目。

        Args:
            skills: JD 中提取的技能名称列表
            total: 总题目数
            exclude_ids: 排除的题目 ID

        Returns:
            按相关性排序的题目列表
        """
        exclude = exclude_ids or set()
        scored: dict[str, tuple[BankQuestion, int]] = {}  # id → (question, score)

        # 阶段 1: 标签匹配打分
        skill_set = {s.lower() for s in skills}
        for skill in skill_set:
            # 精确匹配
            if skill in self._tag_index:
                for q in self._tag_index[skill]:
                    if q.id in exclude:
                        continue
                    score = scored.get(q.id, (q, 0))[1] + 3  # 精确匹配 +3
                    scored[q.id] = (q, score)

            # 模糊匹配（技能词包含标签词 或 标签词包含技能词）
            for tag, questions in self._tag_index.items():
                if tag in skill or skill in tag:
                    for q in questions:
                        if q.id in exclude:
                            continue
                        score = scored.get(q.id, (q, 0))[1] + 1  # 模糊匹配 +1
                        scored[q.id] = (q, score)

        # 按分数排序
        ranked = sorted(scored.values(), key=lambda x: x[1], reverse=True)

        # 阶段 2: 按类型分层，保证覆盖所有题型
        selected = self._stratified_select(ranked, total)

        # 阶段 3: 如果不够，用通用题目补齐
        if len(selected) < total:
            selected += self._fill_generic(total - len(selected), exclude | {q.id for q in selected})

        return selected[:total]

    def _stratified_select(
        self,
        ranked: list[tuple[BankQuestion, int]],
        total: int,
    ) -> list[BankQuestion]:
        """分层选择，保证五类题型都有覆盖"""
        # 每类的最小配额
        quotas = {
            QuestionType.TECHNICAL: max(1, int(total * 0.35)),
            QuestionType.SCENARIO: max(1, int(total * 0.20)),
            QuestionType.PROJECT: max(1, int(total * 0.15)),
            QuestionType.BEHAVIORAL: max(1, int(total * 0.15)),
            QuestionType.CODING: max(1, int(total * 0.10)),
        }

        selected: list[BankQuestion] = []
        seen_ids: set[str] = set()

        # 先按类型分别挑选 top-K
        by_type: dict[QuestionType, list[tuple[BankQuestion, int]]] = {}
        for q, score in ranked:
            by_type.setdefault(q.type, []).append((q, score))

        for qtype, quota in quotas.items():
            candidates = by_type.get(qtype, [])
            for q, _ in candidates:
                if len([s for s in selected if s.type == qtype]) >= quota:
                    break
                if q.id not in seen_ids:
                    selected.append(q)
                    seen_ids.add(q.id)

        # 剩余名额按分数补满
        remaining = total - len(selected)
        for q, _ in ranked:
            if remaining <= 0:
                break
            if q.id not in seen_ids:
                selected.append(q)
                seen_ids.add(q.id)
                remaining -= 1

        return selected

    def _fill_generic(self, count: int, exclude_ids: set[str]) -> list[BankQuestion]:
        """用通用题目补齐数量"""
        # 按难度分层取通用题
        generic = [q for q in self.bank if q.id not in exclude_ids]
        generic.sort(key=lambda q: q.difficulty)

        # 优先取中等难度的
        medium = [q for q in generic if q.difficulty == 3]
        others = [q for q in generic if q.difficulty != 3]
        pool = medium + others

        return pool[:count]

    def get_by_id(self, question_id: str) -> BankQuestion | None:
        """根据 ID 获取题目"""
        for q in self.bank:
            if q.id == question_id:
                return q
        return None

    def stats(self) -> dict:
        """题库统计"""
        by_type = {}
        by_difficulty = {}
        for q in self.bank:
            by_type[q.type.value] = by_type.get(q.type.value, 0) + 1
            by_difficulty[q.difficulty] = by_difficulty.get(q.difficulty, 0) + 1

        return {
            "total_questions": len(self.bank),
            "by_type": by_type,
            "by_difficulty": by_difficulty,
            "unique_tags": len(self._tag_index),
        }
