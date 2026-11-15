"""公开内容校验服务(规则引擎降级模式, 不依赖外部 LLM)。

对一份要进入公开站的草稿做四类检查:
1. privacy_scan   隐私扫描 —— 检测 PII 与私人数据标记(红线: 私人数据绝不出站)
2. fact_check     事实检查 —— 检测未证实的量化声明 / 需要来源的主张
3. license_check  许可检查 —— 依据来源的 license_status 判定可否公开
4. markdown_lint  结构检查 —— 基本 Markdown 完整性与坏链接

返回统一的 (status, errors) 结构: status ∈ PASS/WARN/FAIL, errors 为
[{"check", "level"(error|warning), "message"}]。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- PII 检测正则(中文环境) ---
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_CN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_CN = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_ID_CN18 = re.compile(r"(?<!\d)\d{6}(18|19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")
# 私人数据标记词(本地私人数据系统里常见的字段名/内容)
_PRIVATE_MARKERS = [
    "身份证号", "家庭住址", "银行卡", "银行账号", "个人简历", "履历表",
    "考试成绩单", "能力画像原始分", "记忆中心条目", "私人工作空间",
]

# --- 量化声明(未证实需人工审核) ---
_QUANT_CLAIM = re.compile(r"(提升|降低|减少|增加|提高|节省|下降|增长|改善)[^。\n]{0,12}?\d+(\.\d+)?%")
_BARE_PERCENT = re.compile(r"(?<![\d.])\d{1,3}(\.\d+)?\s*%")

# 坏链接: `](` 后无内容 或 `[x]()` 空链接
_BAD_LINK = re.compile(r"\]\(\s*\)")


@dataclass
class CheckResult:
    status: str = "PASS"  # PASS / WARN / FAIL
    errors: list[dict] = field(default_factory=list)

    def add(self, check: str, level: str, message: str) -> None:
        self.errors.append({"check": check, "level": level, "message": message})
        # 取最高严重度
        if level == "error" or self.status == "PASS":
            self.status = {"error": "FAIL", "warning": "WARN"}.get(level, self.status)
        elif level == "warning" and self.status == "FAIL":
            pass  # 保持 FAIL


def privacy_scan(markdown: str) -> CheckResult:
    r = CheckResult()
    if not markdown:
        r.add("privacy", "warning", "内容为空")
        return r
    if _EMAIL.search(markdown):
        r.add("privacy", "error", "检测到邮箱地址(PII), 不得进入公开站")
    if _PHONE_CN.search(markdown):
        r.add("privacy", "error", "检测到中国大陆手机号(PII), 不得进入公开站")
    if _ID_CN.search(markdown) or _ID_CN18.search(markdown):
        r.add("privacy", "error", "检测到身份证号码(PII), 不得进入公开站")
    for marker in _PRIVATE_MARKERS:
        if marker in markdown:
            r.add("privacy", "error", f"检测到私人数据标记「{marker}」, 不得进入公开站")
    if not r.errors:
        r.add("privacy", "info", "未检测到明显 PII")
    return r


def fact_check(markdown: str) -> CheckResult:
    r = CheckResult()
    if not markdown:
        r.add("fact", "warning", "内容为空, 无法进行事实检查")
        return r
    claims = _QUANT_CLAIM.findall(markdown)
    bare = _BARE_PERCENT.findall(markdown)
    if claims or len(bare) >= 1:
        # 量化声明若无引用来源标记则需人工审核
        has_cite = bool(re.search(r"(\[来源|\[引用|\[证据|\(来源|\(参考|\[\\d+\])", markdown))
        if not has_cite:
            r.add("fact", "warning", f"检测到 {len(claims) + len(bare)} 处量化/百分比声明且无引用来源, 需人工核实后再发布")
        else:
            r.add("fact", "info", "检测到量化声明, 已附引用, 建议仍人工复核")
    if not r.errors:
        r.add("fact", "info", "未检测到明显未证实量化声明")
    return r


def license_check(sources: list[dict]) -> CheckResult:
    """sources: [{license_status, title, url, ...}]"""
    r = CheckResult()
    if not sources:
        r.add("license", "warning", "无来源记录, 版权归属不明, 需人工确认")
        return r
    restricted = [s for s in sources if s.get("license_status") == "RESTRICTED"]
    unknown = [s for s in sources if s.get("license_status") in ("UNKNOWN", None, "")]
    if restricted:
        r.add("license", "error", f"{len(restricted)} 个来源为 RESTRICTED, 禁止公开")
    if unknown:
        r.add("license", "warning", f"{len(unknown)} 个来源许可不明, 需人工确认授权")
    if not restricted and not unknown:
        r.add("license", "info", "全部来源许可清晰(PUBLIC_DOMAIN/PERMISSIVE/USER_OWNED)")
    return r


def markdown_lint(markdown: str) -> CheckResult:
    r = CheckResult()
    if not markdown.strip():
        r.add("markdown", "error", "Markdown 内容为空")
        return r
    if not re.search(r"^#{1,6}\s+\S", markdown, re.M):
        r.add("markdown", "warning", "缺少一级到六级标题, 建议补充结构")
    if _BAD_LINK.search(markdown):
        r.add("markdown", "error", "存在空链接 `[]()`, 需修复")
    if not r.errors:
        r.add("markdown", "info", "Markdown 结构基本完整")
    return r


def run_all(markdown: str, sources: list[dict] | None = None) -> CheckResult:
    """汇总四类检查, 返回最高严重度的总体结论。"""
    sources = sources or []
    combined = CheckResult()
    for r in (privacy_scan(markdown), fact_check(markdown), license_check(sources), markdown_lint(markdown)):
        for e in r.errors:
            combined.errors.append(e)
            if e["level"] == "error" or combined.status == "PASS":
                combined.status = {"error": "FAIL", "warning": "WARN", "info": "PASS"}.get(e["level"], combined.status)
            elif e["level"] == "warning" and combined.status == "FAIL":
                pass
    if not combined.errors:
        combined.status = "PASS"
    return combined


def is_low_risk(result: CheckResult) -> bool:
    """低风险判定: 无 error(FAIL) 且无 warning(WARN)。"""
    return result.status == "PASS"
