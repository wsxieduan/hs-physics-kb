# -*- coding: utf-8 -*-
"""
qc.py —— 质检工具（独立于出成品流程）
====================================

【它和 build_site.py 的分工】

    build_site.py —— 生产环节。校验通过就出成品。
                     → 由执行方（Codex）运行

    qc.py         —— 质检环节。假设成品是"别人做的"，去挑毛病。
                     → 由质检方（阿酉）运行

【为什么质检不能只是"再跑一遍校验"】

因为校验器本身是可以被改的。如果执行方为了让内容通过，
把检查放宽了、把阈值调松了、或者把参考值改成和错误公式一致 ——
那么"再跑一遍校验"当然还是全绿，但这毫无意义。

所以质检要查的是**校验器管不到的东西**，一共七项：

    一、指纹     校验器源码有没有被偷偷改动过（改了就要显式登记）
    二、覆盖门槛 每条公式、每个知识点被检查的"深度"够不够
                  —— 防止"内容其实没被真正考过，只是形式上通过"
    三、独立复算 用一套**完全不同的代码**（Python 自带的表达式求值）
                  重算一遍所有数值，看参考值有没有被凑数
    四、成品一致性 网页里的公式数 / 知识点数 / 校验条目数
                  必须和源文件对得上，且不许有任何外部依赖
    五、质量扫描 占位符（TODO/待补）、过短的字段、空字段
    六、内容自洽性 ★ 正文散文里用到的符号，本卡片的符号表里有没有？
                  符号名有没有拿英文单词充数？
                  —— 这一节补的是一个真实盲区：**正文公式不参与任何
                     校验**。校验器只比 `expr` 字段，正文里写什么它不管。
                     于是曾经出现过「正文写 v = lambda f，符号表里却叫
                     wavelength」这种同一个量两个名字的情况，全库没有
                     任何一道检查能发现它（2026-09-19 补上这道检查）。
    七、抽样单   随机抽若干条公式打印出来，交给人**用物理判断力**核
                  —— 机器只能查规则，正确性最终要靠人看一眼

前四项和第七项是本工具的重点。第七项尤其重要：
**机器永远无法发现"两条公式错得一模一样且互相印证"。**

【怎么用】

    python qc.py --freeze    生成基线指纹（交接时刻的封存）
    python qc.py --check     质检并输出报告
    python qc.py --check --sample 8 --seed 123   抽 8 条公式人工核
"""

import ast
import hashlib
import io
import json
import math
import os
import random
import re
import sys
if hasattr(sys.stdout,"reconfigure"):sys.stdout.reconfigure(encoding="utf-8",errors="backslashreplace")
if hasattr(sys.stderr,"reconfigure"):sys.stderr.reconfigure(encoding="utf-8",errors="backslashreplace")

HERE = os.path.dirname(os.path.abspath(__file__))
KB_DIR = os.path.join(HERE, "kb")
OUT_DIR = os.path.abspath(os.path.join(HERE, "..", "outputs"))
BASELINE = os.path.abspath(os.path.join(HERE, "..", "质检基线.json"))

# ============================================================
# 零、冻结清单 —— 哪些文件属于"校验器本体"，不许随便改
# ============================================================
# 执行方可以改的：kb/ 下的内容文件、build_site.py 的排版与展示
# 执行方不能改的：下面这些。真需要改（比如修 bug），必须写进
#                work/校验器变更.md 说明理由，由质检方核对。
FROZEN_PATTERNS = [
    "work/physkit/quantity.py",
    "work/physkit/expr.py",
    "work/physkit/checks.py",
    "work/physkit/kb.py",
    "work/qc.py",          # 质检工具自己也不能被改
    "work/net_guard.py",   # 网络体检：冻结，防止把「不通」改成「通」
    "work/orchestrator.py",  # 协作编排：冻结，防止把「要质检」改成「跳过质检」
]


def repo_root():
    return os.path.abspath(os.path.join(HERE, ".."))


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint():
    """给冻结清单里的每个文件算指纹。"""
    root = repo_root()
    out = {}
    for rel in FROZEN_PATTERNS:
        p = os.path.join(root, rel.replace("/", os.sep))
        out[rel] = sha256_of(p) if os.path.exists(p) else "(文件不存在)"
    return out


# ============================================================
# 一、指纹检查
# ============================================================

def check_fingerprint(baseline):
    """对比当前指纹和基线。返回 (问题列表, 内容)。"""
    now = fingerprint()
    old = baseline.get("fingerprint", {})
    rows = []
    issues = []
    for rel in FROZEN_PATTERNS:
        a, b = old.get(rel), now.get(rel)
        if a is None:
            state = "基线里没有记录"
            issues.append("%s 在基线里没有指纹记录" % rel)
        elif a == b:
            state = "未改动"
        else:
            state = "**已改动**"
            issues.append("%s 被改动过（%s → %s）" % (rel, str(a)[:12], str(b)[:12]))
        rows.append((rel, state, str(a)[:12] if a else "—", str(b)[:12] if b else "—"))
    return issues, rows


# ============================================================
# 二、覆盖门槛 —— 内容被"真正考过"了吗
# ============================================================
# 设计思路：光看"校验全部通过"是没用的，要通过很容易 ——
# 少写几条检查项就行了。所以要给"检查的深度"设下限。

# 每个知识点至少要有的东西
POINT_MIN = {
    "symbols": 1,          # 至少 1 个符号
    "formulas": 1,         # 至少 1 条公式
    "derivation": 3,       # 推导要点至少 3 条
    "errors": 2,           # 常见错误至少 2 条
    "scenarios": 1,        # 至少 1 组情境参数
}
# 每个知识点至少要出现的检查类型及最低条数
POINT_CHECK_MIN = {
    "numeric": 2,          # 数值代入至少 2 项
    "scan": 1,             # 极端参数扫描至少 1 项
    "consistency": 1,      # 跨公式互证至少 1 项
}
# 每个知识点至少要有一条"量纲抓不到、只能靠数值代入"的陷阱
POINT_MIN_NUMERIC_TRAPS = 1

# 文本长度下限（字符数）
MIN_LEN = {
    "definition": 30,
    "meaning": 60,
    "example_stem": 15,
    "answer": 5,
}

# 占位符（出现即警告）
PLACEHOLDER = ("TODO", "FIXME", "待补", "待填", "待定", "待生产", "XXX", "xxx",
               "示例文本", "此处省略", "略）", "…（", "（略")

