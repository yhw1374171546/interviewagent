"""
输出校验器测试（阶段 3）
======================
覆盖 JSON 提取、Schema 校验/修正、截断 JSON 修复。
全部离线（纯函数），CI 可直接运行。
"""

from interview.output_validator import (
    OutputValidator,
    ValidationLevel,
    ValidationResult,
    extract_json,
    repair_truncated_json,
    safe_parse_json,
    validate_and_parse,
)

# ── extract_json ────────────────────────────────────────────────

class TestExtractJson:

    def test_markdown_json_fence(self):
        text = '```json\n{"position": "后端"}\n```'
        assert extract_json(text) == '{"position": "后端"}'

    def test_markdown_plain_fence(self):
        text = '```\n{"a": 1}\n```'
        assert extract_json(text) == '{"a": 1}'

    def test_bare_json(self):
        assert extract_json('{"a": 1}') == '{"a": 1}'

    def test_surrounding_text(self):
        text = '好的，以下是结果：{"position": "后端"} 希望有帮助'
        assert extract_json(text) == '{"position": "后端"}'

    def test_chinese_punctuation_normalized(self):
        text = '{"position"：“后端”，“n”：1}'  # 中文引号 + 逗号
        result = extract_json(text)
        assert "“" not in result and "”" not in result and "，" not in result


# ── OutputValidator.validate ────────────────────────────────────

class TestValidate:

    def setup_method(self):
        self.v = OutputValidator()

    def test_unknown_schema_warns(self):
        result = self.v.validate("unknown", {})
        assert result.level == ValidationLevel.WARNING
        assert result.is_valid is True

    def test_object_type_mismatch_fatal(self):
        result = self.v.validate("jd_analysis", "not a dict")
        assert result.level == ValidationLevel.FATAL
        assert result.needs_retry is True

    def test_array_type_mismatch_fatal(self):
        result = self.v.validate("questions", {"not": "list"})
        assert result.level == ValidationLevel.FATAL

    def test_valid_object_clean(self):
        result = self.v.validate("jd_analysis", {"position": "后端工程师"})
        assert result.is_clean is True
        assert result.fixed_data is None

    def test_missing_required_field(self):
        result = self.v.validate("jd_analysis", {})
        assert result.level == ValidationLevel.FATAL
        assert result.needs_retry is True
        assert result.fixed_data["position"] == ""

    def test_enum_correction(self):
        result = self.v.validate("evaluation", {
            "depth_level": "很深很深", "structure_level": "清晰",
            "follow_up_decision": "move_on",
        })
        assert result.level == ValidationLevel.WARNING
        assert result.fixed_data["depth_level"] == "表面"  # enum[0]

    def test_number_range_clamp(self):
        result = self.v.validate("interview_report", {
            "overall_score": 99, "verdict": "推荐通过",
        })
        assert result.fixed_data["overall_score"] == 10

    def test_array_items_validated(self):
        result = self.v.validate("questions", [
            {"type": "technical", "question": "解释 GIL"},
        ])
        assert result.is_valid is True

    def test_array_item_missing_required(self):
        result = self.v.validate("questions", [{"type": "technical"}])
        assert result.level == ValidationLevel.FATAL


# ── repair_truncated_json ───────────────────────────────────────

class TestRepairTruncatedJson:

    def test_valid_json_passes_through(self):
        assert repair_truncated_json('{"a": 1}') == {"a": 1}

    def test_repairs_truncated_array(self):
        # 截断发生在 "c" 字段值未闭合 → 回退到字段边界，保住已完整的字段
        repaired = repair_truncated_json('{"a": 1, "b": [1, 2], "c": "x"')
        assert repaired == {"a": 1, "b": [1, 2]}

    def test_unrepairable_returns_none(self):
        assert repair_truncated_json("不是 JSON") is None


# ── safe_parse_json / validate_and_parse ────────────────────────

class TestConvenienceFunctions:

    def test_safe_parse_json_ok(self):
        data, err = safe_parse_json('```json\n{"a": 1}\n```')
        assert data == {"a": 1}
        assert err is None

    def test_safe_parse_json_error(self):
        data, err = safe_parse_json("{invalid")
        assert data is None
        assert err is not None

    def test_validate_and_parse_success(self):
        data, result = validate_and_parse('{"position": "后端"}', "jd_analysis")
        assert data == {"position": "后端"}
        assert result.is_clean is True

    def test_validate_and_parse_fixes(self):
        data, result = validate_and_parse('{"position": ""}', "jd_analysis")
        # position 空串 min_len 1 不满足 → 类型 OK 但太短（无 fixed 覆盖长度）
        assert data is not None


# ── ValidationResult 属性 ───────────────────────────────────────

class TestValidationResult:

    def test_is_valid_and_is_clean(self):
        ok = ValidationResult()
        assert ok.is_valid is True
        assert ok.is_clean is True

        fatal = ValidationResult(level=ValidationLevel.FATAL)
        assert fatal.is_valid is False
        assert fatal.is_clean is False

    def test_is_valid_excludes_fatal_only(self):
        warn = ValidationResult(level=ValidationLevel.WARNING)
        assert warn.is_valid is True  # 非 FATAL 即可用
