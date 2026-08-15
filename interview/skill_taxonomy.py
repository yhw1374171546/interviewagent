"""
技能分类知识库
==============
超过 200 种技术关键词，按领域分类。
JD 解析时先走规则匹配，匹配不到的再用 LLM 兜底。

设计原则:
- 规则能覆盖的，绝不调 LLM
- LLM 只处理语义模糊的部分（软技能、职责描述）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── 技术栈知识库 ──────────────────────────────────────────────
# 格式: 关键词 → (标准名称, 领域, 类别)
# 覆盖: 编程语言 / 框架 / 数据库 / 中间件 / 云原生 / 前端 / 大数据 / AI

SKILL_TAXONOMY: dict[str, tuple[str, str, str]] = {
    # ── 编程语言 ──
    "python":        ("Python", "language", "编程语言"),
    "golang":        ("Go", "language", "编程语言"),
    "go":            ("Go", "language", "编程语言"),
    "java":          ("Java", "language", "编程语言"),
    "javascript":    ("JavaScript", "language", "编程语言"),
    "js":            ("JavaScript", "language", "编程语言"),
    "typescript":    ("TypeScript", "language", "编程语言"),
    "ts":            ("TypeScript", "language", "编程语言"),
    "rust":          ("Rust", "language", "编程语言"),
    "c++":           ("C++", "language", "编程语言"),
    "cpp":           ("C++", "language", "编程语言"),
    "c#":            ("C#", "language", "编程语言"),
    "csharp":        ("C#", "language", "编程语言"),
    "kotlin":        ("Kotlin", "language", "编程语言"),
    "swift":         ("Swift", "language", "编程语言"),
    "scala":         ("Scala", "language", "编程语言"),
    "php":           ("PHP", "language", "编程语言"),
    "ruby":          ("Ruby", "language", "编程语言"),
    "shell":         ("Shell", "language", "编程语言"),
    "bash":          ("Shell", "language", "编程语言"),
    "sql":           ("SQL", "language", "编程语言"),

    # ── 后端框架 ──
    "django":        ("Django", "framework", "后端框架"),
    "flask":         ("Flask", "framework", "后端框架"),
    "fastapi":       ("FastAPI", "framework", "后端框架"),
    "spring":        ("Spring", "framework", "后端框架"),
    "springboot":    ("Spring Boot", "framework", "后端框架"),
    "spring boot":   ("Spring Boot", "framework", "后端框架"),
    "gin":           ("Gin", "framework", "后端框架"),
    "echo":          ("Echo", "framework", "后端框架"),
    "express":       ("Express", "framework", "后端框架"),
    "nestjs":        ("NestJS", "framework", "后端框架"),
    "next.js":       ("Next.js", "framework", "后端框架"),
    "nextjs":        ("Next.js", "framework", "后端框架"),
    "laravel":       ("Laravel", "framework", "后端框架"),
    "rails":         ("Rails", "framework", "后端框架"),
    "tornado":       ("Tornado", "framework", "后端框架"),
    "aiohttp":       ("aiohttp", "framework", "后端框架"),
    "sanic":         ("Sanic", "framework", "后端框架"),

    # ── 数据库 ──
    "mysql":         ("MySQL", "database", "关系型数据库"),
    "postgresql":    ("PostgreSQL", "database", "关系型数据库"),
    "postgres":      ("PostgreSQL", "database", "关系型数据库"),
    "sqlite":        ("SQLite", "database", "关系型数据库"),
    "oracle":        ("Oracle", "database", "关系型数据库"),
    "sqlserver":     ("SQL Server", "database", "关系型数据库"),
    "sql server":    ("SQL Server", "database", "关系型数据库"),
    "mongodb":       ("MongoDB", "database", "NoSQL数据库"),
    "mongo":         ("MongoDB", "database", "NoSQL数据库"),
    "redis":         ("Redis", "database", "缓存/NoSQL"),
    "elasticsearch": ("Elasticsearch", "database", "搜索引擎"),
    "es":            ("Elasticsearch", "database", "搜索引擎"),
    "cassandra":     ("Cassandra", "database", "NoSQL数据库"),
    "dynamodb":      ("DynamoDB", "database", "NoSQL数据库"),
    "clickhouse":    ("ClickHouse", "database", "OLAP数据库"),
    "tidb":          ("TiDB", "database", "分布式数据库"),
    "neo4j":         ("Neo4j", "database", "图数据库"),
    "hbase":         ("HBase", "database", "NoSQL数据库"),

    # ── 中间件 / 消息队列 ──
    "kafka":         ("Kafka", "middleware", "消息队列"),
    "rabbitmq":      ("RabbitMQ", "middleware", "消息队列"),
    "rocketmq":      ("RocketMQ", "middleware", "消息队列"),
    "pulsar":        ("Pulsar", "middleware", "消息队列"),
    "celery":        ("Celery", "middleware", "任务队列"),
    "nginx":         ("Nginx", "middleware", "反向代理"),
    "envoy":         ("Envoy", "middleware", "服务网格"),
    "istio":         ("Istio", "middleware", "服务网格"),
    "zookeeper":     ("ZooKeeper", "middleware", "分布式协调"),

    # ── 云原生 / DevOps ──
    "docker":        ("Docker", "devops", "容器化"),
    "kubernetes":    ("Kubernetes", "devops", "容器编排"),
    "k8s":           ("Kubernetes", "devops", "容器编排"),
    "helm":          ("Helm", "devops", "包管理"),
    "terraform":     ("Terraform", "devops", "IaC"),
    "jenkins":       ("Jenkins", "devops", "CI/CD"),
    "gitlab":        ("GitLab CI", "devops", "CI/CD"),
    "github actions":("GitHub Actions", "devops", "CI/CD"),
    "argo":          ("ArgoCD", "devops", "GitOps"),
    "prometheus":    ("Prometheus", "devops", "监控"),
    "grafana":       ("Grafana", "devops", "可视化"),
    "elk":           ("ELK Stack", "devops", "日志"),
    "ansible":       ("Ansible", "devops", "配置管理"),

    # ── 前端 ──
    "react":         ("React", "frontend", "前端框架"),
    "reactjs":       ("React", "frontend", "前端框架"),
    "vue":           ("Vue.js", "frontend", "前端框架"),
    "vuejs":         ("Vue.js", "frontend", "前端框架"),
    "angular":       ("Angular", "frontend", "前端框架"),
    "svelte":        ("Svelte", "frontend", "前端框架"),
    "jquery":        ("jQuery", "frontend", "前端库"),
    "redux":         ("Redux", "frontend", "状态管理"),
    "webpack":       ("Webpack", "frontend", "构建工具"),
    "vite":          ("Vite", "frontend", "构建工具"),
    "html":          ("HTML", "frontend", "标记语言"),
    "html5":         ("HTML5", "frontend", "标记语言"),
    "css":           ("CSS", "frontend", "样式"),
    "css3":          ("CSS3", "frontend", "样式"),
    "sass":          ("Sass", "frontend", "样式预处理"),
    "tailwind":      ("Tailwind CSS", "frontend", "CSS框架"),
    "bootstrap":     ("Bootstrap", "frontend", "CSS框架"),

    # ── 大数据 ──
    "hadoop":        ("Hadoop", "bigdata", "大数据框架"),
    "spark":         ("Spark", "bigdata", "大数据计算"),
    "flink":         ("Flink", "bigdata", "流计算"),
    "hive":          ("Hive", "bigdata", "数据仓库"),
    "airflow":       ("Airflow", "bigdata", "调度"),
    "dbt":           ("dbt", "bigdata", "数据转换"),
    "hdfs":          ("HDFS", "bigdata", "分布式文件系统"),
    "数仓":          ("数据仓库", "bigdata", "数据仓库"),
    "数据仓库":      ("数据仓库", "bigdata", "数据仓库"),
    "数据湖":        ("数据湖", "bigdata", "数据存储"),
    "实时计算":      ("实时计算", "bigdata", "流计算"),
    "离线计算":      ("离线计算", "bigdata", "批计算"),
    "etl":           ("ETL", "bigdata", "数据集成"),

    # ── AI / ML ──
    "tensorflow":    ("TensorFlow", "ai", "深度学习框架"),
    "pytorch":       ("PyTorch", "ai", "深度学习框架"),
    "scikit-learn":  ("Scikit-learn", "ai", "机器学习"),
    "sklearn":       ("Scikit-learn", "ai", "机器学习"),
    "keras":         ("Keras", "ai", "深度学习"),
    "xgboost":       ("XGBoost", "ai", "机器学习"),
    "pandas":        ("Pandas", "ai", "数据处理"),
    "numpy":         ("NumPy", "ai", "科学计算"),
    "opencv":        ("OpenCV", "ai", "计算机视觉"),
    "langchain":     ("LangChain", "ai", "LLM框架"),
    "llamaindex":    ("LlamaIndex", "ai", "LLM框架"),
    "huggingface":   ("HuggingFace", "ai", "模型平台"),
    "transformers":  ("Transformers", "ai", "NLP库"),
    # LLM / Agent 方向
    "rag":           ("RAG", "ai", "LLM技术"),
    "embedding":     ("Embedding", "ai", "LLM技术"),
    "向量数据库":    ("向量数据库", "ai", "LLM技术"),
    "向量检索":      ("向量检索", "ai", "LLM技术"),
    "chromadb":      ("ChromaDB", "ai", "向量数据库"),
    "faiss":         ("FAISS", "ai", "向量数据库"),
    "milvus":        ("Milvus", "ai", "向量数据库"),
    "llm":           ("LLM", "ai", "LLM技术"),
    "大模型":        ("大模型", "ai", "LLM技术"),
    "prompt":        ("Prompt Engineering", "ai", "LLM技术"),
    "智能体":        ("AI Agent", "ai", "LLM技术"),
    "多智能体":      ("多智能体", "ai", "LLM技术"),
    "multi-agent":   ("多智能体", "ai", "LLM技术"),
    # "agent" 子串补全: 短 JD（如仅「agent开发工程师」岗位名）也能提取到
    # AI Agent 技能 → 检索出 agent 方向题（此前缺失 → JD 无技能 → 通用补齐）
    "agent":         ("AI Agent", "ai", "LLM技术"),
    "agentic":       ("AI Agent", "ai", "LLM技术"),
    "nlp":           ("NLP", "ai", "自然语言处理"),
    "机器学习":      ("机器学习", "ai", "机器学习"),
    "深度学习":      ("深度学习", "ai", "深度学习"),
    "微调":          ("模型微调", "ai", "LLM技术"),
    "知识图谱":      ("知识图谱", "ai", "NLP"),

    # ── 协议 / 概念 ──
    "grpc":          ("gRPC", "concept", "RPC框架"),
    "graphql":       ("GraphQL", "concept", "API协议"),
    "rest":          ("RESTful API", "concept", "API风格"),
    "restful":       ("RESTful API", "concept", "API风格"),
    "websocket":     ("WebSocket", "concept", "通信协议"),
    "tcp":           ("TCP/IP", "concept", "网络协议"),
    "http":          ("HTTP/HTTPS", "concept", "网络协议"),
    "oauth":         ("OAuth 2.0", "concept", "认证协议"),
    "jwt":           ("JWT", "concept", "认证"),
    "sso":           ("SSO", "concept", "单点登录"),
    "rpc":           ("RPC", "concept", "远程调用"),
    "微服务":        ("微服务架构", "concept", "架构模式"),
    "微服务架构":    ("微服务架构", "concept", "架构模式"),
    "分布式":        ("分布式系统", "concept", "系统设计"),
    "高并发":        ("高并发", "concept", "性能"),
    "高可用":        ("高可用", "concept", "可靠性"),
    "敏捷":          ("敏捷开发", "concept", "开发流程"),
    "ddd":           ("领域驱动设计", "concept", "设计方法"),
    "领域驱动":      ("领域驱动设计", "concept", "设计方法"),
    "tdd":           ("测试驱动开发", "concept", "开发方法"),
    "cicd":          ("CI/CD", "concept", "持续集成/部署"),
    "ci/cd":         ("CI/CD", "concept", "持续集成/部署"),
    "devops":        ("DevOps", "concept", "开发运维"),
    "saas":          ("SaaS", "concept", "云服务模式"),
    "paas":          ("PaaS", "concept", "云服务模式"),
}

# 教育/经验关键词
EDUCATION_KEYWORDS = ["本科", "硕士", "博士", "研究生", "985", "211", "计算机相关专业"]
EXPERIENCE_PATTERNS = [
    r"(\d+)[-\s]*(\d+)\s*年",   # "3-5年"
    r"(\d+)\s*年以上",           # "3年以上"
    r"应届",                     # "应届生"
]

# 软技能关键词
SOFT_SKILL_KEYWORDS = [
    "沟通", "协作", "团队合作", "领导力", "自驱", "owner",
    "抗压", "逻辑思维", "学习能力", "主动性", "责任心",
    "跨部门", "项目管理", "时间管理", "文档能力", "英语",
    "ownership", "problem-solving", "problem solving",
]


# ── 规则匹配引擎 ──────────────────────────────────────────────


@dataclass
class RuleMatchResult:
    """规则匹配结果"""
    skills: list[dict] = field(default_factory=list)  # [{name, domain, category, source(required/preferred)}]
    education: str = ""
    experience: str = ""
    soft_skills: list[str] = field(default_factory=list)
    # 未匹配到的文本片段，交给 LLM 处理
    unmatched_text: str = ""


def _is_word_match(jd_lower: str, pos: int, keyword: str) -> bool:
    """
    短关键词词边界检查。

    避免子串误匹配:
        - "java" 命中 "javascript" → 前后都是字母，拒绝
        - "go" 命中 "django"/"mongodb" → 拒绝
        - "es" 命中 "results" → 拒绝
        - "java后端" 中的 "java" → 后接中文，接受（中文不是词内字符）

    注意: 只把 ASCII 字母/数字视为词内字符。中文不算 —
    否则 "java后端开发" 中的 "java" 会被 "后".isalnum() 误杀。

    Args:
        jd_lower: 小写 JD 文本
        pos: 关键词匹配位置
        keyword: 关键词

    Returns:
        True 表示是独立单词（有效匹配）
    """
    def _is_word_char(ch: str) -> bool:
        """ASCII 字母/数字才算词内字符"""
        return ch.isascii() and ch.isalnum()

    before = jd_lower[pos - 1] if pos > 0 else ""
    after_idx = pos + len(keyword)
    after = jd_lower[after_idx] if after_idx < len(jd_lower) else ""
    if _is_word_char(before) or _is_word_char(after):
        return False
    return True


def rule_based_extract(jd_text: str) -> RuleMatchResult:
    """
    基于规则从 JD 中提取技能。

    1. 遍历 SKILL_TAXONOMY，检查每个关键词是否出现在 JD 中
       （短关键词做词边界检查，避免 "java" 误匹配 "javascript"、"go" 误匹配 "django"）
    2. 区分「必须技能」和「加分技能」（通过上下文关键词判断）
    3. 提取学历、经验要求
    4. 提取软技能

    Args:
        jd_text: JD 原始文本

    Returns:
        RuleMatchResult: 规则匹配的部分 + 未匹配文本（交给 LLM）
    """
    result = RuleMatchResult()
    jd_lower = jd_text.lower()
    matched_spans: list[tuple[int, int]] = []  # 记录已匹配的文本区间

    # ── 1. 技能匹配 ──
    seen_skills: set[str] = set()  # 按标准名称去重
    for keyword, (std_name, domain, category) in SKILL_TAXONOMY.items():
        pos = jd_lower.find(keyword)
        if pos == -1:
            continue

        # 短关键词词边界检查: "java" 不能命中 "javascript"，"go" 不能命中 "django"
        if len(keyword) <= 4 and keyword.isalpha() and not _is_word_match(jd_lower, pos, keyword):
            continue

        # 去重：同一个标准名称只保留一次
        if std_name.lower() in seen_skills:
            continue
        seen_skills.add(std_name.lower())

        # 判断是必须还是加分 — 上下文窗口限定在当前行内
        # （真实 JD 每条要求独立成行；±50 字符窗口会跨行误判，
        #   例如「了解 Docker 者优先」会把上一行的 Python 也标记为加分）
        line_start = jd_text.rfind("\n", 0, pos) + 1
        line_end = jd_text.find("\n", pos)
        if line_end == -1:
            line_end = len(jd_text)
        context = jd_text[line_start:line_end].lower()

        is_preferred = any(w in context for w in [
            "加分", "优先", "plus", "nice to have", "preferred",
            "熟悉", "了解", "有...经验优先",
        ])

        result.skills.append({
            "name": std_name,
            "domain": domain,
            "category": category,
            "source": "preferred" if is_preferred else "required",
        })
        matched_spans.append((pos, pos + len(keyword)))

    # ── 2. 学历提取 ──
    for kw in EDUCATION_KEYWORDS:
        pos = jd_text.find(kw)
        if pos != -1:
            start = max(0, pos - 10)
            end = min(len(jd_text), pos + 20)
            result.education = jd_text[start:end].strip()
            matched_spans.append((start, end))
            break

    # ── 3. 经验提取 ──
    for pattern in EXPERIENCE_PATTERNS:
        m = re.search(pattern, jd_text)
        if m:
            result.experience = m.group(0)
            matched_spans.append((m.start(), m.end()))
            break

    # ── 4. 软技能提取 ──
    for kw in SOFT_SKILL_KEYWORDS:
        if kw.lower() in jd_lower:
            result.soft_skills.append(kw)

    # ── 5. 收集未匹配的文本（留给 LLM） ──
    # 合并重叠/相邻的匹配区间
    matched_spans.sort()
    merged = []
    for span in matched_spans:
        if merged and span[0] <= merged[-1][1] + 5:
            merged[-1] = (merged[-1][0], max(merged[-1][1], span[1]))
        else:
            merged.append(span)

    # 提取未匹配的文本段
    unmatched_parts = []
    last_end = 0
    for start, end in merged:
        if start > last_end:
            chunk = jd_text[last_end:start].strip()
            if len(chunk) > 10:  # 忽略太短的片段
                unmatched_parts.append(chunk)
        last_end = max(last_end, end)
    if last_end < len(jd_text):
        chunk = jd_text[last_end:].strip()
        if len(chunk) > 10:
            unmatched_parts.append(chunk)

    result.unmatched_text = "\n".join(unmatched_parts)

    return result


def get_skill_coverage_report(jd_text: str) -> dict:
    """分析 JD 中规则匹配的覆盖率（用于调试/监控）"""
    result = rule_based_extract(jd_text)
    total_chars = len(jd_text)
    matched_chars = total_chars - len(result.unmatched_text)

    return {
        "total_chars": total_chars,
        "matched_chars": matched_chars,
        "coverage": round(matched_chars / total_chars * 100, 1) if total_chars > 0 else 0,
        "skills_found": len(result.skills),
        "skills_by_domain": _group_by_domain(result.skills),
        "unmatched_needs_llm": len(result.unmatched_text) > 50,
    }


def _group_by_domain(skills: list[dict]) -> dict[str, int]:
    """按领域统计技能数量"""
    counts: dict[str, int] = {}
    for s in skills:
        domain = s.get("domain", "other")
        counts[domain] = counts.get(domain, 0) + 1
    return counts
