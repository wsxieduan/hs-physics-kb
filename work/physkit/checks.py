# -*- coding: utf-8 -*-
"""
checks.py —— 六类自动校验
========================

【校验清单】

代码能自动判定的物理错误，按"能不能自动抓"分：

    #  检查项            抓什么错                         本文件是否实现
    ─────────────────────────────────────────────────────────────
    1  量纲一致性        公式抄错（写反、漏项、多一个系数）      ✅ 实现了
    2  单位标注          变量标的单位和公式推出来的不一致      ✅ 实现了
    3  数值代入          算出来的数不对                        ✅ 实现了
    4  变化方向          单调性反了（"摆长越长周期越短"）      ✅ 实现了
    5  跨公式一致        同一情境下两条公式给出不同答案        ✅ 实现了
    6  极端参数扫描      参数取到边界时崩溃 / 出现 NaN         ✅ 实现了

【哪几类最值钱】

第 1 类和第 4 类是最值钱的 —— 因为这两类错误"看起来最像对的"，
人工一条条核不现实，而它们恰好又能被完全自动地判死。

第 5 类（跨公式一致）是这里最漂亮的一条：
    同一个物理情境，用不同的公式各算一遍，答案必须一样。
    例如匀变速直线运动里：
        v  = v₀ + at            （速度公式）
        v² = v₀² + 2ax         （速度位移关系）
    只要给一组自洽的参数，两条公式算出的 v 必须完全相等。
    **这个"相等"不是我手工算出来的，是两条公式互相印证出来的** ——
    所以它能抓到"两条公式里有一条写错了"这种很难靠肉眼看出的错误。

作者备注：本文件只依赖同目录的 quantity.py 和 expr.py，无第三方库。
"""

import math

from . import expr as EX
from .quantity import Dim, Quantity, parse_unit, UnitError, DimError, EPS

# 各检查类型的显示名，报告里用
CHECK_NAMES = {
    "dimension": "量纲一致性",
    "unit": "单位标注",
    "numeric": "数值代入",
    "direction": "变化方向",
    "consistency": "跨公式一致",
    "scan": "极端参数扫描",
    "compare": "不等式关系",
}


class CheckResult:
    """一条检查的结果。"""

    def __init__(self, check_type, formula, ok, detail, note="", error=None):
        self.type = check_type
        self.formula = formula
        self.ok = ok
        self.detail = detail
        self.note = note
        self.error = error          # 语法/量纲等异常时的原文

    @property
    def type_name(self):
        return CHECK_NAMES.get(self.type, self.type)

    def as_dict(self):
        return {
            "type": self.type,
            "type_name": self.type_name,
            "formula": self.formula,
            "ok": self.ok,
            "detail": self.detail,
            "note": self.note,
        }


# ============================================================
# 一、准备环境：把「变量声明」变成可计算的环境
# ============================================================

# 反向表：希腊符号 → 英文名。让 "θ" 和 "theta" 能互相认识。
GREEK_REVERSE = {v: k for k, v in EX.GREEK.items()}


def _alias_env(env):
    """
    给环境加别名：如果声明里用的是 θ，公式里写 theta 也能找到；
    反之亦然。避免因为"写法不同"而误报变量未定义。
    """
    out = dict(env)
    for name, q in env.items():
        if name in EX.GREEK:                 # theta → θ
            out.setdefault(EX.GREEK[name], q)
        if name in GREEK_REVERSE:            # θ → theta
            out.setdefault(GREEK_REVERSE[name], q)
    return out


def build_env(symbols, values=None):
    """
    把符号表 + 一组具体数值，组装成求值环境。

    参数：
        symbols —— {变量名: {"desc": 说明, "unit": 单位字符串}}
        values  —— {变量名: 数值}，可以不完整；没给的变量数值就是 None

    返回 {变量名: Quantity}
    """
    values = values or {}
    env = {}
    for name, info in symbols.items():
        unit = info.get("unit", "")
        try:
            dim = parse_unit(unit)
        except UnitError as exc:
            raise ValueError("变量 %s 的单位 %r 有问题：%s" % (name, unit, exc))
        raw = values.get(name, None)
        val = None if raw is None else float(raw)
        env[name] = Quantity(val, dim)
    return _alias_env(env)