# 每条公式至少要有的检查类型（自动检查不计入这里说的"声明"）
FORMULA_REQUIRED_CHECK_TYPES = ("numeric", "direction", "consistency", "scan", "compare")


def load_raw_kb():
    """直接读原始 JSON，不经过校验器 —— 质检要独立于生产线。"""
    chapters = []
    for name in sorted(os.listdir(KB_DIR)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        with io.open(os.path.join(KB_DIR, name), encoding="utf-8") as fh:
            chapters.append((name, json.load(fh)))
    return chapters


def check_coverage(chapters):
    """覆盖门槛检查。返回 (问题列表, 统计字典)。"""
    issues = []
    stats = {"points": 0, "formulas": 0, "declared_checks": 0,
             "traps": 0, "traps_numeric": 0, "traps_manual": 0,
             "formulas_without_numeric": 0}

    for fname, ch in chapters:
        cname = ch.get("chapter", fname)
        for p in ch.get("points", []):
            pid = p.get("id", "?")
            where = "%s（%s）" % (pid, cname)
            stats["points"] += 1

            # --- 字段数量下限 ---
            for field, need in POINT_MIN.items():
                got = len(p.get(field) or [])
                if got < need:
                    issues.append("%s 的 %s 只有 %d 条，门槛是 %d"
                                  % (where, field, got, need))

            # --- 文本长度下限 ---
            if len(str(p.get("definition", ""))) < MIN_LEN["definition"]:
                issues.append("%s 的「定义」太短（少于 %d 字）" % (where, MIN_LEN["definition"]))
            if len(str(p.get("meaning", ""))) < MIN_LEN["meaning"]:
                issues.append("%s 的「物理意义」太短（少于 %d 字）" % (where, MIN_LEN["meaning"]))

            ex = p.get("example") or {}
            if len(str(ex.get("stem", ""))) < MIN_LEN["example_stem"]:
                issues.append("%s 的例题题干太短或缺失" % where)
            if len(ex.get("solution") or []) < 2:
                issues.append("%s 的例题解题步骤少于 2 步" % where)
            if len(str(ex.get("answer", ""))) < MIN_LEN["answer"]:
                issues.append("%s 的例题答案缺失或太短" % where)

            # --- 检查类型的最低条数 ---
            by_type = {}
            for f in p.get("formulas", []):
                stats["formulas"] += 1
                for c in f.get("checks", []):
                    ct = c.get("type")
                    by_type[ct] = by_type.get(ct, 0) + 1
                    stats["declared_checks"] += 1
                if not any(c.get("type") == "numeric" for c in f.get("checks", [])):
                    stats["formulas_without_numeric"] += 1
                    issues.append("%s 的公式「%s」一项数值代入检查都没有"
                                  % (where, f.get("name", "?")))
            for ct, need in POINT_CHECK_MIN.items():
                got = by_type.get(ct, 0)
                if got < need:
                    issues.append("%s 的「%s」检查只有 %d 项，门槛是 %d"
                                  % (where, ct, got, need))

            # --- 陷阱门槛 ---
            n_num = 0
            for e in p.get("errors", []):
                stats["traps"] += 1
                cb = e.get("caught_by", "")
                if cb == "人工审核":
                    stats["traps_manual"] += 1
                elif cb == "数值代入":
                    stats["traps_numeric"] += 1
                    n_num += 1
                if cb != "人工审核" and not e.get("wrong_expr"):
                    issues.append("%s 有一条常见错误声称能被「%s」抓住，但没给机器可读的 "
                                  "错误写法（wrong_expr）" % (where, cb))
            if n_num < POINT_MIN_NUMERIC_TRAPS:
                issues.append("%s 一条「量纲抓不到、只能靠数值代入」的陷阱都没有 "
                              "—— 这类才是最难发现的错误" % where)

    return issues, stats


# ============================================================
# 三、独立复算 —— 换一套代码重算一遍
# ============================================================
# 关键点：这里**不使用** physkit 的表达式解析器，
# 改用 Python 自带的 ast + eval。两条代码路径完全不同，
# 所以如果参考值是被"凑"出来的（比如把错误公式的结果当成正确答案），
# 就有可能在这里露馅。

SAFE_NAMES = {
    "sqrt": math.sqrt, "abs": abs, "sin": math.sin, "cos": math.cos,
    "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "exp": math.exp, "log": math.log10, "ln": math.log,
    "pi": math.pi, "π": math.pi, "e": math.e,
}

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Constant,
    ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd,
    ast.Mod, ast.Compare, ast.Lt, ast.Gt, ast.LtE, ast.GtE, ast.Eq, ast.NotEq,
)


# ------------------------------------------------------------
# Python 保留字改名（2026-09-19 补）
# ------------------------------------------------------------
# `lambda`（波长 λ）是完全合法的物理量符号 —— physkit 的 GREEK 词表里
# 就明明白白写着 "lambda": "λ"。但它在 Python 里是保留字，ast.parse 会在
# **语法层**直接拒绝，连环境都轮不到查。
#
# 实测踩过：把 osc-06 的波长符号从 `wavelength` 统一成 `lambda` 之后，
# 独立复算立刻报 3 处 `invalid syntax` —— 生产解析器（physkit 自己写的）
# 能处理这个名字，而这里的 ast 路径不能。
#
# 那是本工具的缺陷，不是内容的错：**独立复算器无法校验含合法符号 lambda
# 的公式，等于把这类公式整个漏掉**。而且这种"漏"很危险 —— 它表现为
# 「独立复算失败」，看多了就会被当成噪音忽略掉。
#
# 修法：求值前把保留字换成安全写法，取名字时再还原。只改名字，不动计算。
_RESERVED = {"lambda": "_lam_"}


def _unreserve(text):
    """把保留字换成安全标识符（lambda -> _lam_，lambda_x -> _lam__x）。"""
    for word, safe in _RESERVED.items():
        text = re.sub(r"(?<![A-Za-z0-9_])%s" % re.escape(word), safe, text)
    return text


def _restore(name):
    """把安全标识符还原成原名字（_lam__x -> lambda_x）。"""
    for word, safe in _RESERVED.items():
        if name.startswith(safe):
            return word + name[len(safe):]
    return name


