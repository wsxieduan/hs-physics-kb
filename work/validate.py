# -*- coding: utf-8 -*-
"""
validate.py —— 知识库校验入口（命令行）
=====================================

【怎么运行】
    python validate.py                # 校验 kb/ 目录下的全部内容
    python validate.py --quiet        # 只看有问题的条目

【它做两轮检查】

    第一轮 · 结构检查（kb.py）
        字段全不全、id 有没有重复、前后引用存不存在、
        公式里用到的变量有没有在符号表里声明……

    第二轮 · 物理检查（checks.py）
        量纲一致性、单位标注、数值代入、变化方向、
        跨公式一致、极端参数扫描

【退出码】
    0 = 全部通过
    1 = 有未通过项（这样接到自动流程里，一旦出错就会中断）
"""

import os
import sys

# 让脚本无论从哪个目录运行都能找到 physkit 包
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from physkit import kb as KB
from physkit import checks as CH


def main(argv):
    quiet = "--quiet" in argv

    kb_dir = os.path.join(HERE, "kb")
    if len(argv) > 1 and not argv[1].startswith("-"):
        kb_dir = argv[1]

    print("=" * 62)
    print("高中物理知识库 · 自动校验")
    print("知识库目录：%s" % kb_dir)
    print("=" * 62)
    print()

    # ---------- 装载 ----------
    chapters = KB.load_kb(kb_dir)

    # ---------- 第一轮：结构 ----------
    issues, id_map = KB.check_structure(chapters)
    errs = [i for i in issues if i.level == "错误"]
    warns = [i for i in issues if i.level == "警告"]

    print("【第一轮 · 结构检查】")
    if not issues:
        print("  ✔ 未发现问题")
    else:
        for i in issues:
            print("  %s %s" % ("✘" if i.level == "错误" else "!", i))
    print("  小结：%d 个错误，%d 个警告" % (len(errs), len(warns)))
    print()

    # ---------- 第二轮：物理 ----------
    print("【第二轮 · 物理检查】")
    report = KB.run_physics_checks(chapters, id_map)

    entries = []
    for pid, info in report.items():
        rows = []
        for r in info["results"]:
            rows.append(CH.CheckResult(
                r["type"], r["formula"], r["ok"], r["detail"], r.get("note", "")))
        entries.append(("%s · %s" % (info["chapter"], info["title"]), rows))

    text, (total, passed, by_type) = CH.render_report(entries, verbose=not quiet)
    print(text)
    print()

    # ---------- 汇总 ----------
    stats = KB.summarize(report)
    print("=" * 62)
    print("总览")
    print("=" * 62)
    print("  知识点      %d 个（%d 个全部通过）" % (stats["points"], stats["ok_points"]))
    print("  公式        %d 条" % stats["formulas"])
    print("  校验        %d 项，通过 %d 项，通过率 %.1f%%"
          % (stats["checks"], stats["pass"],
             100.0 * stats["pass"] / max(1, stats["checks"])))
    print("  常见错误    %d 条，已实测抓住 %d 条，无法实测 %d 条"
          % (stats["traps"], stats["traps_ok"], stats["traps_unknown"]))
    if stats["trap_failures"]:
        print("              ★ 以下错误声称会被抓住、实测却抓不住：")
        for t in stats["trap_failures"]:
            print("                - %s" % t)
    if errs:
        print("  结构错误    %d 个（必须修掉）" % len(errs))

    ok = ((not errs)
          and stats["pass"] == stats["checks"]
          and not stats["trap_failures"])
    print()
    print("  结论：%s" % ("✔ 全部通过，允许出成品" if ok else "✘ 存在问题，不允许出成品"))
    print("=" * 62)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