def _missing_for(ast, symbols, values):
    """看看要算出数值结果，还缺哪些变量的值。"""
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


# ============================================================
# 二、检查 1：量纲一致性
# ============================================================
# 做法：左边变量声明的单位 → 一个量纲；
#       右边用各变量声明的单位算一遍 → 另一个量纲；
#       两个必须相同。

def check_dimension(fname, ast_lhs, ast_rhs, lhs_var, symbols):
    try:
        env = build_env(symbols)                    # 只要量纲，数值全部为空
        left = EX.evaluate(ast_lhs, env)
        right = EX.evaluate(ast_rhs, env)
    except (EX.ExprError, UnitError, DimError, ValueError) as exc:
        return CheckResult(
            "dimension", fname, False,
            "算不出量纲：%s" % exc, error=str(exc))

    if left.dim == right.dim:
        return CheckResult(
            "dimension", fname, True,
            "左边 %s —— 右边 %s，一致" % (left.dim, right.dim))

    return CheckResult(
        "dimension", fname, False,
        "左边是 %s，右边算出 %s（差在：%s）"
        % (left.dim, right.dim, right.dim.diff(left.dim)))


# ============================================================
# 三、检查 2：单位标注
# ============================================================
# 做法：左边变量在符号表里声明了单位（比如 v 是 m/s），
#       等号左边这个表达式的量纲必须正好是这个单位的整数/半整数次幂。
#       绝大多数情况就是 1 次幂（v = ...），但 v^2 = ... 这种要容许 2 次幂，
#       否则会把正确的公式误判成错的。
# 目的：抓"单位标错"，例如把 v 的单位写成 m/s^2 —— 这种错平时最难发现，
#       因为公式本身可能是对的。

# 容许的幂次（覆盖高中物理会出现的写法）
UNIT_EXPONENTS = (1, -1, 2, -2, 3, -3, 4, -4, 0.5, -0.5,
                  1.0 / 3, -1.0 / 3, 2.0 / 3, -2.0 / 3)


def check_unit(fname, ast_lhs, ast_rhs, lhs_var, symbols):
    info = symbols.get(lhs_var)
    if info is None:
        return CheckResult("unit", fname, True,
                           "左边变量 %s 没在符号表里声明单位，跳过" % lhs_var)
    unit = info.get("unit", "")
    try:
        declared = parse_unit(unit)
    except UnitError as exc:
        return CheckResult("unit", fname, False,
                           "左边变量 %s 的单位 %r 无法解析：%s" % (lhs_var, unit, exc))

    try:
        env = build_env(symbols)
        left_dim = EX.evaluate(ast_lhs, env).dim
        right_dim = EX.evaluate(ast_rhs, env).dim
    except Exception as exc:
        return CheckResult("unit", fname, False, "推导量纲时出错：%s" % exc)

    if left_dim != right_dim:
        # 这种不一致已经由"量纲一致性"报出来了，这里不重复报警
        return CheckResult("unit", fname, True,
                           "等号两边量纲本就不一致，单位检查交给「量纲一致性」处理")

    for k in UNIT_EXPONENTS:
        if declared ** k == left_dim:
            extra = "" if k == 1 else "（等号左边取了 %g 次幂）" % k
            return CheckResult(
                "unit", fname, True,
                "%s 标注单位 %r（%s）与公式推得的 %s 相符%s"
                % (lhs_var, unit, declared, left_dim, extra))

    return CheckResult(
        "unit", fname, False,
        "%s 标注的单位是 %r（%s），但公式推出来是 %s —— "
        "公式对不对另说，这个单位标注肯定是错的"
        % (lhs_var, unit, declared, left_dim))


# ============================================================
# 四、检查 3：数值代入
# ============================================================
# 做法：按给定的情境参数算一遍，和"独立算出来的参考值"比。
# 参考值必须来自另一条路（手工算 / 另一条公式），不能是这条公式自己算的。
#
# 可以只指定"要算哪个表达式"（spec["expr"]），不指定就默认算等号右边。
# 例如 v² = 2gh 这条，我们想验的是 v 等于多少，就写 "expr": "v"。

def _spec_ast(spec, default_ast, key="expr"):
    """取出检查项里指定的表达式树；没写就用默认的。"""
    text = spec.get(key)
    if not text:
        return default_ast
    return EX.parse(text)


