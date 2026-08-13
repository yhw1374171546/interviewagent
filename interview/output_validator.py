"""
LLM 输出校验器
==============
确保 LLM 生成的 JSON 输出符合预期 Schema，不合规时自动修正。

为什么需要:
    - LLM 的输出不可靠 — 可能缺字段、类型错误、JSON 格式错误
    - 面试评分场景对数据质量要求极高 — 一个格式错误不能污染整场面试
    - Structured Output 是基础保障，但还需要业务规则校验

校验策略:
    1. JSON 解析校验 — 能 parse 吗？
    2. Schema 校验 — 字段全吗？类型对吗？
    3. 业务规则校验 — 分数范围对吗？追问决策合理吗？
    4. 自动修正 — 轻微格式问题自动 fix，严重问题让 LLM 重新生成

控制模型输出的手段:
    1. JSON Schema 约束 (Function Calling / Structured Output)
    2. System Prompt 格式要求
    3. 输出后校验 + 重试
    4. Rule-based 兜底
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class ValidationLevel(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"


# 级别严重度排序（OK < WARNING < ERROR < FATAL）。
# 注意不能用枚举 value 字符串比较 —— "fatal" > "ok" 按字母序为 False，
# 会导致数组子项校验的 ERROR/FATAL 无法传播到顶层结果。
_LEVEL_RANK = {
    ValidationLevel.OK: 0,
    ValidationLevel.WARNING: 1,
    ValidationLevel.ERROR: 2,
    ValidationLevel.FATAL: 3,
}


@dataclass
class ValidationResult:
    """校验结果"""
    level: ValidationLevel = ValidationLevel.OK
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixed_data: dict | None = None   # 自动修正后的数据
    needs_retry: bool = False         # 是否需要让 LLM 重新生成

    @property
    def is_valid(self) -> bool:
        return self.level != ValidationLevel.FATAL

    @property
    def is_clean(self) -> bool:
        return self.level == ValidationLevel.OK


# ── JSON 提取器 — 处理 markdown 包装和常见格式错误 ─────────────

def extract_json(text: str) -> str:
    """
    从 LLM 的原始输出中提取 JSON。

    处理的情况:
        - ```json { ... } ```
        - ``` { ... } ```
        - 裸 JSON
        - JSON 前后有说明文字
        - 中文标点混入
    """
    text = text.strip()

    # 1. 尝试从 markdown 代码块提取
    if "```" in text:
        # 找到第一个 ``` 和最后一个 ```
        start = text.find("```")
        end = text.rfind("```")
        if start != end:
            block = text[start + 3:end].strip()
            # 去掉可能的 "json" 标记
            if block.startswith("json"):
                block = block[4:].strip()
            text = block

    # 2. 尝试找到 { } 或 [ ] 边界
    json_start = -1
    json_end = -1
    for i, ch in enumerate(text):
        if ch in ("{", "[") and json_start == -1:
            json_start = i
        if ch in ("}", "]"):
            json_end = i

    if json_start != -1 and json_end > json_start:
        text = text[json_start:json_end + 1]

    # 3. 修复常见格式问题
    # 中文逗号 → 英文逗号
    text = text.replace("“", '"').replace("”", '"')  # 中文引号
    text = text.replace("，", ",")  # 中文逗号

    return text.strip()


# ── Schema 校验器 ───────────────────────────────────────────────

class OutputValidator:
    """
    LLM 输出校验器。

    定义每个场景的期望 Schema，校验 + 自动修正。

    使用:
        validator = OutputValidator()
        result = validator.validate_jd_analysis(llm_output)
        if result.needs_retry:
            llm_output = await llm.chat(retry_prompt)
    """

    def __init__(self):
        self._schemas = self._define_schemas()

    # ── Schema 定义 ──────────────────────────────────────

    def _define_schemas(self) -> dict[str, dict]:
        """定义各场景的期望 Schema"""
        return {
            "jd_analysis": {
                "type": "object",
                "required": ["position"],
                "properties": {
                    "position": {"type": "string", "min_len": 1},
                    "domain_knowledge": {"type": "array"},
                    "responsibilities": {"type": "array"},
                    "interview_focus": {"type": "array", "max_items": 5},
                    "missing_skills": {"type": "array"},
                },
            },
            "evaluation": {
                "type": "object",
                "required": ["depth_level", "structure_level", "follow_up_decision"],
                "properties": {
                    "depth_level": {"type": "string", "enum": [
                        "表面", "较浅", "适中", "深入", "非常深入"
                    ]},
                    "structure_level": {"type": "string", "enum": [
                        "混乱", "松散", "一般", "清晰", "优秀"
                    ]},
                    "overall_comment": {"type": "string"},
                    "strengths": {"type": "array"},
                    "weaknesses": {"type": "array"},
                    "follow_up_decision": {"type": "string", "enum": [
                        "deepen", "challenge", "upgrade", "example", "move_on"
                    ]},
                    "follow_up_question": {"type": "string"},
                    "follow_up_reason": {"type": "string"},
                },
            },
            "interview_report": {
                "type": "object",
                "required": ["overall_score", "verdict"],
                "properties": {
                    "overall_score": {"type": "number", "min": 0, "max": 10},
                    "overall_level": {"type": "string"},
                    "main_strengths": {"type": "array", "max_items": 5},
                    "main_weaknesses": {"type": "array", "max_items": 5},
                    "improvement_advice": {"type": "string"},
                    "verdict": {"type": "string", "enum": [
                        "推荐通过", "建议待定", "不推荐通过"
                    ]},
                    "verdict_reason": {"type": "string"},
                },
            },
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["type", "question"],
                    "properties": {
                        "type": {"type": "string", "enum": [
                            "technical", "scenario", "project", "behavioral", "coding"
                        ]},
                        "category": {"type": "string"},
                        "question": {"type": "string", "min_len": 5},
                        "expected_points": {"type": "array"},
                        "difficulty": {"type": "integer", "min": 1, "max": 5},
                    },
                },
            },
        }

    # ── 校验方法 ──────────────────────────────────────────

    def validate(self, schema_name: str, data: dict | list) -> ValidationResult:
        """
        按 Schema 校验数据。

        Args:
            schema_name: Schema 名称
            data: 待校验的数据

        Returns:
            ValidationResult: 校验结果
        """
        schema = self._schemas.get(schema_name)
        if not schema:
            return ValidationResult(
                level=ValidationLevel.WARNING,
                warnings=[f"未定义 Schema: {schema_name}"],
            )

        result = ValidationResult()

        # 1. 类型校验
        if schema["type"] == "object":
            if not isinstance(data, dict):
                return ValidationResult(
                    level=ValidationLevel.FATAL,
                    errors=[f"期望 object，实际 {type(data).__name__}"],
                    needs_retry=True,
                )
            result = self._validate_object(schema, data)

        elif schema["type"] == "array":
            if not isinstance(data, list):
                return ValidationResult(
                    level=ValidationLevel.FATAL,
                    errors=[f"期望 array，实际 {type(data).__name__}"],
                    needs_retry=True,
                )
            result = self._validate_array(schema, data)

        return result

    def _validate_object(self, schema: dict, data: dict) -> ValidationResult:
        result = ValidationResult()
        fixed = dict(data)

        # 检查必填字段
        for field_name in schema.get("required", []):
            if field_name not in data or data[field_name] is None:
                result.errors.append(f"缺少必填字段: {field_name}")
                result.level = ValidationLevel.ERROR
                fixed[field_name] = self._default_value(
                    schema["properties"].get(field_name, {})
                )

        # 检查每个字段
        for field_name, prop in schema.get("properties", {}).items():
            if field_name not in data:
                continue

            value = data[field_name]

            # 类型检查
            expected_type = prop.get("type", "string")
            if not self._check_type(value, expected_type):
                result.errors.append(
                    f"字段 {field_name} 类型错误: 期望 {expected_type}，"
                    f"实际 {type(value).__name__}"
                )
                result.level = ValidationLevel.ERROR
                fixed[field_name] = self._coerce_type(value, expected_type)

            # 枚举检查
            if "enum" in prop and value not in prop["enum"]:
                result.warnings.append(
                    f"字段 {field_name} 值 '{value}' 不在允许范围 {prop['enum']}，"
                    f"已自动修正为 '{prop['enum'][0]}'"
                )
                fixed[field_name] = prop["enum"][0]
                if result.level == ValidationLevel.OK:
                    result.level = ValidationLevel.WARNING

            # 数值范围检查
            if expected_type in ("number", "integer"):
                if "min" in prop and value < prop["min"]:
                    fixed[field_name] = prop["min"]
                    result.warnings.append(f"字段 {field_name} 超出最小值，已修正")
                if "max" in prop and value > prop["max"]:
                    fixed[field_name] = prop["max"]
                    result.warnings.append(f"字段 {field_name} 超出最大值，已修正")

            # 字符串长度
            if expected_type == "string" and "min_len" in prop:
                if len(str(value)) < prop["min_len"]:
                    result.errors.append(f"字段 {field_name} 太短")
                    result.level = ValidationLevel.ERROR

        # 致命错误 → 需要重试
        if any("缺少必填字段" in e for e in result.errors):
            result.needs_retry = True
            result.level = ValidationLevel.FATAL

        if fixed != data:
            result.fixed_data = fixed

        return result

    def _validate_array(self, schema: dict, data: list) -> ValidationResult:
        result = ValidationResult()

        if "max_items" in schema and len(data) > schema["max_items"]:
            result.warnings.append(f"数组过长，截断至 {schema['max_items']} 项")
            data = data[:schema["max_items"]]

        # 校验每个元素
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(data):
                if isinstance(item, dict) and item_schema.get("type") == "object":
                    sub = self._validate_object(item_schema, item)
                    result.errors.extend(f"[{i}] {e}" for e in sub.errors)
                    result.warnings.extend(f"[{i}] {w}" for w in sub.warnings)
                    if _LEVEL_RANK[sub.level] > _LEVEL_RANK[result.level]:
                        result.level = sub.level

        return result

    # ── 工具方法 ──────────────────────────────────────────

    def _check_type(self, value: Any, expected: str) -> bool:
        if expected == "string":
            return isinstance(value, str)
        elif expected == "number":
            return isinstance(value, (int, float))
        elif expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        elif expected == "boolean":
            return isinstance(value, bool)
        elif expected == "array":
            return isinstance(value, list)
        elif expected == "object":
            return isinstance(value, dict)
        return True

    def _coerce_type(self, value: Any, expected: str) -> Any:
        """尝试类型转换"""
        try:
            if expected == "string":
                return str(value)
            elif expected in ("number", "integer"):
                return int(value) if expected == "integer" else float(value)
            elif expected == "array":
                return [value] if not isinstance(value, list) else value
            elif expected == "boolean":
                return bool(value)
        except (ValueError, TypeError):
            pass
        return self._default_value({"type": expected})

    def _default_value(self, prop: dict) -> Any:
        """返回类型的默认值"""
        defaults = {
            "string": "",
            "number": 0,
            "integer": 0,
            "boolean": False,
            "array": [],
            "object": {},
        }
        return defaults.get(prop.get("type", "string"), "")


# ── 截断 JSON 修复 ─────────────────────────────────────────────

def repair_truncated_json(text: str) -> dict | None:
    """
    尝试修复被 max_tokens 截断的 JSON。

    场景: 推理模型（如 deepseek-v4-pro）推理过长，回答 JSON 在
    max_tokens 处被硬截断 → json.loads 失败 → 评估降级。
    修复策略: 从尾部逐字符回退到最近的字段边界（`}` `]` `"` `,`），
    补齐未闭合的括号后重试解析 — 保住截断前已完整的字段。

    Args:
        text: LLM 原始输出

    Returns:
        修复后的 dict（尽力而为），无法修复返回 None
    """
    cleaned = extract_json(text)
    if not cleaned:
        return None

    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    # 尾部回退: 找最近的字段边界，补齐括号重试
    for i in range(len(cleaned) - 1, max(0, len(cleaned) - 600), -1):
        if cleaned[i] not in '}],':
            continue
        candidate = cleaned[:i + 1]
        # 补齐未闭合的括号/引号
        candidate += '}' * max(0, candidate.count("{") - candidate.count("}"))
        candidate += ']' * max(0, candidate.count("[") - candidate.count("]"))
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and data:
                return data
        except json.JSONDecodeError:
            continue

    return None


# ── 便捷函数 ────────────────────────────────────────────────────

def safe_parse_json(text: str) -> tuple[dict | list | None, str | None]:
    """
    安全解析 LLM 输出的 JSON。

    Returns:
        (parsed_data, error_message)
        如果成功，error_message 为 None
    """
    try:
        cleaned = extract_json(text)
        data = json.loads(cleaned)
        return data, None
    except json.JSONDecodeError as e:
        return None, f"JSON 解析失败: {e}"
    except Exception as e:
        return None, f"未知解析错误: {e}"


def validate_and_parse(
    text: str,
    schema_name: str,
    validator: OutputValidator | None = None,
) -> tuple[dict | list | None, ValidationResult]:
    """
    一站式：提取 JSON + Schema 校验。

    Returns:
        (validated_data, validation_result)
        如果校验失败需要重试，validated_data 为修正后的数据
    """
    v = validator or OutputValidator()

    # Step 1: 提取 JSON
    data, parse_error = safe_parse_json(text)
    if parse_error:
        return None, ValidationResult(
            level=ValidationLevel.FATAL,
            errors=[parse_error],
            needs_retry=True,
        )

    # Step 2: Schema 校验
    result = v.validate(schema_name, data)

    # Step 3: 返回修正后的数据
    if result.fixed_data is not None:
        return result.fixed_data, result

    return data, result
