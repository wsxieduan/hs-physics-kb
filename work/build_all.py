# -*- coding: utf-8 -*-
"""统一生成初中、高中知识库与两份离线例题自测页。

脚本先分别重跑原有结构与物理校验；任一知识点、公式或常见错误防线失败，
就停止生成，避免把未通过的内容带进站点。旧速查页不会被本脚本删除或覆盖。
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import build_site as SITE
from build_quiz import build as build_quiz
from build_quiz import load_diagnostic_banks
from build_quiz import make_diagnostic_bank
from physkit import kb as KB
from physkit import expr as EX
from physkit import checks as CHECKS
import build_lite
import build_quick_site
import qc as FROZEN_QC


def inspect_kb(kb_dir, label):
    """沿用正式知识库校验器；校验未通过时不给页面输出。"""
    chapters = KB.load_kb(kb_dir)
    issues, id_map = KB.check_structure(chapters)
    errors = [issue for issue in issues if issue.level == "错误"]
    if errors:
        raise RuntimeError("%s结构检查失败：\n%s" %
                           (label, "\n".join("  - %s" % item for item in errors)))
    report = KB.run_physics_checks(chapters, id_map)
    stats = KB.summarize(report)
    bad_points = [item for item in report.values() if not item["ok"]]
    if bad_points or stats["trap_failures"] or stats["pass"] != stats["checks"]:
        details = ["%s：%s" % (item["id"], item["title"]) for item in bad_points]
        details.extend(stats["trap_failures"])
        raise RuntimeError("%s物理校验未全部通过：\n%s" % (label, "\n".join(details)))
    print("%s校验通过：%d 个知识点，%d 条公式，%d/%d 项检查，常见错误防线无失败。" %
          (label, stats["points"], stats["formulas"], stats["pass"], stats["checks"]))
    return chapters, report, stats


def point_index(chapters):
    """收集跨学段链接需要的标题和章节名。"""
    result = {}
    for _filename, chapter in chapters:
        chapter_name = chapter.get("chapter", "未命名章节")
        for point in chapter.get("points", []):
            result[point["id"]] = {"title": point["title"], "chapter": chapter_name}
    return result


def make_related(crosswalk, senior_points, junior_points):
    """把每一条跨学段线索加上复核档位，再交给两侧页面显示。"""
    senior_related, junior_related = {}, {}
    known = set(senior_points) | set(junior_points)
    review_path = os.path.join(HERE, "crosswalk_review.json")
    with open(review_path, "r", encoding="utf-8") as handle:
        review_doc = json.load(handle)
    review_by_key = {(item["senior"], item["junior"]): item
                     for item in review_doc.get("reviews", [])}
    source_keys = {(pair.get("senior"), junior_id)
                   for pair in crosswalk.get("pairs", [])
                   for junior_id in pair.get("junior", [])}
    if source_keys != set(review_by_key):
        missing = sorted(source_keys - set(review_by_key))
        extra = sorted(set(review_by_key) - source_keys)
        raise ValueError("映射复核记录与现有条目不一致；缺少=%s，多出=%s" % (missing, extra))
    allowed_tiers = {"solid", "loose", "risky"}
    if any(item.get("tier") not in allowed_tiers for item in review_doc.get("reviews", [])):
        raise ValueError("映射复核档位只能是 solid / loose / risky")
    for pair in crosswalk.get("pairs", []):
        senior_id = pair.get("senior")
        junior_ids = pair.get("junior", [])
        if senior_id not in senior_points:
            raise ValueError("映射表引用了不存在的高中知识点：%s" % senior_id)
        if not junior_ids:
            raise ValueError("映射表中的高中知识点没有初中关联：%s" % senior_id)
        for junior_id in junior_ids:
            if junior_id not in junior_points:
                raise ValueError("映射表引用了不存在的初中知识点：%s" % junior_id)
            if junior_id == senior_id or senior_id not in known:
                raise ValueError("跨学段映射无效：%s ↔ %s" % (senior_id, junior_id))
            review = review_by_key[(senior_id, junior_id)]
            page_note = {
                "solid": "可作为复习导航；高中内容会继续展开。",
                "loose": "这是主题衔接提示；两侧的学习范围或深度可能不同。",
                "risky": "此线索需要谨慎比较，页面已列出主要风险。",
            }[review["tier"]]
            senior_related.setdefault(senior_id, []).append({
                "id": junior_id, "title": junior_points[junior_id]["title"],
                "chapter": junior_points[junior_id]["chapter"],
                "href": "junior.html", "note": page_note,
                "tier": review["tier"], "risk": review["risk"],
            })
            junior_related.setdefault(junior_id, []).append({
                "id": senior_id, "title": senior_points[senior_id]["title"],
                "chapter": senior_points[senior_id]["chapter"],
                "href": "index.html", "note": page_note,
                "tier": review["tier"], "risk": review["risk"],
            })
    return senior_related, junior_related


def make_crosswalk_checklist(crosswalk, review_doc, senior_points, junior_points):
    """生成供教师快速浏览的首轮分级复核表，不改变原有映射目标。"""
    labels = {"solid": "稳固衔接", "loose": "主题相关", "risky": "易生误解"}
    review_by_key = {(item["senior"], item["junior"]): item
                     for item in review_doc.get("reviews", [])}
    rows = {tier: [] for tier in labels}
    for pair in crosswalk.get("pairs", []):
        sid = pair["senior"]
        for jid in pair["junior"]:
            item = review_by_key[(sid, jid)]
            hs_title = senior_points[sid]["title"]
            ju_title = junior_points[jid]["title"]
            theme = hs_title + " / " + ju_title
            rows[item["tier"]].append(
                "| □ | %s | `%s` · %s | `%s` · %s | %s | %s |" % (
                    theme,
                    sid, hs_title, jid, ju_title,
                    item["basis"], item["risk"]))
    lines = [
        "# 初高中跨学段学习线索复核清单", "",
        "本表是首轮保守整理，最终教学口径请由教师复核。原有映射维持 **%d 组 / %d 条单项线索**，本轮没有新增或删除目标。" % (
            len(crosswalk.get("pairs", [])), sum(len(p.get("junior", [])) for p in crosswalk.get("pairs", []))),
        "勾选列留给复核人；`稳固衔接`表示学习同一物理量或现象的延续，`主题相关`表示背景衔接，`易生误解`表示页面会显著提示谨慎比较。", ""
    ]
    for tier, label in labels.items():
        lines.extend(["## %s（%d 条）" % (label, len(rows[tier])), "",
                      "| 复核 | 线索 | 高中知识点 | 初中知识点 | 判断依据 | 风险点 |",
                      "|---|---|---|---|---|---|"])
        lines.extend(rows[tier])
        lines.append("")
    lines.extend([
        "## 复核提示", "",
        "- 表中每一行来自 `crosswalk.json` 已有目标；同一个高中知识点连到多个初中知识点时分行展示，便于逐条核对。",
        "- 首轮判断只依据现有知识点标题、定义和学习范围；未声称经过真实师生试用。",
        "- 如果认为某条线索不合适，请在复核后明确指出高中 id、初中 id 和建议处理方式。", ""
    ])
    return "\n".join(lines)


def write_text(path, value):
    """用 UTF-8 写入成品文件，保证中文在常见浏览器中正常显示。"""
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(value)


def write_review_checklist(path, banks):
    """把所有非高置信度题目集中列出，供负责人抽查而非逐题翻代码。"""
    groups = {"low": [], "medium": []}
    for bank in banks:
        for question in bank.get("questions", []):
            review = question.get("review", {})
            confidence = review.get("confidence")
            if confidence in groups:
                groups[confidence].append((bank.get("segment", ""), question, review))
    lines = [
        "# 诊断题自审清单", "",
        "只列出置信度不是 high 的题目。答案倾向来自原知识库 `caught_by` 映射；教师点评题仍需教师确认，不进入系统正确率。", "",
    ]
    for confidence, title in (("low", "低置信度"), ("medium", "中置信度")):
        rows = groups[confidence]
        lines.extend(["## %s（%d 题）" % (title, len(rows)), ""])
        if not rows:
            lines.extend(["无。", ""])
            continue
        for segment, question, review in rows:
            note = review.get("note") or "复核标记：" + ", ".join(
                name for name, ok in review.get("checks", {}).items() if not ok)
            lines.append("- `%s` · %s（%s，%s）：%s；倾向答案：**%s**。" % (
                question.get("id", ""), question.get("point_title", ""),
                segment, question.get("chapter", ""), note, question.get("error_type", "")))
        lines.append("")
    lines.extend(["## 置信度口径", "",
                  "- `high`：四个标签各不相同，题干、答案唯一性、同章干扰项与学段边界检查均通过。",
                  "- `medium`：本章类别不足四类，选项以同章其他错误的类别和原解释区分；答案仍按完整选项唯一匹配，但教学区分度建议抽查。",
                  "- `low`：题干可读性或学段边界自检未通过，建议优先人工查看。", ""])
    write_text(path, "\n".join(lines))
    return {key: len(value) for key, value in groups.items()}


def summarize_diagnostic_banks(banks):
    """汇总分章题库数量、题目类型与跳过原因，便于报告逐项对账。"""
    result = {"questions": [], "skipped": [], "chapters": []}
    for bank in banks:
        questions = bank.get("questions", [])
        result["questions"].extend(questions)
        for skipped in bank.get("skipped", []):
            result["skipped"].append((bank.get("segment", ""), bank.get("chapter", ""),
                                      bank.get("source_file", ""), skipped))
        result["chapters"].append({
            "segment": bank.get("segment", ""), "chapter": bank.get("chapter", ""),
            "source_file": bank.get("source_file", ""), "total": len(questions),
            "auto": sum(1 for item in questions if item.get("judging") == "auto"),
            "teacher": sum(1 for item in questions if item.get("judging") == "teacher"),
            "skipped": len(bank.get("skipped", [])),
        })
    return result


def audit_dimension_evidence(kb_dirs):
    """用正式引擎复算所有量纲证据，并列出仍无法判定的源错误。"""
    summary = {}
    for segment, directory in kb_dirs.items():
        row = {"filled": 0, "preexisting": 0, "failed": []}
        for path in sorted(os.path.join(directory, name) for name in os.listdir(directory)
                           if name.endswith(".json")):
            with open(path, "r", encoding="utf-8") as handle:
                chapter = json.load(handle)
            for point in chapter.get("points", []):
                symbols = point.get("symbols", {})
                for index, error in enumerate(point.get("errors", [])):
                    if error.get("caught_by") != "量纲一致性":
                        continue
                    test = error.get("trap_test")
                    if test and test.get("kind") != "dimension":
                        row["preexisting"] += 1
                        continue
                    expression = error.get("wrong_expr")
                    record = {
                        "segment": segment, "file": os.path.basename(path),
                        "chapter": chapter.get("chapter", "未命名章节"),
                        "point_id": point.get("id", ""),
                        "point_title": point.get("title", ""),
                        "error_index": index + 1, "wrong_expr": expression,
                    }
                    try:
                        lhs_ast, rhs_ast = EX.parse_equation(expression)
                        if lhs_ast[0] != "var":
                            raise ValueError("等號左側不是單一變量，無法指定 lhs_var")
                        result = CHECKS.check_dimension(
                            expression, lhs_ast, rhs_ast, lhs_ast[1], symbols)
                        if result.error:
                            raise ValueError("正式引擎无法算出左右量纲：" + result.error)
                        env = CHECKS.build_env(symbols)
                        lhs = EX.evaluate(lhs_ast, env)
                        rhs = EX.evaluate(rhs_ast, env)
                        lhs_dim, rhs_dim = str(lhs.dim), str(rhs.dim)
                        if result.ok or lhs.dim == rhs.dim:
                            raise ValueError("正式引擎算得左右量綱一致，不能登记为 mismatch")
                    except Exception as exc:
                        if test:
                            raise RuntimeError("已写入的量纲 trap_test 无法复现：%s / 错误 #%d：%s" %
                                               (record["point_id"], record["error_index"], exc)) from exc
                        row["failed"].append((record, str(exc)))
                        continue

                    if test:
                        if (test.get("target") != expression or
                                test.get("lhs_dim") != lhs_dim or
                                test.get("rhs_dim") != rhs_dim or
                                test.get("expect") != "mismatch"):
                            raise RuntimeError("量纲 trap_test 与正式引擎复算不一致：%s / 错误 #%d" %
                                               (record["point_id"], record["error_index"]))
                        row["filled"] += 1
                    else:
                        raise RuntimeError("有正式引擎可判定的量纲错误未回填 trap_test：%s / 错误 #%d" %
                                           (record["point_id"], record["error_index"]))
        summary[segment] = row
    return summary


def read_existing_questions(bank_dirs):
    """读取生成前题目快照，供本批逐题核对是否原样保留。"""
    result = {}
    for directory in bank_dirs:
        for question in load_diagnostic_banks(directory).get("questions", []):
            question_id = question.get("id")
            if not question_id or question_id in result:
                raise RuntimeError("旧题库缺少唯一题目 id：%s" % question_id)
            result[question_id] = question
    return result


def audit_question_types(questions):
    """核对机器题与人工点评题的来源类型没有错配。"""
    failures = []
    for question in questions:
        judging = question.get("judging")
        source_type = question.get("source_caught_by")
        error_type = question.get("error_type")
        if judging == "auto" and source_type != error_type:
            failures.append((question.get("id"), source_type, error_type))
        elif judging == "teacher" and (source_type != "人工审核" or error_type != "概念混淆"):
            failures.append((question.get("id"), source_type, error_type))
    if failures:
        raise RuntimeError("题目来源类型映射不一致：%s" % failures[:20])
    return True


class _PageAuditParser(HTMLParser):
    """提取离线页面中的脚本、站内链接和锚点，供构建后自检。"""
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.hrefs = []
        self.ids = set()
        self._inside_script = False
        self._script_type = ""
        self._script_src = False
        self._script_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "a" and attrs.get("name"):
            self.ids.add(attrs["name"])
        if tag == "a" and attrs.get("href"):
            self.hrefs.append(attrs["href"])
        if tag == "script":
            self._inside_script = True
            self._script_type = attrs.get("type", "").lower()
            self._script_src = bool(attrs.get("src"))
            self._script_parts = []

    def handle_data(self, data):
        if self._inside_script:
            self._script_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._inside_script:
            if not self._script_src and self._script_type != "application/json":
                self.scripts.append("".join(self._script_parts))
            self._inside_script = False
            self._script_parts = []


def audit_generated_pages(package_dir):
    """对交付目录中的 HTML 做 Node 语法检查与本地链接完整性检查。"""
    node = shutil.which("node")
    if not node and os.path.isfile(r"E:\node.exe"):
        node = r"E:\node.exe"
    pages = sorted(os.path.abspath(os.path.join(package_dir, name))
                   for name in os.listdir(package_dir) if name.lower().endswith(".html"))
    parsed = {}
    for page in pages:
        parser = _PageAuditParser()
        with open(page, "r", encoding="utf-8") as handle:
            parser.feed(handle.read())
        parsed[page] = parser

    script_count = 0
    if node:
        for page, parser in parsed.items():
            for script in parser.scripts:
                if not script.strip():
                    continue
                result = subprocess.run([node, "--check", "-"], input=script,
                                        capture_output=True, text=True, encoding="utf-8")
                if result.returncode:
                    raise RuntimeError("Node --check 失败：%s\n%s" %
                                       (page, result.stderr or result.stdout))
                script_count += 1

    missing_paths, missing_anchors, external_links = [], [], []
    for page, parser in parsed.items():
        for href in parser.hrefs:
            link = urlsplit(href)
            if link.scheme or link.netloc:
                if link.scheme in ("http", "https"):
                    external_links.append((page, href))
                continue
            if link.path:
                target = os.path.abspath(os.path.join(
                    os.path.dirname(page), unquote(link.path)))
            else:
                target = page
            if not os.path.isfile(target):
                missing_paths.append((page, href))
                continue
            # 答题入口将 mode=... 放在 # 后作为单页应用参数，并非 DOM 锚点。
            if link.fragment and not link.fragment.startswith("mode=") and target in parsed:
                if unquote(link.fragment) not in parsed[target].ids:
                    missing_anchors.append((page, href))
    if missing_paths or missing_anchors:
        raise RuntimeError("离线页面链接检查失败：断链=%s，缺失锚点=%s" %
                           (missing_paths[:20], missing_anchors[:20]))
    return {"pages": len(pages), "scripts": script_count, "node": bool(node),
            "missing_paths": len(missing_paths), "missing_anchors": len(missing_anchors),
            "external_links": len(external_links)}


def run_frozen_qc_without_overwriting_report():
    """运行冻结质检逻辑，但把报告写入内存，避免覆盖质检方已有文件。"""
    expected_path = os.path.normcase(os.path.abspath(
        os.path.join(FROZEN_QC.OUT_DIR, "质检报告.md")))
    original_open = FROZEN_QC.io.open

    class MemoryReport(io.StringIO):
        # StringIO 默认在 with 结束时关闭；这里保留缓冲区用于确认报告已完整生成。
        def close(self):
            pass

    memory_report = MemoryReport()

    def guarded_open(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if os.path.normcase(os.path.abspath(str(path))) == expected_path and "w" in mode:
            return memory_report
        return original_open(path, *args, **kwargs)

    console = io.StringIO()
    try:
        FROZEN_QC.io.open = guarded_open
        with contextlib.redirect_stdout(console):
            result = FROZEN_QC.main(["qc.py", "--check"])
    finally:
        FROZEN_QC.io.open = original_open
    if result != 0 or not memory_report.getvalue():
        raise RuntimeError("冻结质检未通过，或未能生成内存质检报告：\n" + console.getvalue())
    summary = [line.strip() for line in console.getvalue().splitlines()
               if re.match(r"^(指纹问题|覆盖门槛问题|独立复算不符|成品一致性问题|质量扫描问题|内容自洽性)", line.strip())]
    return "；".join(summary)


def main():
    """完成双学段校验、主页面、自测页、离线包与交付说明。"""
    hs_dir = os.path.join(HERE, "kb")
    junior_dir = os.path.join(HERE, "kb_junior")
    crosswalk_path = os.path.join(HERE, "crosswalk.json")
    with open(crosswalk_path, "r", encoding="utf-8") as handle:
        crosswalk = json.load(handle)
    with open(os.path.join(HERE, "crosswalk_review.json"), "r", encoding="utf-8") as handle:
        review_doc = json.load(handle)

    hs_chapters, hs_report, hs_stats = inspect_kb(hs_dir, "高中")
    junior_chapters, junior_report, junior_stats = inspect_kb(junior_dir, "初中")
    hs_points, junior_points = point_index(hs_chapters), point_index(junior_chapters)
    hs_related, junior_related = make_related(crosswalk, hs_points, junior_points)
    checklist = make_crosswalk_checklist(crosswalk, review_doc, hs_points, junior_points)

    site_dir = os.path.abspath(os.path.join(ROOT, "site"))
    outputs_dir = os.path.abspath(os.path.join(ROOT, "outputs"))
    package_dir = os.path.abspath(os.path.join(ROOT, "outputs", "物理知识库整合版"))
    report_path = os.path.join(package_dir, "构建与自检记录.md")
    previous_report = ""
    if os.path.isfile(report_path):
        with open(report_path, "r", encoding="utf-8") as handle:
            previous_report = handle.read()
    previous_question_record = re.search(
        r"生成前的 (\d+) 道已验收题逐题保留原 JSON 对象，字段未重写；本批新增 (\d+) 道机器判定题，全库现有 (\d+) 道。",
        previous_report)
    previous_question_stats = (tuple(int(value) for value in previous_question_record.groups())
                               if previous_question_record else None)
    hs_bank_dir = os.path.join(HERE, "quiz_bank", "hs")
    junior_bank_dir = os.path.join(HERE, "quiz_bank", "junior")
    previous_questions = read_existing_questions((hs_bank_dir, junior_bank_dir))
    dimension_audit = audit_dimension_evidence({"高中": hs_dir, "初中": junior_dir})
    os.makedirs(site_dir, exist_ok=True)
    os.makedirs(outputs_dir, exist_ok=True)
    os.makedirs(package_dir, exist_ok=True)

    hs_links = {"current": "高中", "senior_href": "index.html",
                "junior_href": "junior.html", "quiz_href": "quiz-hs.html"}
    junior_links = {"current": "初中", "senior_href": "index.html",
                    "junior_href": "junior.html", "quiz_href": "quiz-junior.html"}
    hs_page = SITE.render_page(hs_report, hs_stats, hs_chapters,
                               SITE.SEGMENT["kb"]["title"],
                               SITE.SEGMENT["kb"]["lead"] + SITE.LEAD_TAIL,
                               segment_nav=hs_links, related=hs_related)
    junior_page = SITE.render_page(junior_report, junior_stats, junior_chapters,
                                   SITE.SEGMENT["kb_junior"]["title"],
                                   SITE.SEGMENT["kb_junior"]["lead"] + SITE.LEAD_TAIL,
                                   segment_nav=junior_links, related=junior_related)

    # 两个发布目录使用同一组相对文件名，页面因此可在线部署，也可整文件夹离线打开。
    for directory in (site_dir, outputs_dir, package_dir):
        write_text(os.path.join(directory, "index.html"), hs_page)
        write_text(os.path.join(directory, "高中物理知识库.html"), hs_page)
        write_text(os.path.join(directory, "junior.html"), junior_page)
        write_text(os.path.join(directory, "初中物理知识库.html"), junior_page)

    # 按章节落盘诊断题；不合并跨学段题目，并保留旧题的 JSON 字段对象。
    os.makedirs(hs_bank_dir, exist_ok=True)
    os.makedirs(junior_bank_dir, exist_ok=True)
    hs_banks, junior_banks = [], []
    for filename, _chapter in hs_chapters:
        bank_path = os.path.join(hs_bank_dir, filename)
        hs_banks.append(make_diagnostic_bank(hs_dir, bank_path, "高中", filename))
    for filename, _chapter in junior_chapters:
        bank_path = os.path.join(junior_bank_dir, filename)
        junior_banks.append(make_diagnostic_bank(junior_dir, bank_path, "初中", filename))
    diagnostic_data = summarize_diagnostic_banks(hs_banks + junior_banks)
    rebuilt_questions = {question.get("id"): question
                         for question in diagnostic_data["questions"]}
    changed_questions = [question_id for question_id, old in previous_questions.items()
                         if rebuilt_questions.get(question_id) != old]
    missing_questions = [question_id for question_id in previous_questions
                        if question_id not in rebuilt_questions]
    if changed_questions or missing_questions:
        raise RuntimeError("旧版诊断题必须原样保留；内容变化=%s，缺失=%s" %
                           (changed_questions[:20], missing_questions[:20]))
    preserved_question_count = len(previous_questions)
    new_question_count = len(rebuilt_questions) - preserved_question_count
    audit_question_types(diagnostic_data["questions"])
    all_banks = hs_banks + junior_banks
    review_counts = write_review_checklist(
        os.path.join(outputs_dir, "诊断题自审清单.md"), all_banks)

    hs_quiz_count = build_quiz(hs_dir, "高中", "index.html",
                               os.path.join(site_dir, "quiz-hs.html"), hs_bank_dir)
    junior_quiz_count = build_quiz(junior_dir, "初中", "junior.html",
                                   os.path.join(site_dir, "quiz-junior.html"), junior_bank_dir)
    for filename in ("quiz-hs.html", "quiz-junior.html"):
        shutil.copyfile(os.path.join(site_dir, filename), os.path.join(outputs_dir, filename))
        shutil.copyfile(os.path.join(site_dir, filename), os.path.join(package_dir, filename))

    # 旧衍生页仍由现有速查/学生版模板生成，导航与练习入口统一指向同目录成品。
    hs_links = {"current": "高中", "senior_href": "index.html",
                "junior_href": "junior.html", "quiz_href": "quiz-hs.html"}
    junior_links = {"current": "初中", "senior_href": "index.html",
                    "junior_href": "junior.html", "quiz_href": "quiz-junior.html"}
    if build_lite.build(hs_dir, os.path.join(site_dir, "student.html"), hs_links) != 0:
        raise RuntimeError("学生版重新生成失败")
    build_quick_site.build_page(hs_dir, "高中", os.path.join(site_dir, "quick.html"))
    build_quick_site.build_page(junior_dir, "初中", os.path.join(site_dir, "junior-quick.html"))
    for filename in ("quick.html", "junior-quick.html", "student.html"):
        source = os.path.join(site_dir, filename)
        shutil.copyfile(source, os.path.join(outputs_dir, filename))
        shutil.copyfile(source, os.path.join(package_dir, filename))

    page_audit = audit_generated_pages(package_dir)

    readme = """# 初高中物理知识库整合版