def check_numeric(fname, ast_lhs, ast_rhs, lhs_var, symbols, spec):
    scenario_name = spec.get("scenario")
    values = spec["_scenario_values"]
    target = spec.get("target", lhs_var)
    ref = spec["ref"]
    tol = spec.get("tol", 1e-6)

    try:
        ast = _spec_ast(spec, ast_rhs)
    except EX.ExprError as exc:
        return CheckResult("numeric", fname, False, "检查项里的表达式写不通：%s" % exc)

    miss = _missing_for(ast, symbols, values)
    if miss:
        return CheckResult("numeric", fname, False,
                           "情境「%s」缺少这些变量的值：%s"
                           % (scenario_name, "、".join(miss)))

    try:
        env = build_env(symbols, values)
        got = EX.evaluate(ast, env).value
    except Exception as exc:
        return CheckResult("numeric", fname, False, "代入算账时出错：%s" % exc)

    if got is None:
        return CheckResult("numeric", fname, False, "算出来没有数值")

    if abs(got - ref) <= tol * max(1.0, abs(ref)):
        return CheckResult(
            "numeric", fname, True,
            "情境「%s」：算得 %s = %.6g，独立参考值 %.6g，相符"
            % (scenario_name, target, got, ref),
            note=spec.get("note", ""))

    return CheckResult(
        "numeric", fname, False,
        "情境「%s」：算得 %s = %.6g，但独立参考值是 %.6g（相差 %.3g）"
        % (scenario_name, target, got, ref, got - ref),
        note=spec.get("note", ""))


# ============================================================
# 五、检查 4：变化方向（单调性）
# ============================================================
# 做法：把某个参数从小变到大，看结果该变大还是变小。
# 只要给出一句"参数变大时结果往哪边变"的物理规律，就能自动判。

def check_direction(fname, ast_rhs, symbols, spec, lhs_var=None):
    scenario_name = spec.get("scenario")
    var = spec["var"]
    lo, hi = spec["lo"], spec["hi"]
    expect = spec["expect"]                    # "up" / "down" / "same"
    target = spec.get("target") or lhs_var
    base = dict(spec["_scenario_values"])

    try:
        ast = _spec_ast(spec, ast_rhs)
    except EX.ExprError as exc:
        return CheckResult("direction", fname, False, "检查项里的表达式写不通：%s" % exc)

    try:
        base_q = build_env(symbols, base)
    except Exception as exc:
        return CheckResult("direction", fname, False, "组装环境时出错：%s" % exc)

    # 被改变的那个变量取它的量纲（没声明过就当作无量纲）
    var_dim = base_q[var].dim if var in base_q else Dim()

    def value_at(x):
        env = dict(base_q)
        env[var] = Quantity(float(x), var_dim)
        return EX.evaluate(ast, env).value

    try:
        v_lo, v_hi = value_at(lo), value_at(hi)
    except Exception as exc:
        return CheckResult("direction", fname, False, "取参数值算账时出错：%s" % exc)

    if v_lo is None or v_hi is None:
        # 结果算不出数值，通常不是"被改变的那个变量"没给值，
        # 而是情境里其他变量缺了值 —— 这里把真正缺的变量报出来，别误导。
        miss = _missing_for(ast, symbols, spec["_scenario_values"])
        if miss:
            return CheckResult("direction", fname, False,
                               "情境里缺少这些变量的值：%s" % "、".join(miss))
        return CheckResult("direction", fname, False,
                           "结果算不出数值，无法判断方向")

    scale = max(abs(v_lo), abs(v_hi), 1e-12)
    if abs(v_hi - v_lo) < 1e-9 * scale:
        actual = "same"
    elif v_hi > v_lo:
        actual = "up"
    else:
        actual = "down"

    word = {"up": "变大", "down": "变小", "same": "不变"}

    if actual == expect:
        return CheckResult(
            "direction", fname, True,
            "%s 从 %g 增到 %g，%s 由 %.6g 变为 %.6g —— 方向正确（应%s）"
            % (var, lo, hi, target or "结果", v_lo, v_hi, word[expect]),
            note=spec.get("note", ""))

    return CheckResult(
        "direction", fname, False,
        "%s 从 %g 增到 %g，%s 由 %.6g 变为 %.6g —— 方向反了：应当%s，实际%s"
        % (var, lo, hi, target or "结果", v_lo, v_hi, word[expect], word[actual]),
        note=spec.get("note", ""))


