# -*- coding: utf-8 -*-
"""
kb.py —— 知识库的结构、装载与结构校验
====================================

【这个文件管什么】

1. 定下「一个知识点」应该长什么样（字段规范）
2. 从 kb/ 目录把 JSON 内容读进来
3. 做「结构层」的检查 —— 注意这和 checks.py 的分工：
       checks.py  ——  **物理层**检查（公式对不对、方向对不对）
       kb.py      ——  **结构层**检查（字段有没有漏、前后引用存不存在）
   两层都通过的内容，才允许进入最终交付的网页。

【一个知识点的字段】

    id          唯一编号，如 "kine-01"
    title       标题
    level       难度：基础 / 进阶 / 拓展
    definition  定义（这个量到底是什么）
    meaning     物理意义（为什么要有这个概念、它描述了什么）
    symbols     符号表 {变量名: {desc, unit}} —— 公式里出现的变量都必须在这里
    scenarios   情境表 {情境名: {变量名: 数值}} —— 数值检查用的一组自洽参数
    formulas    公式列表，每条含 name / expr / when / checks
    derivation  推导要点（字符串数组，一条一步）
    errors      常见错误列表 {wrong, why, caught_by}
    example     典型例题 {stem, solution[], answer}
    prereq      前置知识点 id
    next        后续知识点 id
    tags        标签（给检索和分类用）

作者备注：本文件只依赖 Python 标准库。
"""

import json
import os
import re

from . import expr as EX
from . import checks as CH
from .quantity import parse_unit, UnitError

# 知识点必须有的字段（缺一个就报结构性错误）
REQUIRED_FIELDS = ("id", "title", "definition", "symbols", "formulas")

# 公式记录必须有的字段
REQUIRED_FORMULA_FIELDS = ("name", "expr")

# 合法的难度等级
LEVELS = ("基础", "进阶", "拓展")

# 「人工审核」是允许的 caught_by 取值 —— 表示这条错误不属于自动检查的射程，
# 明确承认自动化的边界，而不是硬凑一个能跑的检查上去。
MANUAL_REVIEW = "人工审核"

# id 的格式：小写字母 + 短横线 + 数字，例如 kine-01
ID_PATTERN = re.compile(r"^[a-z]+-[0-9]{2}$")


# ============================================================
# 一、装载
# ============================================================