def safe_eval(text, env):
    """
    用 Python 自带的表达式求值算一条公式的数值。
    只放行白名单里的语法节点，不执行任何别的代码。
    """
    # ^ → **（先把已有的 ** 保护起来，免得变成 ****）
    py = text.replace("**", "\x00").replace("^", "**").replace("\x00", "**")
    py = _unreserve(py)
    tree = ast.parse(py, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError("表达式里出现了不允许的写法：%s" % type(node).__name__)
    names = dict(SAFE_NAMES)
    names.update(env)
    # 改名之后，环境里也要提供对应的安全名
    for word, safe in _RESERVED.items():
        if word in env:
            names[safe] = env[word]
    return eval(compile(tree, "<qc>", "eval"), {"__builtins__": {}}, names)


def split_equation(text):
    if text is None or "=" not in text:
        raise ValueError("不是等式：%r" % text)
    a, b = text.split("=", 1)
    return a.strip(), b.strip()


def check_recompute(chapters, sample_n, seed):
    """独立复算所有数值类检查 + 生成人工抽样单。"""
    from physkit.kb import build_formula_index
    formula_index=build_formula_index(chapters)
    issues = []
    checked = 0
    samples = []

    for fname, ch in chapters:
        cname = ch.get("chapter", fname)
        for p in ch.get("points", []):
            pid = p.get("id", "?")
            where = "%s（%s）" % (pid, cname)
            symbols = p.get("symbols", {})
            scenarios = p.get("scenarios", {})

            for f in p.get("formulas", []):
                fname_ = f.get("name", "?")
                expr = f.get("expr", "")
                for c in f.get("checks", []):
                    ct = c.get("type")

                    # --- 数值代入：独立重算，看参考值对不对得上 ---
                    if ct == "numeric" and "ref" in c:
                        sc = scenarios.get(c.get("scenario"), {})
                        target = c.get("expr") or split_equation(expr)[1]
                        if not all(v in sc for v in _names(target)):
                            issues.append("%s / %s：数值检查引用了情境里没有的变量，"
                                          "质检无法独立复算" % (where, fname_))
                            continue
                        try:
                            got = safe_eval(target, sc)
                            checked += 1
                        except Exception as exc:
                            issues.append("%s / %s：独立复算失败（%s）"
                                          % (where, fname_, exc))
                            continue
                        ref = c["ref"]
                        if not __import__("physkit.checks", fromlist=["comparison"]).comparison(got, ref, c)[0]:
                            issues.append(
                                "**参考值可疑** %s / %s：独立重算得 %.6g，"
                                "但源文件写的参考值是 %.6g（差 %.3g）"
                                % (where, fname_, got, ref, got - ref))

                    # --- 跨公式一致：两边独立重算 ---
                    if ct == "consistency":
                        sc = scenarios.get(c.get("scenario"), {})
                        ea = c.get("expr") or split_equation(expr)[1]
                        eb = c.get("with_expr")
                        if not eb:
                            reference=formula_index.get(c.get("with"))
                            if not reference:
                                issues.append("一致性参考公式不存在："+str(c.get("with")));continue
                            eb=split_equation(reference["expr"])[1]
                        if not (all(v in sc for v in _names(ea))
                                and all(v in sc for v in _names(eb))):
                            issues.append("%s / %s：跨公式一致检查引用了情境里没有的变量"
                                          % (where, fname_))
                            continue
                        try:
                            a1, b1 = safe_eval(ea, sc), safe_eval(eb, sc)
                            checked += 1
                        except Exception as exc:
                            issues.append("%s / %s：跨公式一致独立复算失败（%s）"
                                          % (where, fname_, exc))
                            continue
                        if not __import__("physkit.checks", fromlist=["comparison"]).comparison(a1, b1, c)[0]:
                            issues.append("**互证不成立** %s / %s：独立重算两边分别是 %.6g 和 %.6g"
                                          % (where, fname_, a1, b1))

                # --- 抽样单：把这一类公式挑出来给人看 ---
                samples.append({
                    "chapter": cname, "pid": pid, "point": p.get("title", ""),
                    "formula": fname_, "expr": expr,
                    "when": f.get("when", ""),
                    "symbols": {k: v.get("unit", "") for k, v in symbols.items()},
                    "scenarios": scenarios,
                    "formulas_of_point": [x.get("name") for x in p.get("formulas", [])],
                    "another_expr_in_point": _other_expr(p, fname_),
                })

    rnd = random.Random(seed)
    picked = rnd.sample(samples, min(sample_n, len(samples))) if samples else []
    return issues, checked, picked


def _names(text):
    """取出表达式里用到的所有名字，跳过函数名和常量。"""
    py = _unreserve(text.replace("^", "**"))
    try:
        tree = ast.parse(py, mode="eval")
    except SyntaxError:
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(_restore(node.id))
    return {n for n in out if n not in SAFE_NAMES}


def _other_expr(point, this_name):
    """同一个知识点里另一条公式的表达式（抽样核验时用来做交叉判断）。"""
    for f in point.get("formulas", []):
        if f.get("name") != this_name:
            return "%s：%s" % (f.get("name"), f.get("expr"))
    return ""


# ============================================================
# 四、成品一致性
# ============================================================

def check_output(chapters):
    """检查生成的网页和源文件对不对得上，以及有没有偷偷联外网。"""
    issues = []
    info = {}
    path = os.path.join(OUT_DIR, "高中物理知识库.html")
    if not os.path.exists(path):
        return ["找不到成品文件：%s" % path], {}

    with io.open(path, encoding="utf-8") as fh:
        html = fh.read()

    size_kb = os.path.getsize(path) / 1024.0
    info["体积"] = "%.1f KB" % size_kb
    # ★ 2026-09-19：体积约束由主人明确放宽 ——「大小不重要，重要的是成品的效果，
    # 不出 bug 就行」。放宽的原因：做到第 4 章时 300 KB 已经见底，执行方为了压体积
    # 反复精简内容，反而把「覆盖门槛」拖下水（mom-01 / mom-05 未达标）。
    # 体积不是正确性，不该让它挤压内容深度。现只留一个很宽的防呆上限（8 MB），
    # 仅用于防止失控膨胀，正常交付碰不到。变更理由见 work/校验器变更.md。
    # ★ 2026-10-04 二次放宽（2 MB → 8 MB）：主人明确「质量第一，占多少 MB 无所谓」。
    #   背景：高中完整版 2026-10-04 已到 1999.1 KB，距 2 MB 上限只剩 49 KB，
    #   再补内容是必然的，继续卡着只会逼执行方为省空间牺牲内容深度。
    if size_kb > 8192:
        issues.append("成品 %.1f KB，超过 8 MB 的防呆上限（体积已放宽，此项仅防失控）" % size_kb)

    # 外部依赖（硬约束：断网可用）
    ext = len(re.findall(r'(?:src|href)\s*=\s*["\']https?://', html))
    ext += len(re.findall(r'@import\s+url\(', html))
    info["外部引用"] = ext
    if ext:
        issues.append("成品里有 %d 处外部引用 —— 违反了「断网可用」的硬约束" % ext)

    # 数量必须与源文件一致
    n_points = sum(len(ch.get("points", [])) for _, ch in chapters)
    n_formulas = sum(len(p.get("formulas", []))
                     for _, ch in chapters for p in ch.get("points", []))
    n_traps = sum(len(p.get("errors", []))
                  for _, ch in chapters for p in ch.get("points", []))
    got = {
        "知识点": (html.count('class="kp"'), n_points),
        "公式": (html.count('class="fbox"'), n_formulas),
        "常见错误": (html.count('class="trap"'), n_traps),
    }
    for k, (a, b) in got.items():
        info[k] = "%d（源文件 %d）" % (a, b)
        if a != b:
            issues.append("成品里的%s数是 %d，但源文件里有 %d —— 对不上" % (k, a, b))

    # 必要的结构元素
    for need, why in [("<math", "数学公式排版"),
                      ("<style", "内联样式"),
                      ("<script", "内联脚本")]:
        if need not in html:
            issues.append("成品里找不到 %s（%s）" % (need, why))

    return issues, info


# ============================================================
# 五、质量扫描（占位符、空字段）
# ============================================================

def check_quality(chapters):
    issues = []
    for fname, ch in chapters:
        for p in ch.get("points", []):
            pid = p.get("id", "?")
            blob = json.dumps(p, ensure_ascii=False)
            for bad in PLACEHOLDER:
                if bad in blob:
                    issues.append("%s 里出现了占位符文字「%s」" % (pid, bad))
            for sname, sinfo in (p.get("symbols") or {}).items():
                if not sinfo.get("desc"):
                    issues.append("%s 的符号 %s 没有中文说明" % (pid, sname))
                if "unit" not in sinfo:
                    issues.append("%s 的符号 %s 没有写单位字段" % (pid, sname))
            for e in p.get("errors", []):
                if len(str(e.get("why", ""))) < 30:
                    issues.append("%s 有一条常见错误的解释太短（少于 30 字）" % pid)
    return issues


# ============================================================
# 六、内容自洽性（正文里用到的符号，符号表里有没有）
# ============================================================
#
# 【为什么需要这一节】
#
# 校验器比的是每条公式的 `expr` 字段，以及符号表本身。正文散文里
# （定义 / 物理意义 / 推导 / 例题）写的公式**不参与任何检查** ——
# 这是流水线的一个真实盲区。
#
# 它造成过什么后果：osc-06 的定义正文写 `v = lambda f`，而同一张卡片的
# 符号表却把波长定义成 `wavelength`，公式 expr 也用 `wavelength`。
# 同一个物理量在一张卡片里有俩名字，页面上会同时出现 λ 和 wavelength。
# 六道自动检查一道都没发现 —— 因为六道检查都不看正文。
#
# 本节的规则是「按卡片自洽」：正文里用到的量，应当能在**本卡片**的
# 符号表里找到。找不到的分两种处理：
#   · 符号名拿英文单词充数（wavelength）→ 硬问题，拦。
#   · 正文引用了本卡片没定义的量（推导时顺手用到邻近知识点的 θ、ω）
#     → 软提示，不拦，但列出来让人判断。

# 用机器名书写的希腊字母。`pi` 故意不列 —— 它是数学常数，本来就不该
# 出现在符号表里。`delta` 要列 —— `delta_r` / `Delta_v` / `Delta_t`
# 都是合法符号名（增量），不列的话会被下面的「英文单词」规则误判。
# 大写 `Delta` 不列进这里：正文里的「Delta Ep」是增量运算符，不是符号，
# 列进来反而会让它被当成符号去符号表里找。硬检查那边用 .lower() 兜住。
_GREEK_NAMES = [
    "omega", "theta", "lambda", "alpha", "beta", "gamma",
    "sigma", "rho", "tau", "eta", "psi", "zeta", "phi", "mu",
    "delta",
    "Omega", "Theta", "Lambda", "Phi",
]

# 希腊名后面的下标同样可能带多段（delta_E_total 总能量差），吃完整，不要截成 delta_E
_RE_PROSE_GREEK = re.compile(
    r"(?<![A-Za-z])(" + "|".join(_GREEK_NAMES) + r")((?:_[A-Za-z0-9]+)+)?(?![A-Za-z])")

# 下标里可能带好几段：delta_E_total（总能量差）、E_k_s1（状态1的动能）。
# 以前只吃到第一段，于是 delta_E_total 被截成 delta_E，而符号表里没有 delta_E，
# 白白报一条「正文用到的符号没定义」。改成把连续的下标段一次吃完整。
_RE_PROSE_SUB = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+)")