# ============================================================
# 六、检查 5：跨公式一致（★ 最有价值的一条）
# ============================================================
# 做法：同一个物理情境下，两条不同的公式各算一遍"同一个量"，答案必须相同。
# 这个"相同"是两条公式互相印证出来的，不需要人工提供参考值 ——
# 所以它能抓到"两条公式里有一条写错了"这种最难靠肉眼发现的错误。
#
# 可以在检查项里用 expr / with_expr 指定"要比较哪个表达式"。
# 不写的话，默认比较两条公式各自的等号右边。
# 例：把 v² = 2gh 和 v = gt 放在一起时，写
#       "expr": "v^2",   "with_expr": "v^2"
#     就能让两条公式都算一遍 v² 然后比对。

def check_consistency(fname, ast_rhs, symbols, spec, other_formulas):
    other_name = spec.get("with")
    if other_name not in other_formulas:
        return CheckResult("consistency", fname, False,
                           "找不到要对照的公式「%s」" % other_name)

    other = other_formulas[other_name]
    values = dict(spec["_scenario_values"])
    tol = spec.get("tol", 1e-6)
    target = spec.get("target") or other.get("lhs_var") or "结果"

    try:
        ast_a = _spec_ast(spec, ast_rhs, "expr")
        ast_b = _spec_ast(spec, other["rhs"], "with_expr")
    except EX.ExprError as exc:
        return CheckResult("consistency", fname, False,
                           "检查项里的表达式写不通：%s" % exc)

    miss = sorted(set(_missing_for(ast_a, symbols, values))
                  | set(_missing_for(ast_b, symbols, values)))
    if miss:
        return CheckResult("consistency", fname, False,
                           "两条公式一起算还缺这些变量的值：%s" % "、".join(miss))

    try:
        env = build_env(symbols, values)
        a = EX.evaluate(ast_a, env).value
        b = EX.evaluate(ast_b, env).value
    except Exception as exc:
        return CheckResult("consistency", fname, False, "对照计算时出错：%s" % exc)

    if a is None or b is None:
        return CheckResult("consistency", fname, False, "对照时有一边没有数值")

    # 对照式的称呼：写了 with_expr 就说明对照的是"某个具体表达式"，
    # 没写就说明对照的是另一条公式的等号右边 —— 两种情况说法要分开，别让人误解。
    if spec.get("with_expr"):
        who = "对照式 %s" % EX.to_plain(ast_b)
    else:
        who = "「%s」" % other_name

    if abs(a - b) <= tol * max(1.0, abs(a)):
        return CheckResult(
            "consistency", fname, True,
            "同一情境下「%s」：本式算得 %.6g，%s 算得 %.6g，完全吻合"
            % (target, a, who, b),
            note=spec.get("note", ""))

    return CheckResult(
        "consistency", fname, False,
        "同一情境下「%s」：本式算得 %.6g，但 %s 算得 %.6g（相差 %.3g）"
        "—— 两条路子必有一条是错的" % (target, a, who, b, a - b),
        note=spec.get("note", ""))


# ============================================================
# 七、检查 6：极端参数扫描
# ============================================================
# 做法：让参数在一个区间里连续取很多值，看会不会崩、会不会算出 NaN / 无穷。
# 这是最省事的一条 —— 什么都不用提供，只要有公式就行。

def check_scan(fname, ast_rhs, symbols, spec):
    var = spec["var"]
    lo, hi = spec["lo"], spec["hi"]
    n = spec.get("n", 60)
    base = dict(spec["_scenario_values"])

    try:
        base_q = build_env(symbols, base)
    except Exception as exc:
        return CheckResult("scan", fname, False, "组装环境时出错：%s" % exc)

    bad = []
    for k in range(n + 1):
        x = lo + (hi - lo) * k / n
        env = dict(base_q)
        env[var] = Quantity(float(x), base_q[var].dim if var in base_q else Dim())
        try:
            v = EX.evaluate(ast_rhs, env).value
        except Exception as exc:
            bad.append("%s=%g 时出错（%s）" % (var, x, exc))
            continue
        if v is None:
            continue
        if math.isnan(v) or math.isinf(v):
            bad.append("%s=%g 时算出 %s" % (var, x, "NaN" if math.isnan(v) else "无穷"))

    if not bad:
        return CheckResult(
            "scan", fname, True,
            "%s 在 %g ~ %g 之间取了 %d 个值，全程无异常、无 NaN、无无穷"
            % (var, lo, hi, n + 1),
            note=spec.get("note", ""))

    return CheckResult(
        "scan", fname, False,
        "%s 在 %g ~ %g 之间扫描出 %d 处问题：%s"
        % (var, lo, hi, len(bad), "；".join(bad[:3]) + ("…" if len(bad) > 3 else "")),
        note=spec.get("note", ""))


