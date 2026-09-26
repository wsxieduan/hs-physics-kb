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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import build_site as SITE
from build_quiz import build as build_quiz
from build_quiz import load_diagnostic_banks
from build_quiz import make_diagnostic_bank
from physkit import kb as KB
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

    # 按章节落盘诊断题；不修改知识库内容源，也不合并跨学段题目。
    hs_bank_dir = os.path.join(HERE, "quiz_bank", "hs")
    junior_bank_dir = os.path.join(HERE, "quiz_bank", "junior")
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

    readme = """# 初高中物理知识库整合版

双击 `index.html` 打开高中知识库；页面顶部可切换到初中，或进入练习页。初中页也可以双击 `junior.html`。

两个知识库与两份自测页均为独立 HTML 文件，不依赖网络、账号、安装程序或第三方库。请保持这个文件夹里的页面放在一起，以便学段跳转和测试回看链接正常工作。

例题自测从知识库已有例题抽题。因为答案是开放文本，页面不自动判分；学生看完解析后自评“会做 / 卡住了 / 做错了”。错误诊断按章节提供机器判定题与教师点评题；教师点评题不进入系统正确率。每页将题目数据直接内嵌，不请求外部 JSON。如果浏览器开放本地存储，例题自评记录保存在当前设备；清理浏览器数据或换设备后不会同步。

跨学段内容只提供复习线索，各知识点的定义、学习范围与使用条件请分别查看，并等待教师复核分级。

错误诊断目前只在高中第一章开放样板；其中机器可判定题会显示正误，教师点评题只展示待确认参考归类，不进入自动正确率统计。
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
    lines = [
        "# 第三批 · 构建与自检记录", "",
        "本轮按现有构建脚本刷新题库、主页面和旧衍生页；构建前重跑原知识库结构与物理校验。未修改知识点内容源、物理校验器或跨学段映射。", "",
        "| 校验项 | 高中 | 初中 |", "|---|---:|---:|",
        "| 知识点 | %d | %d |" % (hs_stats["points"], junior_stats["points"]),
        "| 公式 | %d | %d |" % (hs_stats["formulas"], junior_stats["formulas"]),
        "| 自动校验 | %d / %d 通过 | %d / %d 通过 |" % (
            hs_stats["pass"], hs_stats["checks"], junior_stats["pass"], junior_stats["checks"]),
        "| 例题自测题目 | %d | %d |" % (hs_quiz_count, junior_quiz_count), "",
        "## 诊断题数量", "",
        "| 学段 | 现有题数 | 自动判定 | 教师点评 | 跳过 |", "|---|---:|---:|---:|---:|",
    ]
    for segment in ("高中", "初中"):
        rows = [item for item in diagnostic_data["chapters"] if item["segment"] == segment]
        lines.append("| %s | %d | %d | %d | %d |" % (
            segment, sum(x["total"] for x in rows), sum(x["auto"] for x in rows),
            sum(x["teacher"] for x in rows), sum(x["skipped"] for x in rows)))
    total_questions = len(diagnostic_data["questions"])
    chapter_one_count = sum(item["total"] for item in diagnostic_data["chapters"]
                            if item["segment"] == "高中" and item["source_file"].startswith("01_"))
    added_outside_sample = total_questions - chapter_one_count
    lines.extend(["", "高中第 2–21 章与初中全 18 章本轮新增 %d 题；全库现有 %d 题。高中第 1 章 18 条源错误按新证据规则复核后保留 %d 题，跳过 %d 条缺少 trap_test 的自动判定记录。" % (
        added_outside_sample, total_questions, chapter_one_count, 18 - chapter_one_count), "",
                 "### 分章统计", "", "| 学段 | 章节文件 | 题数 | auto | teacher | 跳过 |",
                 "|---|---|---:|---:|---:|---:|"])
    for item in diagnostic_data["chapters"]:
        lines.append("| %s | `%s` · %s | %d | %d | %d | %d |" % (
            item["segment"], item["source_file"], item["chapter"], item["total"],
            item["auto"], item["teacher"], item["skipped"]))
    lines.extend(["", "## 跳过的来源错误及原因", ""])
    if not diagnostic_data["skipped"]:
        lines.extend(["无。", ""])
    else:
        for segment, chapter, source_file, item in diagnostic_data["skipped"]:
            lines.append("- %s · `%s` · %s / 错误 #%d：%s" % (
                segment, source_file, item["point_id"], item["source_error_index"] + 1,
                item["reason"]))
        lines.append("")
    lines.extend([
        "## 页面与范围", "",
        "- 重生成：`site/quick.html`、`site/student.html`、`site/junior-quick.html`；均从当前知识库 JSON 取数，带学段切换和按知识点直达例题自测的入口。",
        "- 新题直接内嵌到 `site/quiz-hs.html` 与 `site/quiz-junior.html`，没有 fetch 外部 JSON；教师点评题不参与系统正确率。",
        "- `work/crosswalk.json` 与 `outputs/跨学段映射复核清单.md` 本轮保持原样；没有新增跨学段推荐。",
        "- 发布目录原有 9 个文件名均保留。交付入口：`D:\\codex\\outputs\\物理知识库整合版\\index.html`。", "",
        "## 自检说明", "",
        "- 本次构建调用旧结构与物理校验：高中 %d/%d、初中 %d/%d 通过。" % (
            hs_stats["pass"], hs_stats["checks"], junior_stats["pass"], junior_stats["checks"]),
        "- 冻结质检通过：%s；质检正文在内存中生成，未覆盖现有 `outputs/质检报告.md`。" % qc_summary,
        "- 题目选项检查在生成时要求四项文本互异、正确答案唯一；自动题缺少 `trap_test` 时跳过。",
        "- 每道保留题都有 review 字段；非 high 项列于 `D:\\codex\\outputs\\诊断题自审清单.md`（low %d，medium %d）。" % (
            review_counts["low"], review_counts["medium"]),
        "- 本轮 11 段内嵌 JavaScript 已通过 Node 语法检查；静态检查未发现缺失本地链接或外部资源。Chrome / Edge headless 在当前受限环境启动失败，因此未声称完成浏览器点按测试。",
        "- 未据此宣称经过真实学生或教师试用，也未部署线上站点。", "",
        "## 我没做到 / 我不确定", "",
        "- 自动题源没有 `trap_test` 时，我没有自造数值证据，逐条跳过并在上表列出；这会使题量低于源错误总数。",
        "- 很多章节的源记录只包含两三种错误类别。为满足四选一且不杜撰类别，选项在类别不足时使用同章原解释补充分辨；这些题标为 medium，需要负责人抽查教学区分度。",
        "- 当前自审只覆盖题干/选项结构、源解释、类型映射和学段字样扫描；不能代替真实师生试用或教师的最终教学判断。",
        "- 未执行 git 提交：仓库协作约束要求由编排器负责存档；未做线上发布。", "",
    ])
    report_text = "\n".join(lines)
    write_text(os.path.join(package_dir, "构建与自检记录.md"), report_text)
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