def _prose_fields(p):
    """取出一个知识点里所有「写给人看」的正文（不含公式的 expr）。"""
    out = [("定义", p.get("definition", "")), ("物理意义", p.get("meaning", ""))]
    for i, d in enumerate(p.get("derivation") or []):
        out.append(("推导%d" % (i + 1), d))
    ex = p.get("example") or {}
    if ex.get("stem"):
        out.append(("例题题干", ex["stem"]))
    for i, s in enumerate(ex.get("solution") or []):
        out.append(("例题解答%d" % (i + 1), s))
    if ex.get("answer"):
        out.append(("例题答案", ex["answer"]))
    return out


def _defined_here(name, syms):
    """name 算不算「本卡片已定义」。

    三种算数的情况：
      1. 符号表里直接有这个名字；
      2. 带状态编号的形式：Ep_s1 / Ep_s2 —— 只要 Ep_s 有定义即可；
      3. 是两个量相乘写在一起：rv_t —— 只要 r 和 v_t 都有定义即可。
    """
    if name in syms:
        return True
    m = re.match(r"^(.*?)(\d+)$", name)
    if m and m.group(1) in syms:
        return True
    for i in range(1, len(name)):
        a, b = name[:i], name[i:]
        if a in syms and (b in syms or _defined_here(b, syms)):
            return True
    return False