# ============================================================
# 八、检查 7：不等式关系
# ============================================================
# 做法：在同一情境下比较两个表达式的大小关系。
# 物理里有很多重要的结论是"不等式"而不是"等式"，例如
#   匀变速直线运动：中间位置的瞬时速度 ≥ 中间时刻的瞬时速度
#   匀加速直线运动：连续相等时间内的位移越往后越大
# 这类结论用等式检查抓不到，所以单独做一条。

def check_compare(fname, ast_rhs, symbols, spec):
    values = dict(spec["_scenario_values"])
    op = spec.get("op", ">")
    note = spec.get("note", "")
    scenario_name = spec.get("scenario")
    tol = spec.get("tol", 1e-9)

    if op not in ("<", ">", "<=", ">=", "==", "!="):
        return CheckResult("compare", fname, False, "不认识的关系符号 %r" % op)

    try:
        ast_a = _spec_ast(spec, ast_rhs, "expr")
        ast_b = _spec_ast(spec, ast_rhs, "vs")
    except EX.ExprError as exc:
        return CheckResult("compare", fname, False, "检查项里的表达式写不通：%s" % exc)

    miss = sorted(set(_missing_for(ast_a, symbols, values))
                  | set(_missing_for(ast_b, symbols, values)))
    if miss:
        return CheckResult("compare", fname, False,
                           "情境「%s」缺少这些变量的值：%s" % (scenario_name, "、".join(miss)))

    try:
        env = build_env(symbols, values)
        a = EX.evaluate(ast_a, env).value
        b = EX.evaluate(ast_b, env).value
    except Exception as exc:
        return CheckResult("compare", fname, False, "比较时出错：%s" % exc)

    if a is None or b is None:
        return CheckResult("compare", fname, False, "比较时有一边没有数值")

    scale = max(abs(a), abs(b), 1.0)
    d = a - b
    if abs(d) <= tol * scale:
        actual = "=="
    else:
        actual = ">" if d > 0 else "<"

    # 满足 "<=" 时也满足 "<"（相等的情况）。这里按"实际是否满足关系"来判。
    ok = {
        ">": actual == ">",
        "<": actual == "<",
        "==": actual == "==",
        ">=": actual in (">", "=="),
        "<=": actual in ("<", "=="),
        "!=": actual != "==",
    }[op]

    left_txt = EX.to_plain(ast_a)
    right_txt = EX.to_plain(ast_b)

    if ok:
        return CheckResult(
            "compare", fname, True,
            "情境「%s」：%s = %.6g，%s = %.6g，满足 %s %s %s"
            % (scenario_name, left_txt, a, right_txt, b, left_txt, op, right_txt),
            note=note)

    return CheckResult(
        "compare", fname, False,
        "情境「%s」：%s = %.6g，%s = %.6g，不满足 %s %s %s"
        % (scenario_name, left_txt, a, right_txt, b, left_txt, op, right_txt),
        note=note)


# ============================================================
# 九、总调度：对一条公式跑完所有声明的检查
# ============================================================

