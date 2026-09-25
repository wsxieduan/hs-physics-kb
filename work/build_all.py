# -*- coding: utf-8 -*-
"""统一生成初中、高中知识库与两份离线例题自测页。

脚本先分别重跑原有结构与物理校验；任一知识点、公式或常见错误防线失败，
就停止生成，避免把未通过的内容带进站点。旧速查页不会被本脚本删除或覆盖。
"""

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import build_site as SITE
from build_quiz import build as build_quiz
from build_quiz import make_diagnostic_bank
from physkit import kb as KB


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

    diagnostic_path = os.path.join(HERE, "quiz_bank", "hs", "01_kinematics.json")
    diagnostic_bank = make_diagnostic_bank(hs_dir, diagnostic_path)
    hs_quiz_count = build_quiz(hs_dir, "高中", "index.html",
                               os.path.join(site_dir, "quiz-hs.html"), diagnostic_path)
    junior_quiz_count = build_quiz(junior_dir, "初中", "junior.html",
                                   os.path.join(site_dir, "quiz-junior.html"))
    for filename in ("quiz-hs.html", "quiz-junior.html"):
        shutil.copyfile(os.path.join(site_dir, filename), os.path.join(outputs_dir, filename))
        shutil.copyfile(os.path.join(site_dir, filename), os.path.join(package_dir, filename))

    readme = """# 初高中物理知识库整合版

双击 `index.html` 打开高中知识库；页面顶部可切换到初中，或进入例题自测。初中页也可以双击 `junior.html`。

两个知识库与两份自测页均为独立 HTML 文件，不依赖网络、账号、安装程序或第三方库。请保持这个文件夹里的页面放在一起，以便学段跳转和测试回看链接正常工作。

自测从知识库已有例题抽题。因为答案是开放文本，页面不自动判分；学生看完解析后自评“会做 / 卡住了 / 做错了”。如果浏览器开放本地存储，记录保存在当前设备；如果浏览器限制存储，页面会提示。清理浏览器数据或换设备后不会同步。

跨学段内容只提供复习线索，各知识点的定义、学习范围与使用条件请分别查看，并等待教师复核分级。

错误诊断目前只在高中第一章开放样板；其中机器可判定题会显示正误，教师点评题只展示待确认参考归类，不进入自动正确率统计。
"""
    write_text(os.path.join(package_dir, "使用说明.md"), readme)

    checklist_path = os.path.join(outputs_dir, "跨学段映射复核清单.md")
    write_text(checklist_path, checklist)
    write_text(os.path.join(package_dir, "跨学段映射复核清单.md"), checklist)
    junior_bank_dir = os.path.join(HERE, "quiz_bank", "junior")
    os.makedirs(junior_bank_dir, exist_ok=True)
    write_text(os.path.join(junior_bank_dir, "待扩展说明.md"),
               "# 初中错误诊断题库\n\n本批按计划只制作高中第一章样板，初中诊断题库暂不生成题目。"
               "例题自测仍可正常使用；不能把此空缺理解为已完成初中诊断覆盖。\n")

    mapping_count = len(crosswalk.get("pairs", []))
    report_text = """# 联动升级 · 构建与自检记录

本报告由统一构建脚本生成。构建前分别使用原有知识库结构检查和物理检查；未通过时脚本会停止，不生成新页面。

| 项目 | 高中 | 初中 |
|---|---:|---:|
| 知识点 | %(hs_points)d | %(junior_points)d |
| 公式 | %(hs_formulas)d | %(junior_formulas)d |
| 自动检查 | %(hs_checks)d / %(hs_pass)d 通过 | %(junior_checks)d / %(junior_pass)d 通过 |
| 例题自测题目 | %(hs_quiz)d | %(junior_quiz)d |

- 跨学段线索：%(mapping)d 组原始映射、%(link_count)d 条单项线索；分级统计：稳固 %(solid)d / 主题相关 %(loose)d / 易生误解 %(risky)d，均待教师复核。清单：`D:\\codex\\outputs\\跨学段映射复核清单.md`。
- 错误诊断样板：高中第一章 %(diag_total)d 题，其中 `auto` 自动判定 %(diag_auto)d 题、`teacher` 教师点评待确认 %(diag_teacher)d 题；初中及其他章节本批未扩题。
- 本批修改：`work/build_all.py`、`work/build_quiz.py`、`work/build_site.py`、`work/crosswalk.json`、`work/crosswalk_review.json`；新增 `work/quiz_bank/hs/01_kinematics.json` 与初中待扩展说明；重生成站点 / 离线包中的知识库、答题页面，并新增本复核清单。
- 站点目录：`D:\\codex\\site\\`；双击离线版：`D:\\codex\\outputs\\物理知识库整合版\\index.html`。
- 未改动 `work/physkit/`、`work/qc.py`、`work/validate.py`、知识点 JSON 或题目答案；旧速查版、学生版等既有文件保留原名。
- 自测使用自我评估，不对开放式答案进行自动判分；记录只使用浏览器本地存储。

## 我没做到 / 我不确定

- 映射分级是首轮保守整理，尚未由项目负责人进行教学复核；清单逐条留有勾选位。
- 本批只做高中第一章 18 条诊断样题（%(diag_auto)d 条自动判定、%(diag_teacher)d 条教师点评）；未生成其余高中章节或初中诊断题。
- 若原错误记录没有单独填写 `trap_test`，解析会如实标明证据来自知识库校验分类和原说明；这类题仍需核对校验器实际记录。
- 仅做代码静态与本机离线流程自检，没有真实师生试用，也没有部署线上站点。
""" % {
        "hs_points": hs_stats["points"], "junior_points": junior_stats["points"],
        "hs_formulas": hs_stats["formulas"], "junior_formulas": junior_stats["formulas"],
        "hs_checks": hs_stats["checks"], "hs_pass": hs_stats["pass"],
        "junior_checks": junior_stats["checks"], "junior_pass": junior_stats["pass"],
        "hs_quiz": hs_quiz_count, "junior_quiz": junior_quiz_count,
        "mapping": mapping_count,
        "link_count": sum(len(pair.get("junior", [])) for pair in crosswalk.get("pairs", [])),
        "solid": sum(1 for item in review_doc["reviews"] if item["tier"] == "solid"),
        "loose": sum(1 for item in review_doc["reviews"] if item["tier"] == "loose"),
        "risky": sum(1 for item in review_doc["reviews"] if item["tier"] == "risky"),
        "diag_total": len(diagnostic_bank["questions"]),
        "diag_auto": sum(1 for item in diagnostic_bank["questions"] if item["judging"] == "auto"),
        "diag_teacher": sum(1 for item in diagnostic_bank["questions"] if item["judging"] == "teacher"),
    }
    write_text(os.path.join(package_dir, "构建与自检记录.md"), report_text)
    print("\n整合版已生成：")
    print("  网站目录：%s" % site_dir)
    print("  离线入口：%s" % os.path.join(package_dir, "index.html"))
    print("  跨学段学习线索：%d 组 / %d 条单项（分级待教师复核）" %
          (mapping_count, sum(len(pair.get("junior", [])) for pair in crosswalk.get("pairs", []))))
    print("  诊断样板：%d 题（自动 %d，教师点评 %d）" %
          (len(diagnostic_bank["questions"]),
           sum(1 for item in diagnostic_bank["questions"] if item["judging"] == "auto"),
           sum(1 for item in diagnostic_bank["questions"] if item["judging"] == "teacher")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("构建中止：%s" % exc)
        sys.exit(1)