双击 `index.html` 打开高中知识库；页面顶部可切换到初中，或进入练习页。初中页也可以双击 `junior.html`。

两个知识库与两份自测页均为独立 HTML 文件，不依赖网络、账号、安装程序或第三方库。请保持这个文件夹里的页面放在一起，以便学段跳转和测试回看链接正常工作。

例题自测从知识库已有例题抽题。因为答案是开放文本，页面不自动判分；学生看完解析后自评“会做 / 卡住了 / 做错了”。错误诊断按章节提供机器判定题与教师点评题；教师点评题不进入系统正确率。每页将题目数据直接内嵌，不请求外部 JSON。如果浏览器开放本地存储，例题自评记录保存在当前设备；清理浏览器数据或换设备后不会同步。

跨学段内容只提供复习线索，各知识点的定义、学习范围与使用条件请分别查看，并等待教师复核分级。

错误诊断覆盖高中 21 章与初中 18 章；机器可判定题会显示正误，教师点评题只展示待确认参考归类，不进入自动正确率统计。
"""
    write_text(os.path.join(package_dir, "使用说明.md"), readme)

    write_text(os.path.join(junior_bank_dir, "诊断题库状态.json"),
               json.dumps({"segment": "初中", "status": "generated",
                           "question_count": sum(x["total"] for x in diagnostic_data["chapters"]
                                                  if x["segment"] == "初中"),
                           "note": "逐章题库见同目录中与初中知识库章节同名的 JSON 文件。"},
                          ensure_ascii=False, indent=2) + "\n")
    write_text(os.path.join(junior_bank_dir, "待扩展说明.md"),
               "# 初中错误诊断题库\n\n本批已按章节生成诊断题；每道题含机器/教师判定标记和教学自审置信度。"
               "没有 trap_test 证据的自动判定条目及无法构成四个互异选项的条目会跳过，具体见构建与自检记录。\n")

    qc_summary = run_frozen_qc_without_overwriting_report()
    mapping_count = len(crosswalk.get("pairs", []))
    segment_questions = {
        "高中": [q for bank in hs_banks for q in bank.get("questions", [])],
        "初中": [q for bank in junior_banks for q in bank.get("questions", [])],
    }
    category_counts = {}
    for segment, questions in segment_questions.items():
        category_counts[segment] = {}
        for question in questions:
            if question.get("judging") == "auto":
                kind = question.get("error_type", "")
                category_counts[segment][kind] = category_counts[segment].get(kind, 0) + 1
    hs_auto = sum(category_counts["高中"].values())
    junior_auto = sum(category_counts["初中"].values())
    numeric_hs = category_counts["高中"].get("数值代入", 0)
    numeric_junior = category_counts["初中"].get("数值代入", 0)
    dimension_question_count = sum(
        1 for question in diagnostic_data["questions"]
        if (question.get("trap_test") or {}).get("kind") == "dimension")
    verified_dimension_count = sum(row["filled"] for row in dimension_audit.values())
    if dimension_question_count != verified_dimension_count:
        raise RuntimeError("量纲证据与诊断题数量不一致：来源 %d，题库 %d" %
                           (verified_dimension_count, dimension_question_count))
    if new_question_count:
        displayed_preserved = preserved_question_count
        displayed_added = new_question_count
    elif previous_question_stats and previous_question_stats[2] <= len(diagnostic_data["questions"]):
        displayed_preserved = previous_question_stats[0]
        displayed_added = previous_question_stats[1] + (
            len(diagnostic_data["questions"]) - previous_question_stats[2])
    else:
        displayed_preserved = preserved_question_count
        displayed_added = 0

    lines = [
        "## 第四批：量纲类错误执行证据（2026-09-29）", "",
        "本批仅在知识库错误条目中新增 `trap_test` 字段；不改 `wrong`、`why`、`wrong_expr`、`caught_by`、符号表或校验引擎。每条证据都由 `physkit.expr` 与 `physkit.checks.check_dimension` 实际执行得出。", "",
        "### 量纲证据回填统计", "",
        "| 学段 | 本批待测（缺少 trap_test） | 新增 dimension 证据 | 正式引擎无法判定 | 原有其他形态 trap_test 保留 |",
        "|---|---:|---:|---:|---:|",
    ]
    for segment in ("高中", "初中"):
        row = dimension_audit[segment]
        lines.append("| %s | %d | %d | %d | %d |" % (
            segment, row["filled"] + len(row["failed"]), row["filled"],
            len(row["failed"]), row["preexisting"]))
    lines.extend(["", "`lhs_dim` / `rhs_dim` 使用正式引擎从该知识点自己的符号表求得；例如 `L·T^-2` 表示长度除以时间平方。新增记录的 `target` 与原 `wrong_expr` 完全一致，`expect` 固定为 `mismatch`。", "",
                  "### 未能回填的条目", ""])
    failures = [item for segment in ("高中", "初中")
                for item in dimension_audit[segment]["failed"]]
    if failures:
        for record, reason in failures:
            lines.append("- %s · `%s` · %s（%s）/ 错误 #%d，`%s`：%s" % (
                record["segment"], record["file"], record["point_id"],
                record["point_title"], record["error_index"],
                record["wrong_expr"], reason))
    else:
        lines.append("无。")

    lines.extend(["", "### 诊断题变化与类型占比", "",
                  "生成前的 %d 道已验收题逐题保留原 JSON 对象，字段未重写；本批新增 %d 道机器判定题，全库现有 %d 道。" % (
                      displayed_preserved, displayed_added, len(diagnostic_data["questions"])), "",
                  "| 学段 | 机器题总数 | 数值代入 | 量纲一致性 | 数值代入占比 |", "|---|---:|---:|---:|---:|"])
    for segment, total, numeric in (("高中", hs_auto, numeric_hs),
                                    ("初中", junior_auto, numeric_junior)):
        ratio = 100.0 * numeric / total if total else 0.0
        lines.append("| %s | %d | %d | %d | %.1f%% |" % (
            segment, total, numeric,
            category_counts[segment].get("量纲一致性", 0), ratio))
    lines.extend(["", "两学段其余机器判定类型分别为：高中“不等式关系” %d 题、“变化方向” %d 题；初中其他类型 0 题。教师点评题仍标记为 `judging: teacher`、`error_type: 概念混淆`，不参与自动正确率。" % (
        category_counts["高中"].get("不等式关系", 0), category_counts["高中"].get("变化方向", 0)), "",
                  "按本批前的原题量回算，数值代入占比：高中 %d/%d（%.1f%%）→ %d/%d（%.1f%%）；初中 %d/%d（%.1f%%）→ %d/%d（%.1f%%）。" % (
                      numeric_hs, hs_auto - dimension_audit["高中"]["filled"],
                      100.0 * numeric_hs / (hs_auto - dimension_audit["高中"]["filled"]),
                      numeric_hs, hs_auto, 100.0 * numeric_hs / hs_auto,
                      numeric_junior, junior_auto - dimension_audit["初中"]["filled"],
                      100.0 * numeric_junior / (junior_auto - dimension_audit["初中"]["filled"]),
                      numeric_junior, junior_auto, 100.0 * numeric_junior / junior_auto), "",
                  "### 校验与范围", "",
                  "- 高中原知识库：%d/%d 项检查通过；初中：%d/%d 项检查通过。知识点内容与原有校验结论未变。" % (
                      hs_stats["pass"], hs_stats["checks"], junior_stats["pass"], junior_stats["checks"]),
                  "- 新增量纲 trap_test 均已再次由正式引擎复算并核对 `target`、左右量纲和 `expect`；有任一不一致时构建会中止。",
                  "- 旧诊断题题型映射逐条复核：机器题 `source_caught_by == error_type`；教师题 `source_caught_by == 人工审核`。",
                  "- 自审清单当前列出 low %d、medium %d 道；所有非 high 题目逐条列在 `outputs/诊断题自审清单.md`。" % (
                      review_counts["low"], review_counts["medium"]),
                  "- 离线包 %d 个 HTML 页面：%s；本地文件断链 %d、片段锚点缺失 %d、外部网页链接 %d。" % (
                      page_audit["pages"],
                      ("Node --check %d 个内嵌脚本全部通过" % page_audit["scripts"]
                       if page_audit["node"] else "当前环境未找到 Node，未运行脚本语法检查"),
                      page_audit["missing_paths"], page_audit["missing_anchors"],
                      page_audit["external_links"]),
                  "- 构建生成两学段页面与题库；跨学段映射源文件和复核清单不写入、不变更。",
                  "- 冻结质检：%s；质检方原有 `outputs/质检报告.md` 未被覆盖。" % qc_summary,
                  "- 尚未据此宣称完成真实师生试用；本地浏览器点按测试需在可启动浏览器的环境另行完成。", ""])
    section = "\n".join(lines)
    section_marker = "## 第四批：量纲类错误执行证据"
    history = previous_report
    if section_marker in history:
        history = history.split(section_marker, 1)[0].rstrip()
    if history.strip():
        report_text = history.rstrip() + "\n\n" + section
    else:
        report_text = "# 初高中物理知识库构建与自检记录\n\n" + section
    write_text(report_path, report_text)
    print("\n整合版已生成：")
    print("  网站目录：%s" % site_dir)
    print("  离线入口：%s" % os.path.join(package_dir, "index.html"))
    print("  跨学段学习线索：%d 组 / %d 条单项（分级待教师复核）" %
          (mapping_count, sum(len(pair.get("junior", [])) for pair in crosswalk.get("pairs", []))))
    print("  诊断题全库：%d 题（自动 %d，教师点评 %d，跳过 %d）" % (
        len(diagnostic_data["questions"]),
        sum(1 for item in diagnostic_data["questions"] if item["judging"] == "auto"),
        sum(1 for item in diagnostic_data["questions"] if item["judging"] == "teacher"),
        len(diagnostic_data["skipped"])))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("构建中止：%s" % exc)
        sys.exit(1)