def load_kb(kb_dir):
    """
    读取 kb 目录下所有 .json 文件。

    返回 [(文件名, 章节数据), ...]，按文件名排序（所以文件请用 01_ 02_ 开头命名）。
    """
    if not os.path.isdir(kb_dir):
        raise IOError("找不到知识库目录：%s" % kb_dir)

    out = []
    for name in sorted(os.listdir(kb_dir)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        path = os.path.join(kb_dir, name)
        with open(path, "r", encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except ValueError as exc:
                raise ValueError("%s 不是合法的 JSON：%s" % (name, exc))
        out.append((name, data))
    if not out:
        raise IOError("知识库目录里一个 .json 都没有：%s" % kb_dir)
    return out


# ============================================================
# 一·五、常见错误的"抓取实测"
# ============================================================
# 【为什么要有这一节】
#
# 每个知识点里都列了若干"常见错误"，并声称"这条错误会被 XX 检查抓住"。
# 但这句话本身可能是错的 —— 也许我们以为量纲能抓，其实那条错写法的量纲是对的。
#
# 所以这里做一件比较狠的事：
#     **把每条错误写法真的当成一条公式跑一遍那类检查，看它是不是真的被判为不通过。**
# 判为不通过 = 声称成立；判为通过 = 这条错误的"防线"是假的，必须修。
#
# 这是整条流水线里最"自证"的一环：它在证明校验器真的在干活，
# 而不是写了一句好听的话摆在那里。

def _trap_type_key(name):
    """把 caught_by 写成中文名或英文类型名都能认。"""
    if not name:
        return None
    if name in CH.CHECK_NAMES:
        return name
    for k, v in CH.CHECK_NAMES.items():
        if v == name:
            return k
    return None


def _missing_vars(ast, symbols, values):
    """列出某个表达式要算出数值、还缺哪些变量。"""
    need = EX.collect_vars(ast)
    miss = []
    for name in sorted(need):
        if name in EX.CONSTANTS:
            continue
        if name not in symbols:
            continue
        if values.get(name, None) is None:
            miss.append(name)
    return miss


def _auto_numeric_trap(wlhs, wrhs, symbols, scenarios):
    """
    数值类错误的"自动实测"。

    做法最直白也最彻底：**把这条错误写法原样代进知识库里的各个情境，
    看等号左边算出来和右边算出来是不是一样。**
    对不上，就说明这条错误写法确实算错数 —— 数值代入检查能抓住它。

    之所以要这样验，是因为"量纲对但公式错"的那一类错误（例如 ½at² 漏了 ½）
    量纲检查完全无能为力，只能靠代数字。而代数字这件事机器做得比人可靠。
    """
    if not scenarios:
        return None, "这个知识点没有情境参数，无法自动实测"

    computed = 0          # 有多少个情境真的算出了两边的数
    skipped = None

    for name, vals in scenarios.items():
        miss = sorted(set(_missing_vars(wlhs, symbols, vals))
                      | set(_missing_vars(wrhs, symbols, vals)))
        if miss:
            skipped = skipped or ("情境「%s」缺少这些变量的值：%s"
                                  % (name, "、".join(miss)))
            continue
        try:
            env = CH.build_env(symbols, vals)
            a = EX.evaluate(wlhs, env).value
            b = EX.evaluate(wrhs, env).value
        except Exception as exc:
            skipped = skipped or ("代入情境「%s」时出错：%s" % (name, exc))
            continue
        if a is None or b is None:
            continue

        computed += 1
        scale = max(abs(a), abs(b), 1.0)
        if abs(a - b) > 1e-6 * scale:
            return True, ("代进情境「%s」：左边 %s = %.6g，右边 %s = %.6g，两边对不上"
                          " —— 数值代入能抓住它"
                          % (name, EX.to_plain(wlhs), a, EX.to_plain(wrhs), b))

    if computed:
        return False, ("★ 把这条错误写法代进全部 %d 个情境，左右两边居然都对得上 —— "
                       "说明这个情境参数选得不好（把错误掩盖了），该防线实际上是空的" % computed)
    return None, (skipped or "现有情境都没法算出这条写法的数值")


def verify_traps(chapters):
    """
    逐条实测「常见错误」是否真的能被声称的那类检查抓住。

    返回 {(知识点id, 错误序号): {"caught": True/False/None, "by": 类型, "detail": 说明}}
        caught = True   声称成立，该错写法确实被抓住
        caught = False  ★ 声称不成立，这条防线是假的（属于内容缺陷）
        caught = None   没法自动实测（缺 wrong_expr / trap_test，或公式本身跑不通）
    """
    out = {}

    for fname, chapter in chapters:
        for p in chapter.get("points", []):
            pid = p.get("id", "?")
            symbols = p.get("symbols", {})
            scenarios = p.get("scenarios", {})

            for idx, e in enumerate(p.get("errors", [])):
                key = (pid, idx)
                by = e.get("caught_by", "")
                ctype = _trap_type_key(by)
                wrong = e.get("wrong_expr")
                tt = e.get("trap_test", {}) or {}

                # 明确标为「人工审核」的，不参与自动实测 —— 这是有意为之，
                # 代表我们清楚哪些错误落在自动化的射程之外。
                if by == MANUAL_REVIEW:
                    out[key] = {"caught": None, "by": by, "detail":
                                "已明确标注为概念性错误，不在自动检查的射程内，需人工审核"}
                    continue

                if not wrong:
                    out[key] = {"caught": None, "by": by, "detail":
                                "没写机器可读的错误式（wrong_expr），无法实测"}
                    continue
                if ctype is None:
                    out[key] = {"caught": None, "by": by, "detail":
                                "没写清是哪类检查（caught_by），无法实测"}
                    continue

                # --- 解析那条错误写法 ---
                try:
                    wlhs, wrhs = EX.parse_equation(wrong)
                except EX.ExprError as exc:
                    out[key] = {"caught": None, "by": by,
                                "detail": "错误写法本身解析不了：%s" % exc}
                    continue

                lv = list(EX.collect_vars(wlhs))
                lvar = lv[0] if len(lv) == 1 else "?"
                used = EX.collect_vars(wlhs) | EX.collect_vars(wrhs)
                bad = [v for v in sorted(used)
                       if v not in symbols and v not in EX.CONSTANTS
                       and v not in CH.GREEK_REVERSE]
                if bad:
                    out[key] = {"caught": None, "by": by,
                                "detail": "错误写法用到未声明的变量 %s，无法实测"
                                          % "、".join(bad)}
                    continue

                # --- 按声称的类型，真的跑一遍 ---
                try:
                    if ctype in ("dimension", "unit"):
                        r = CH.check_dimension("【错写法】" + str(e.get("wrong", ""))[:40],
                                               wlhs, wrhs, lvar, symbols)
                    elif ctype == "numeric":
                        if "ref" in tt:
                            # 指定了参考值：按指定的情境和参考值实测
                            spec = dict(tt)
                            spec["_scenario_values"] = scenarios.get(tt.get("scenario"), {})
                            r = CH.check_numeric("【错写法】", wlhs, wrhs, lvar, symbols, spec)
                            caught = not r.ok
                            if caught:
                                detail = r.detail
                            else:
                                detail = ("★ 实测发现这条错写法居然通过了「%s」检查 —— 该防线是假的"
                                          % r.type_name)
                        else:
                            # 没指定参考值：自动把错误写法代进各个情境，看左右两边对不对得上
                            caught, detail = _auto_numeric_trap(wlhs, wrhs, symbols, scenarios)
                        out[key] = {"caught": caught, "by": by, "detail": detail}
                        continue
                    elif ctype == "direction":
                        spec = dict(tt)
                        spec["_scenario_values"] = scenarios.get(tt.get("scenario"), {})
                        r = CH.check_direction("【错写法】", wrhs, symbols, spec, lvar)
                    elif ctype == "compare":
                        spec = dict(tt)
                        spec["_scenario_values"] = scenarios.get(tt.get("scenario"), {})
                        r = CH.check_compare("【错写法】", wrhs, symbols, spec)
                    else:
                        out[key] = {"caught": None, "by": by, "detail":
                                    "%s 这类检查暂时没有实测手段" % by}
                        continue
                except Exception as exc:
                    out[key] = {"caught": None, "by": by,
                                "detail": "实测时出错：%s" % exc}
                    continue

                # 检查判为"不通过" → 说明这条错写法确实被抓住了
                out[key] = {
                    "caught": (not r.ok),
                    "by": by,
                    "detail": r.detail if not r.ok else
                              "★ 实测发现这条错写法居然通过了「%s」检查 —— 该防线是假的" % r.type_name,
                }

    return out


# ============================================================
# 二、结构层检查
# ============================================================
class StructureIssue:
    def __init__(self, where, level, message):
        self.where = where
        self.level = level          # "错误" / "警告"
        self.message = message

    def __str__(self):
        return "[%s] %s —— %s" % (self.level, self.where, self.message)


def check_structure(chapters):
    """
    检查所有知识点的结构完整性。返回 (issues, id_to_point)。

    issues 里 level == "错误" 的条目必须修掉，否则不允许出成品。
    """
    issues = []
    id_to_point = {}
    id_to_where = {}

    for fname, chapter in chapters:
        cname = chapter.get("chapter", fname)
        points = chapter.get("points", [])
        if not points:
            issues.append(StructureIssue(cname, "错误", "这个章节里一个知识点都没有"))
            continue

        for p in points:
            pid = p.get("id", "(没有 id)")
            where = "%s / %s" % (cname, p.get("title", pid))

            # --- 必填字段 ---
            for f in REQUIRED_FIELDS:
                if f not in p or p[f] in ("", [], {}, None):
                    issues.append(StructureIssue(where, "错误", "缺少必填字段 %s" % f))

            # --- id 格式与重复 ---
            if not ID_PATTERN.match(str(pid)):
                issues.append(StructureIssue(
                    where, "错误", "id %r 格式不对（应形如 kine-01：小写字母-两位数字）" % pid))
            if pid in id_to_point:
                issues.append(StructureIssue(
                    where, "错误", "id %r 和「%s」重复了" % (pid, id_to_where[pid])))
            else:
                id_to_point[pid] = p
                id_to_where[pid] = where

            # --- 难度值 ---
            lv = p.get("level", "基础")
            if lv not in LEVELS:
                issues.append(StructureIssue(
                    where, "警告", "难度 %r 不在 %s 里" % (lv, "/".join(LEVELS))))

            # --- 符号表 ---
            symbols = p.get("symbols", {})
            if not isinstance(symbols, dict) or not symbols:
                issues.append(StructureIssue(where, "错误", "符号表是空的"))
                symbols = {}
            for sname, sinfo in symbols.items():
                if not isinstance(sinfo, dict):
                    issues.append(StructureIssue(
                        where, "错误", "符号 %s 的说明不是对象" % sname))
                    continue
                if not sinfo.get("desc"):
                    issues.append(StructureIssue(
                        where, "警告", "符号 %s 没有写中文说明" % sname))
                try:
                    parse_unit(sinfo.get("unit", ""))
                except UnitError as exc:
                    issues.append(StructureIssue(
                        where, "错误", "符号 %s 的单位有问题：%s" % (sname, exc)))
                if sname in EX.CONSTANTS:
                    issues.append(StructureIssue(
                        where, "错误", "符号 %s 是内置常量名，不要重复声明" % sname))

            # --- 公式 ---
            formulas = p.get("formulas", [])
            if not isinstance(formulas, list) or not formulas:
                issues.append(StructureIssue(where, "错误", "一个公式都没有"))
                continue
            seen_names = set()
            for rec in formulas:
                for f in REQUIRED_FORMULA_FIELDS:
                    if f not in rec:
                        issues.append(StructureIssue(
                            where, "错误", "公式记录缺少字段 %s" % f))
                fname = rec.get("name", "?")
                if fname in seen_names:
                    issues.append(StructureIssue(
                        where, "错误", "公式名 %r 在同一个知识点里重复" % fname))
                seen_names.add(fname)

                expr_text = rec.get("expr", "")
                try:
                    EX.parse_equation(expr_text)
                except EX.ExprError as exc:
                    issues.append(StructureIssue(
                        where, "错误", "公式「%s」解析不了：%s" % (fname, exc)))
                    continue

                # 公式里用到的变量必须都在符号表里
                lhs, rhs = EX.parse_equation(expr_text)
                used = EX.collect_vars(lhs) | EX.collect_vars(rhs)
                for v in sorted(used):
                    if v in EX.CONSTANTS:
                        continue
                    if v in EX.GREEK and EX.GREEK[v] in symbols:
                        continue                    # theta 与 θ 视为同一个
                    if CH.GREEK_REVERSE.get(v, v) in symbols:
                        continue
                    if v not in symbols:
                        issues.append(StructureIssue(
                            where, "错误",
                            "公式「%s」用到变量 %s，但符号表里没有声明" % (fname, v)))

                # 声明的检查类型必须是认识的
                for spec in rec.get("checks", []):
                    ct = spec.get("type")
                    if ct not in CH.CHECK_NAMES:
                        issues.append(StructureIssue(
                            where, "错误",
                            "公式「%s」里有不认识的检查类型 %r" % (fname, ct)))
                    if ct in ("numeric", "direction", "consistency",
                              "compare") and not spec.get("scenario"):
                        issues.append(StructureIssue(
                            where, "错误", "公式「%s」的 %s 检查没有指定 scenario" % (fname, ct)))

            # --- 情境表 ---
            scenarios = p.get("scenarios", {})
            for sc_name, sc_vals in scenarios.items():
                for v in sc_vals:
                    if v not in symbols:
                        issues.append(StructureIssue(
                            where, "错误",
                            "情境「%s」里给了变量 %s 的值，但符号表里没有它" % (sc_name, v)))

            # --- 常见错误（这里只做字段检查，"能不能真的抓住"由 verify_traps 实测）---
            for e in p.get("errors", []):
                if not e.get("wrong"):
                    issues.append(StructureIssue(where, "警告", "有一条常见错误没写「错误写法」"))
                cb = e.get("caught_by")
                if cb and cb != MANUAL_REVIEW and _trap_type_key(cb) is None:
                    issues.append(StructureIssue(
                        where, "警告",
                        "常见错误的 caught_by=%r 不是已知检查类型" % cb))

    # --- 跨知识点的前后置引用 ---
    for pid, p in id_to_point.items():
        for key in ("prereq", "next"):
            for ref in p.get(key, []):
                if ref not in id_to_point:
                    issues.append(StructureIssue(
                        id_to_where[pid], "错误",
                        "%s 引用了不存在的知识点 %r" % (key, ref)))

    return issues, id_to_point


# ============================================================
# 三、物理层检查（调 checks.py 跑全套）
# ============================================================

def build_formula_index(chapters):
    """
    建立「全局公式索引」，供"跨公式一致"检查使用。

    键有两种写法，都能查到同一条公式：
        "kine-05|速度位移关系"   —— 全限定名，永远唯一，推荐
        "速度位移关系"           —— 短名，仅当全库只有一条同名公式时才登记

    因为"跨公式一致"是本流水线最值钱的检查（两条公式互相印证），
    所以索引必须做成全库范围的，不能只在单个知识点内找。
    """
    index = {}
    short_names = {}

    for fname, chapter in chapters:
        for p in chapter.get("points", []):
            pid = p.get("id", "?")
            for rec in p.get("formulas", []):
                try:
                    lhs, rhs = EX.parse_equation(rec.get("expr", ""))
                except Exception:
                    continue
                lv = list(EX.collect_vars(lhs))
                entry = {
                    "rhs": rhs, "lhs": lhs,
                    "lhs_var": lv[0] if len(lv) == 1 else "?",
                    "expr": rec.get("expr", ""),
                    "point": pid,
                }
                short = rec.get("name", "?")
                qname = "%s|%s" % (pid, short)
                index[qname] = entry
                short_names.setdefault(short, []).append(qname)

    # 短名唯一时才登记，避免歧义
    for short, qnames in short_names.items():
        if len(qnames) == 1:
            index[short] = index[qnames[0]]

    return index


def run_physics_checks(chapters, id_to_point=None):
    """
    对每个知识点的每条公式跑完整校验。

    - id_to_point 参数保留是为了兼容调用方的签名，内部不使用
    - 返回 {知识点 id: {...一条知识点的完整渲染数据 + 校验结果...}}
    """
    report = {}
    formula_index = build_formula_index(chapters)      # 先建全库索引
    trap_all = verify_traps(chapters)                  # 再实测所有"常见错误"

    for fname, chapter in chapters:
        cname = chapter.get("chapter", fname)
        for p in chapter.get("points", []):
            pid = p.get("id", "?")
            symbols = p.get("symbols", {})
            scenarios = p.get("scenarios", {})
            formulas = p.get("formulas", [])

            all_results = []
            fmap = {}
            for rec in formulas:
                results, info = CH.run_formula_checks(
                    rec, symbols, scenarios, formula_index)
                all_results.extend(results)

                entry = {
                    "name": rec.get("name", "?"),
                    "expr": rec.get("expr", ""),
                    "when": rec.get("when", ""),
                    "results": [r.as_dict() for r in results],
                }
                try:
                    lhs, rhs = EX.parse_equation(rec.get("expr", ""))
                    entry["mathml"] = EX.equation_to_mathml(lhs, rhs)
                    entry["plain"] = EX.equation_to_plain(lhs, rhs)
                except Exception as exc:
                    entry["mathml"] = ""
                    entry["plain"] = rec.get("expr", "")
                    entry["parse_error"] = str(exc)
                fmap[entry["name"]] = entry

            # 符号表的 MathML 排版
            sym_render = []
            for sname, sinfo in symbols.items():
                sym_render.append({
                    "name": sname,
                    "mathml": EX._symbol_mathml(sname),
                    "desc": sinfo.get("desc", ""),
                    "unit": sinfo.get("unit", ""),
                })

            report[pid] = {
                "id": pid,
                "title": p.get("title", pid),
                "chapter": cname,
                "level": p.get("level", "基础"),
                "definition": p.get("definition", ""),
                "meaning": p.get("meaning", ""),
                "derivation": p.get("derivation", []),
                "errors": _attach_traps(p.get("errors", []), pid, trap_all),
                "example": p.get("example", {}),
                "tags": p.get("tags", []),
                "prereq": p.get("prereq", []),
                "next": p.get("next", []),
                "symbols": sym_render,
                "formulas": [fmap[rec["name"]] for rec in formulas if rec.get("name") in fmap],
                "results": [r.as_dict() for r in all_results],
                "ok": all(r.ok for r in all_results),
                "n_total": len(all_results),
                "n_pass": sum(1 for r in all_results if r.ok),
            }

    return report


def _attach_traps(errors, pid, trap_all):
    """把实测结果挂到每条常见错误上，供网页展示。"""
    out = []
    for idx, e in enumerate(errors):
        item = dict(e)
        v = trap_all.get((pid, idx))
        if v:
            item["verify"] = v
        out.append(item)
    return out


def summarize(report):
    """算一些用于展示的统计数字。"""
    n_points = len(report)
    n_formulas = sum(len(v["formulas"]) for v in report.values())
    n_checks = sum(v["n_total"] for v in report.values())
    n_pass = sum(v["n_pass"] for v in report.values())
    n_ok_points = sum(1 for v in report.values() if v["ok"])
    by_type = {}
    for v in report.values():
        for r in v["results"]:
            d = by_type.setdefault(r["type"], [0, 0])
            d[0] += 1
            if r["ok"]:
                d[1] += 1

    # 常见错误的实测统计
    n_traps = n_trap_ok = n_trap_unknown = 0
    trap_failures = []
    for v in report.values():
        for e in v["errors"]:
            ver = e.get("verify")
            if not ver:
                continue
            n_traps += 1
            if ver["caught"] is True:
                n_trap_ok += 1
            elif ver["caught"] is None:
                n_trap_unknown += 1
            else:
                trap_failures.append("%s：%s" % (v["title"], e.get("wrong", "")))

    return {
        "points": n_points,
        "formulas": n_formulas,
        "checks": n_checks,
        "pass": n_pass,
        "ok_points": n_ok_points,
        "by_type": by_type,
        "traps": n_traps,
        "traps_ok": n_trap_ok,
        "traps_unknown": n_trap_unknown,
        "trap_failures": trap_failures,
    }