def check_prose_symbols(chapters):
    """正文里用到的符号，本卡片的符号表里有没有。

    返回 (硬问题, 软提示)：硬问题参与通过判定，软提示只列出来给人看。
    """
    hard = []
    soft = []
    for _fname, ch in chapters:
        for p in ch.get("points", []):
            pid = p.get("id", "?")
            syms = set(p.get("symbols") or {})

            # --- 硬：符号名不能拿英文单词充数 ---
            for sname in sorted(syms):
                base = sname.split("_")[0]
                if len(base) >= 3 and base.isalpha() and base.lower() not in _GREEK_NAMES:
                    hard.append(
                        "%s 把符号 `%s` 写成了英文单词 —— 页面上会直接显示这个单词，"
                        "而不是数学符号（波长应记作 `lambda`，显示为 λ）" % (pid, sname))

            # --- 软：正文用到的量，本卡片符号表里有没有 ---
            used = {}
            for where, text in _prose_fields(p):
                if not text:
                    continue
                for m in _RE_PROSE_GREEK.finditer(text):
                    used.setdefault(m.group(1) + (m.group(2) or ""), where)
                for m in _RE_PROSE_SUB.finditer(text):
                    used.setdefault(m.group(1), where)
            for nm in sorted(used):
                if _defined_here(nm, syms):
                    continue
                soft.append("%s 的正文（%s）用到了 `%s`，但本知识点符号表里没有它"
                            % (pid, used[nm], nm))
    return hard, soft


# ============================================================
# 七、报告
# ============================================================

