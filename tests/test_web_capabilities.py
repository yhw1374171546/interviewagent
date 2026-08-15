"""
Web 生产能力接入测试
====================
确保生产面试链路（server.py 创建 Interviewer）已启用全部已验证能力:
    1. 多评委仲裁（评分随机性 -36%）
    2. 评分校准（MAE -37%）
    3. 自适应难度（个性化面试）
    4. JD 语义缓存（省 LLM 调用）

防回归: 任何一项开关丢失 = 能力断层（评测成果在生产不生效），测试即红。
"""

from pathlib import Path

SERVER_SRC = Path(__file__).resolve().parent.parent / "web" / "server.py"


class TestWebProductionCapabilities:

    def test_all_capabilities_enabled_in_create(self):
        """创建面试的 Interviewer 构造必须启用全部 4 项能力"""
        src = SERVER_SRC.read_text(encoding="utf-8")
        # 定位创建 Interviewer 的代码块
        start = src.index("interviewer = Interviewer(")
        block = src[start:start + 600]

        required = {
            "jd_cache": "jd_cache=get_jd_cache()",
            "multi_judge": "multi_judge=get_multi_judge()",
            "calibrate": "calibrate=True",
            "adaptive": "adaptive_enabled=True",
        }
        missing = [name for name, pattern in required.items() if pattern not in block]
        assert not missing, f"Web 生产能力未接入: {missing}（能力断层！）"

    def test_lazy_getters_exist(self):
        """懒加载 getter 必须存在（get_jd_cache / get_multi_judge）"""
        src = SERVER_SRC.read_text(encoding="utf-8")
        assert "def get_jd_cache()" in src
        assert "def get_multi_judge()" in src