def run_formula_checks(rec, symbols, scenarios, other_formulas):
    """
    对一条公式记录跑完它声明的全部检查（外加两条不用声明的自动检查）。

    参数：
        rec            —— 一条公式记录（来自 kb 的 JSON）
        symbols        —— 符号表
        scenarios      —— 情境表 {情境名: {变量名: 数值}}
        other_formulas —— 同一个知识点里其他公式，供"跨公式一致"用
    """
    results = []
    fname = rec["name"]

    # --- 先解析公式。语法错了后面全都免谈。 ---
    try:
        lhs, rhs = EX.parse_equation(rec["expr"])
    except EX.ExprError as exc:
        results.append(CheckResult("dimension", fname, False,
                                   "公式本身写不通：%s" % exc, error=str(exc)))
        return results, None

    lhs_vars = EX.collect_vars(lhs)
    if len(lhs_vars) != 1:
        results.append(CheckResult(
            "dimension", fname, False,
            "等号左边必须正好是一个变量，实际是 %s"
            % ("、".join(sorted(lhs_vars)) if lhs_vars else "空")))
        return results, None
    lhs_var = list(lhs_vars)[0]

    # --- 检查器一：有没有用到没声明的变量 ---
    used = EX.collect_vars(rhs) | EX.collect_vars(lhs)
    undeclared = sorted(v for v in used
                        if v not in symbols and v not in EX.CONSTANTS
                        and v not in GREEK_REVERSE)
    if undeclared:
        results.append(CheckResult(
            "dimension", fname, False,
            "公式里用了没在符号表中声明的变量：%s" % "、".join(undeclared)))
        return results, None

    # --- 自动检查 1：量纲一致性 ---
    results.append(check_dimension(fname, lhs, rhs, lhs_var, symbols))
    # --- 自动检查 2：单位标注 ---
    results.append(check_unit(fname, lhs, rhs, lhs_var, symbols))

    # --- 记录型检查：逐条按声明跑 ---
    for spec in rec.get("checks", []):
        ctype = spec.get("type")

        # 把情境名换成实际数值
        spec = dict(spec)
        if spec.get("scenario"):
            sc = spec["scenario"]
            if sc not in scenarios:
                results.append(CheckResult(
                    ctype or "?", fname, False,
                    "引用了不存在的情境「%s」" % sc))
                continue
            spec["_scenario_values"] = scenarios[sc]
        else:
            spec["_scenario_values"] = {}

        if ctype == "numeric":
            results.append(check_numeric(fname, lhs, rhs, lhs_var, symbols, spec))
        elif ctype == "direction":
            results.append(check_direction(fname, rhs, symbols, spec, lhs_var))
        elif ctype == "consistency":
            results.append(check_consistency(fname, rhs, symbols, spec, other_formulas))
        elif ctype == "scan":
            results.append(check_scan(fname, rhs, symbols, spec))
        elif ctype == "compare":
            results.append(check_compare(fname, rhs, symbols, spec))
        else:
            results.append(CheckResult(
                ctype or "?", fname, False,
                "不认识的检查类型：%r（可选：numeric / direction / consistency / scan / compare）" % ctype))

    return results, {"lhs": lhs, "rhs": rhs, "lhs_var": lhs_var}


# ============================================================
# 九、报告渲染（纯文本，命令行里看）
# ============================================================

def render_report(entries, verbose=True):
    """
    entries 是 [(知识点标题, [CheckResult, ...]), ...]
    返回一段可直接打印的文本，以及统计数字。
    """
    lines = []
    total = passed = 0
    by_type = {}

    for title, results in entries:
        bad = [r for r in results if not r.ok]
        total += len(results)
        passed += len(results) - len(bad)
        for r in results:
            d = by_type.setdefault(r.type, [0, 0])
            d[0] += 1
            if r.ok:
                d[1] += 1

        if verbose or bad:
            head = "✔" if not bad else "✘"
            lines.append("%s %s（%d/%d 通过）" % (head, title, len(results) - len(bad), len(results)))
            for r in results:
                if verbose or not r.ok:
                    lines.append("    %s [%s] %s" % ("✔" if r.ok else "✘", r.type_name, r.detail))
                    if r.note:
                        lines.append("        依据：%s" % r.note)
            lines.append("")

    lines.append("─" * 62)
    lines.append("合计：%d 项检查，%d 项通过，%d 项未通过" % (total, passed, total - passed))
    lines.append("")
    lines.append("分类：")
    ALL_TYPES = ("dimension", "unit", "numeric", "direction",
                 "consistency", "compare", "scan")
    for t in ALL_TYPES:
        if t in by_type:
            tot, p = by_type[t][0], by_type[t][1]
            lines.append("    %-10s %d/%d   %s"
                         % (CHECK_NAMES.get(t, t), p, tot,
                            "全部通过" if p == tot else "★ 有未通过"))
    lines.append("─" * 62)

    return "\n".join(lines), (total, passed, by_type)