def render_report(fps, fp_rows, cov, cov_stats, rec, rec_n, samples, out, out_info,
                  baseline, quality_issues, prose_hard=None, prose_soft=None, release=None):
    prose_hard = list(prose_hard or [])
    prose_soft = list(prose_soft or [])
    L = []
    L.append("# 质检报告 · 初高中物理知识库")
    L.append("")
    L.append("由 `work/qc.py` 自动生成。**质检的前提是「假设成品有问题」，"
             "所以这里查的是校验器管不到的东西。**")
    L.append("")
    # 版本号 = 源 + 校验器 + 生成器所有文件的组合哈希（与 work/发布版本.py 同一算法）。
    # 发布验收靠它判断「这份报告和那批成品是不是同一版」，所以必须写进来。
    L.append("源/校验器/生成器版本：`%s`" % release_version())
    L.append("")
    L.append("基线封存时间：%s" % baseline.get("frozen_at", "（尚未封存）"))
    L.append("")

    # --- 结论 ---
    all_issues = fps + cov + rec + out + prose_hard + (release.get("issues", []) if release else ["未执行完整正式验收"])
    hard = [i for i in all_issues]          # 一到五项的问题都算硬问题
    soft = quality_issues
    L.append("## 一、结论")
    L.append("")
    L.append("| 环节 | 结果 |")
    L.append("|---|---|")
    L.append("| 指纹（校验器是否被改动） | %s |" % ("✔ 干净" if not fps else "✘ %d 处" % len(fps)))
    L.append("| 覆盖门槛（内容被考得够不够深） | %s |" % ("✔ 达标" if not cov else "✘ %d 处未达标" % len(cov)))
    L.append("| 独立复算（参考值有没有被凑） | ✔ 复算 %d 项，%s |"
             % (rec_n, "全部相符" if not rec else "**%d 处不符**" % len(rec)))
    L.append("| 成品一致性 | %s |" % ("✔ 通过" if not out else "✘ %d 处" % len(out)))
    L.append("| 质量扫描（占位符 / 空字段） | %s |" % ("✔ 通过" if not quality_issues else "⚠ %d 处（软性，不拦）" % len(quality_issues)))
    if prose_hard:
        L.append("| 内容自洽性（正文符号 vs 符号表） | ✘ %d 处硬问题 |" % len(prose_hard))
    elif prose_soft:
        L.append("| 内容自洽性（正文符号 vs 符号表） | ⚠ %d 处提示（软性，不拦） |" % len(prose_soft))
    else:
        L.append("| 内容自洽性（正文符号 vs 符号表） | ✔ 通过 |")
    L.append("")
    if hard:
        L.append("**自动环节未通过。** 各类问题的明细见下面第二～五节，"
                 "汇总如下：")
        L.append("")
        if fps:
            L.append("- 校验器本体有 **%d** 处改动（第二节）→ 必须补 `work/校验器变更.md`" % len(fps))
        if cov:
            L.append("- 覆盖门槛有 **%d** 处未达标（第三节）→ 执行方的补课清单" % len(cov))
        if rec:
            L.append("- 独立复算有 **%d** 处不符（第四节）→ **参考值可能是凑出来的，最严重**" % len(rec))
        if out:
            L.append("- 成品一致性有 **%d** 处问题（第五节）" % len(out))
        if prose_hard:
            L.append("- 内容自洽性有 **%d** 处硬问题（第七节）→ "
                     "符号名拿英文单词充数，页面会显示成英文单词" % len(prose_hard))
        L.append("")
        L.append("> 其中「独立复算不符」级别最高：它意味着内容本身可能就是错的，"
                 "而不是形式不达标。")
    else:
        L.append("**自动环节全部通过。** 但这不等于内容正确 —— "
                 "请继续看第七节的抽样单，那部分只能靠人。")
    L.append("")

    # --- 一、指纹 ---
    L.append(render_release_section(release))
    L.append("## 二、指纹：校验器本体有没有被动过")
    L.append("")
    L.append("校验器源码属于「冻结」范围。改了就必须在 `work/校验器变更.md` 里说明理由，"
             "否则视为违规 —— 因为放松检查比写错内容更危险。")
    L.append("")
    L.append("| 文件 | 状态 | 基线 | 当前 |")
    L.append("|---|---|---|---|")
    for rel, state, a, b in fp_rows:
        L.append("| `%s` | %s | `%s` | `%s` |" % (rel, state, a, b))
    L.append("")

    # --- 二、覆盖门槛 ---
    L.append("## 三、覆盖门槛：内容有没有被真正考过")
    L.append("")
    L.append("「校验全部通过」本身不说明问题 —— 少写几条检查项也很容易通过。")
    L.append("所以给「检查的深度」设了下限：")
    L.append("")
    L.append("- 每个知识点的公式里，`数值代入` 至少 2 项、`极端参数扫描` 至少 1 项、"
             "`一致性核对` 至少 1 项")
    L.append("- 每个知识点至少要有一条**「量纲抓不到、只能靠数值代入」**的陷阱")
    L.append("- 每个知识点至少 3 条推导要点、2 条常见错误、1 组情境参数")
    L.append("- 定义 / 物理意义 / 例题都有最短长度要求")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    L.append("| 知识点 | %d |" % cov_stats["points"])
    L.append("| 公式 | %d |" % cov_stats["formulas"])
    L.append("| 声明的检查项 | %d |" % cov_stats["declared_checks"])
    L.append("| 常见错误 | %d（数值代入类 %d / 人工审核 %d）"
             % (cov_stats["traps"], cov_stats["traps_numeric"], cov_stats["traps_manual"]))
    L.append("| 没有任何数值代入检查的公式 | %d |" % cov_stats["formulas_without_numeric"])
    L.append("")
    if cov:
        L.append("**未达标的条目：**")
        L.append("")
        for i in cov:
            L.append("- %s" % i)
    else:
        L.append("✔ 全部达标。")
    L.append("")

    # --- 三、独立复算 ---
    L.append("## 四、独立复算：参考值有没有被凑出来")
    L.append("")
    L.append("这一步换了一套**完全不同的代码**（Python 自带的表达式求值），"
             "把每个数值检查重新算一遍，跟源文件里写的参考值比。")
    L.append("")
    L.append("为什么必须这么做：如果有人把公式写错了，再把「参考值」也改成错误公式算出来的数，"
             "那么校验器会认为一切正常 —— 因为校验器比的就是这两者。"
             "独立求值可发现参考数值或求值实现不符；源表达式与参考值共同写错仍需独立物理证据或教师审核。")
    L.append("")
    L.append("共独立复算 **%d** 项，%s。" % (rec_n, "全部相符" if not rec else "发现 %d 处不符" % len(rec)))
    L.append("")
    for i in rec:
        L.append("- %s" % i)
    L.append("")

    # --- 四、成品一致性 ---
    L.append("## 五、成品一致性")
    L.append("")
    L.append("| 项目 | 值 |")
    L.append("|---|---|")
    for k, v in out_info.items():
        L.append("| %s | %s |" % (k, v))
    L.append("")
    if out:
        for i in out:
            L.append("- %s" % i)
    else:
        L.append("✔ 成品与源文件一致，无外部依赖。")
    L.append("")

    # --- 六、质量扫描 ---
    L.append("## 六、质量扫描")
    L.append("")
    if soft:
        for i in soft:
            L.append("- %s" % i)
    else:
        L.append("✔ 未发现占位符与空字段。")
    L.append("")

    # --- 七、内容自洽性 ---
    L.append("## 七、内容自洽性：正文里的符号对不对得上")
    L.append("")
    L.append("上面六节查的大多是「公式」和「结构」。这一节查的是**正文散文** —— "
             "定义、物理意义、推导、例题里随手写下的那些式子。")
    L.append("")
    L.append("为什么要单独查：**正文里的公式不参与任何校验。** 校验器只比每条公式的 "
             "`expr` 字段；正文写什么、用了哪些符号，它一概不看。")
    L.append("于是曾经出现过这种情况：某知识点的定义正文写 `v = lambda f`，"
             "而同一张卡片的符号表却把波长定义成 `wavelength` —— "
             "同一个物理量在一张卡片里有两个名字，页面上会同时出现 λ 和 "
             "wavelength。六道检查一道都没抓到。这一节就是补这个洞。")
    L.append("")
    if prose_hard:
        L.append("**硬问题（%d 处，参与通过判定）：**" % len(prose_hard))
        L.append("")
        for i in prose_hard:
            L.append("- %s" % i)
        L.append("")
    if prose_soft:
        L.append("**提示（%d 处，软性，不拦）：**" % len(prose_soft))
        L.append("")
        L.append("多数是推导时顺手引用了邻近知识点的量（例如斜面例题里的倾角 θ、"
                 "单摆推导里的角频率 ω）。不影响正确性，但补进符号表会更完整。")
        L.append("")
        for i in prose_soft:
            L.append("- %s" % i)
        L.append("")
    if not prose_hard and not prose_soft:
        L.append("✔ 正文里用到的符号，本知识点的符号表里都能找到。")
        L.append("")

    # --- 八、抽样单 ---
    L.append("## 八、人工抽样单（只有这部分机器替不了）")
    L.append("")
    L.append("随机抽出的公式如下。**机器能查规则，查不了物理正确性** —— "
             "尤其是「两条公式错得一模一样、还能互相印证」这种情况，"
             "任何自动检查都发现不了。这一节必须由人看。")
    L.append("")
    for i, s in enumerate(samples, 1):
        L.append("### 抽样 %d · %s / %s" % (i, s["pid"], s["formula"]))
        L.append("")
        L.append("- 章节：%s" % s["chapter"])
        L.append("- 知识点：%s" % s["point"])
        L.append("- 公式原文：`%s`" % s["expr"])
        if s["when"]:
            L.append("- 适用条件：%s" % s["when"])
        L.append("- 符号与单位：%s"
                 % "、".join("%s(%s)" % (k, v or "无量纲") for k, v in s["symbols"].items()))
        L.append("- 情境参数：%s" % json.dumps(s["scenarios"], ensure_ascii=False))
        if s["another_expr_in_point"]:
            L.append("- 同知识点的另一条公式：%s" % s["another_expr_in_point"])
        L.append("")
        L.append("**请人工确认**：这条公式在物理上是否正确？适用条件写得是否准确？")
        L.append("")

    return "\n".join(L)


# ============================================================
# 主流程
# ============================================================

def release_version():
    # 与发布门禁共用版本登记，包括被门禁调用的契约检查脚本。
    from 发布版本 import versions
    return versions(repo_root())['version']





# -*- coding: utf-8 -*-
"""供质检方审查的完整正式检查与版本记录；与独立复算分别计数。"""
def release_versions(root=None):
    root=root or repo_root();records={}
    for directory in ['kb','kb_junior','quiz_bank/hs','quiz_bank/junior']:
        path=os.path.join(root,'work',directory)
        if not os.path.isdir(path):continue
        for name in sorted(os.listdir(path)):
            if name.endswith('.json') and not name.startswith('_'):
                relative='work/'+directory+'/'+name;records[relative]=sha256_of(os.path.join(path,name))
    for name in FROZEN_PATTERNS+['work/validate.py','work/physkit/__init__.py','work/build_all.py','work/build_site.py','work/build_lite.py','work/build_quick_site.py','work/build_quiz.py','work/诊断题重构.py','work/prose_math.py']:
        path=os.path.join(root,name)
        if os.path.isfile(path):records[name]=sha256_of(path)
    # 发布版本以真实输入摘要确定，检查时间另外记录，不把时间当内容版本。
    from 发布版本 import versions
    canonical=versions(root);records.update(canonical['fingerprints']);version=canonical['version']
    baseline_path=os.path.join(root,'质检基线.json')
    if os.path.isfile(baseline_path):records['质检基线.json']=sha256_of(baseline_path)
    return {'version':version,'fingerprints':records}

def check_release_all(root=None):
    """双学段完整结构、完整物理及独立求值分别执行。"""
    from physkit import kb as KB
    from datetime import datetime,timezone,timedelta
    root=root or repo_root();before=release_versions(root);data={'started_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),'before':before,'segments':{}};issues=[]
    import sys
    modules={name:sys.modules[name].__file__ for name in ['physkit.kb','physkit.checks','physkit.expr']}
    modules['质检']=__file__;data['loaded_engine_fingerprints']={path:sha256_of(path) for path in modules.values()}
    # ★ 2026-10-05 质检方修：判断「跑的是不是正式路径」必须先把路径统一写法再比。
    #   原来直接拿 dirname 和 root 拼出来的字符串比，只要调用时 root 写成 D:/codex（正斜杠）
    #   而模块路径是 D:\codex\work\physkit（反斜杠），比就永远不成立，
    #   于是明明跑的是正式引擎，报告里却写成「隔离候选」——凭空多一场虚惊。
    formal_engine=os.path.normcase(os.path.realpath(os.path.join(root,'work','physkit')))
    formal_qc=os.path.normcase(os.path.realpath(os.path.join(root,'work','qc.py')))
    loaded_paths=[os.path.normcase(os.path.realpath(p)) for n,p in modules.items() if n!='质检']
    data['scope']='正式路径' if all(os.path.dirname(p)==formal_engine for p in loaded_paths) and os.path.normcase(os.path.realpath(__file__))==formal_qc else '隔离候选，仅供审查，不是正式发布'
    for label,directory in [('高中','kb'),('初中','kb_junior')]:
        chapters=KB.load_kb(os.path.join(root,'work',directory));structure,_=KB.check_structure(chapters);physics=KB.run_physics_checks(chapters);stats=KB.summarize(physics)
        hard_structure=[str(x) for x in structure if x.level=='错误'];rec,n,_=check_recompute(chapters,0,20261005)
        failed=[{'point':pid,**r} for pid,p in physics.items() for r in p['results'] if not r['ok']]
        data['segments'][label]={'structure_errors':hard_structure,'summary':stats,'failed_count':stats['checks']-stats['pass'],'failed_checks':failed,'independent_count':n,'independent_issues':rec}
        issues.extend(label+'结构：'+s for s in hard_structure);issues.extend(label+'正式物理失败：'+r['point']+'/'+r['formula'] for r in failed);issues.extend(label+'独立复算：'+s for s in rec)
        issues.extend(label+'错误防线未抓住：'+s for s in stats['trap_failures'])
    data['after']=release_versions(root);data['version_stable']=data['before']==data['after'];data['finished_at']=datetime.now(timezone(timedelta(hours=8))).isoformat()
    if not data['version_stable']:issues.append('验收期间输入或校验器改变，必须重新验收')
    data['issues']=issues;return issues,data

def complete_release_review(release,root=None):
    """质检结束时复查版本、三处七页面及维护别名；旧成品不能冒充新版。"""
    from datetime import datetime,timezone,timedelta
    from pathlib import Path
    root=Path(root or repo_root());release['after']=release_versions(str(root))
    release['version_stable']=release['before']==release['after']
    if not release['version_stable']:release['issues'].append('整个质检期间输入或校验器改变，必须重新验收')
    names=['index.html','junior.html','student.html','quick.html','junior-quick.html','quiz-hs.html','quiz-junior.html']
    aliases={'高中物理知识库.html':'index.html','初中物理知识库.html':'junior.html','高中物理速查.html':'quick.html','高中物理速查_优化版.html':'quick.html','高中物理知识库-学生版.html':'student.html','初中物理速查.html':'junior-quick.html'}
    records=[]
    for name in names:
        hashes=[]
        for directory in ['outputs','site','outputs/物理知识库整合版']:
            p=root/directory/name
            if not p.exists():release['issues'].append('缺少成品：'+str(p));continue
            content=p.read_text(encoding='utf-8');h=sha256_of(str(p));hashes.append(h)
            match=re.search(r'<meta name="物理知识库版本" content="([a-f0-9]+)">',content)
            pv=match.group(1) if match else None;records.append({'path':str(p.relative_to(root)),'sha256':h,'version':pv})
            if pv!=release['after']['version']:release['issues'].append('成品版本不同或仍为历史成品：'+str(p))
            if p.stat().st_size>8*1024*1024:release['issues'].append('成品超过8 MiB：'+str(p))
        if len(hashes)!=3 or len(set(hashes))!=1:release['issues'].append('三处交付不同版：'+name)
    for directory in ['outputs','site','outputs/物理知识库整合版']:
        for alias,target in aliases.items():
            p=root/directory/alias;t=root/directory/target
            required=directory=='outputs' or target in ['index.html','junior.html']
            if not p.exists():
                if required:release['issues'].append('缺少维护别名：'+str(p))
            elif not t.exists() or sha256_of(str(p))!=sha256_of(str(t)):release['issues'].append('维护别名不同版：'+str(p))
    last=release_versions(str(root))
    if last!=release['after']:
        release['issues'].append('成品检查过程中输入或校验器变化，必须重验');release['version_stable']=False;release['after']=last
    release['products']=records;release['finished_at']=datetime.now(timezone(timedelta(hours=8))).isoformat()
    return release['issues']

def render_release_section(release):
    if not release:return '\n## 完整正式验收\n\n未执行完整正式验收，不能发布。\n'
    lines=['## 完整正式验收与版本','', '源/校验器/生成器版本：`'+release['after']['version']+'`。', '检查开始：'+release['started_at']+'；结束：'+release['finished_at']+'。','', '| 学段 | 结构错误 | 正式通过 | 正式失败 | 独立复算 |','|---|---:|---:|---:|---:|']
    for label,row in release['segments'].items():
        s=row['summary'];lines.append('| %s | %d | %d/%d | %d | %d项，%d处不符 |'%(label,len(row['structure_errors']),s['pass'],s['checks'],row['failed_count'],row['independent_count'],len(row['independent_issues'])))
    lines += ['', '运行范围：'+release.get('scope','未登记')+'。']
    lines += ['', '验收版本稳定：'+('是' if release['version_stable'] else '否，必须重验')+'。完整正式检查与独立复算分别执行，不能互相替代。', '教学质量仍待教师签署；实验装置未实测的不宣称已验证。','', '### 本次正式失败或版本问题','']
    lines += ['- '+s for s in release['issues']] or ['无。']

    # ---------- ★ 2026-10-05：报告必须绑定「实际输入 + 实际加载的校验器 + 实际成品」 ----------
    # 以前门禁只用一句"源/校验器/生成器版本：`xxx`"的文字匹配来判断"这份报告是不是
    # 对这批成品写的"。文字是可以复制的，所以现在改成**算指纹**：输入、成品各压一个
    # 汇总指纹，再合成一个验收总指纹；门禁按这个总指纹核对，文字对不上都不算数。
    lines += ['', '### 本次验收绑定（报告 ↔ 实际受检输入与成品）','']
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    from 发布版本 import acceptance_binding, input_fingerprints, product_fingerprints
    bind = acceptance_binding()
    lines += ['- 实际输入：**%d** 个文件，指纹汇总 `%s`' % (bind['inputs_count'], bind['inputs_digest'])]
    lines += ['- 最终成品：三处交付 × 7 个页面，指纹汇总 `%s`' % bind['products_digest']]
    lines += ['- **验收绑定总指纹：`%s`**' % bind['binding']]
    lines += ['- 机器识别标记：`ACCEPTANCE-BINDING %s`' % bind['binding']]
    lines += ['', '> 题库（含 `work/quiz_bank/*.json` 派生快照）、校验器或成品任何一处变化，'
                  '本指纹即失效，这份报告随之作废，必须重新质检。']
    lines += ['', '#### 实际加载的正式校验器指纹','']
    for path, h in sorted((release.get('loaded_engine_fingerprints') or {}).items()):
        lines += ['- `%s`：`%s`' % (path, h)]
    lines += ['', '#### 冻结基线指纹','']
    lines += ['- `%s`：`%s`'%(n,h) for n,h in release['after']['fingerprints'].items() if n.startswith('work/physkit/') or n in ['work/qc.py','work/validate.py']]
    lines += ['', '#### 最终成品逐页指纹（三处交付）','']
    products = release.get('products') or []
    for name in ['index.html','junior.html','student.html','quick.html','junior-quick.html','quiz-hs.html','quiz-junior.html']:
        rows = [r for r in products if str(r['path']).endswith(name)]
        if not rows:
            lines += ['- `%s`：未登记' % name];continue
        for r in rows:
            lines += ['- `%s`：`%s`（%s，版本 `%s`）' % (r['path'], r['sha256'], '同版' if r.get('version') == release['after']['version'] else '**版本不符**', r.get('version'))]
    return '\n'.join(lines)+'\n'


def write_text_retry(path, value):
    """写质检报告/基线，带一个有界重试。

    ★ 2026-10-05：实测 Windows 会在写入时偶发抛 `OSError: [Errno 22] Invalid argument`
      （杀毒或索引器短暂占住文件）。它是资源问题，不是内容问题，但会让质检直接崩，
      留下**上一次的旧报告** —— 而旧报告看起来是完整的，很容易被误当成本轮结果。
      所以这里重试 3 次；仍然失败才抛出，绝不"静默保留旧报告"。
    """
    import time
    last = None
    for attempt in range(3):
        try:
            with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(value)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise last


def main(argv):
    freeze = "--freeze" in argv
    sample_n = 5
    seed = 20260918
    for i, a in enumerate(argv):
        if a == "--sample" and i + 1 < len(argv):
            sample_n = int(argv[i + 1])
        if a == "--seed" and i + 1 < len(argv):
            seed = int(argv[i + 1])

    print("=" * 62)
    print("质检 · 高中物理知识库")
    print("=" * 62)

    # ---------- 封存基线 ----------
    if freeze:
        fp = fingerprint()
        data = {
            "frozen_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note": "这是质检基线。冻结清单里的文件若发生改动，质检会报出来。",
            "fingerprint": fp,
        }
        with io.open(BASELINE, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        print("已封存基线：%s" % BASELINE)
        for k, v in fp.items():
            print("  %-28s %s" % (k, v[:16]))
        return 0

    # ---------- 质检 ----------
    if not os.path.exists(BASELINE):
        print("还没有基线。请先运行：python qc.py --freeze")
        return 1
    with io.open(BASELINE, encoding="utf-8") as fh:
        baseline = json.load(fh)

    release_issues, release = check_release_all()
    chapters = load_raw_kb()

    fps, fp_rows = check_fingerprint(baseline)
    cov, cov_stats = check_coverage(chapters)
    rec, rec_n, samples = check_recompute(chapters, sample_n, seed)
    out, out_info = check_output(chapters)
    soft = check_quality(chapters)
    prose_hard, prose_soft = check_prose_symbols(chapters)

    release_issues = complete_release_review(release)
    report = render_report(fps, fp_rows, cov, cov_stats, rec, rec_n, samples,
                           out, out_info, baseline, soft, prose_hard, prose_soft, release)
    path = os.path.join(OUT_DIR, "质检报告.md")
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    # 带重试写盘；失败就抛出，绝不"静默保留旧报告"（旧报告看着完整，极易被误当本轮结果）。
    write_text_retry(path, report)

    hard = len(fps) + len(cov) + len(rec) + len(out) + len(prose_hard) + len(release_issues)
    print("完整正式验收问题 %d" % len(release_issues))
    print("指纹问题      %d" % len(fps))
    print("覆盖门槛问题  %d" % len(cov))
    print("独立复算不符  %d（共复算 %d 项）" % (len(rec), rec_n))
    print("成品一致性问题 %d" % len(out))
    print("质量扫描问题  %d（软性，不拦）" % len(soft))
    print("内容自洽性    %d 硬 / %d 软" % (len(prose_hard), len(prose_soft)))
    print()
    print("已生成：%s" % path)
    print("结论：%s" % ("✔ 自动环节通过，待人工看抽样单" if hard == 0 else "✘ 未通过"))
    return 0 if hard == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
