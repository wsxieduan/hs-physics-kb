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
    """保留量纲、实际表达式、精度与结构化状态。"""
    def __init__(self,check_type,formula,ok,detail,note='',error=None,evaluation_status=None,result_dimension=None,reference_dimension=None,diagnostics=None,status=None):
        self.type=check_type;self.formula=formula;self.ok=ok;self.detail=detail;self.note=note;self.error=error
        self.evaluation_status=evaluation_status or status or ('success' if ok else 'unclassified_failure')
        self.status=self.evaluation_status;self.result_dimension=result_dimension;self.reference_dimension=reference_dimension;self.diagnostics=diagnostics or {}
    @property
    def type_name(self):return CHECK_NAMES.get(self.type,self.type)
    def as_dict(self):
        return {'type':self.type,'type_name':self.type_name,'formula':self.formula,'ok':self.ok,'detail':self.detail,'note':self.note,'error':self.error,'evaluation_status':self.evaluation_status,'failure_reason':None if self.ok else self.evaluation_status,'result_dimension':self.result_dimension,'reference_dimension':self.reference_dimension,'diagnostics':self.diagnostics,'physical_error_identified':self.evaluation_status in ['numeric_mismatch','dimension_conflict','physical_mismatch']}


# 结构化状态取值（给上层判断用，不靠中文文案识别）
EVAL_STATUS_OK = "ok"                            # 算得对，通过
EVAL_STATUS_NUMERIC_MISMATCH = "numeric_mismatch"  # 算出来了，但数值对不上
EVAL_STATUS_DIMENSION_CONFLICT = "dimension_conflict"  # 量纲对不上（速度冒充位移一类）
EVAL_STATUS_NOT_EVALUATED = "not_evaluated"      # 没测成（缺变量、表达式写不通等）

# 默认相对精度：源和检查项都不声明精度时，一律按「相差不超过被比较量级的百万分之一」判。
# （2026-10-05 由质检方抽出成常量，供「声明精度不得宽于默认」的护栏引用。）
_DEFAULT_RTOL = 1e-6


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

def check_dimension(fname,ast_lhs,ast_rhs,lhs_var,symbols):
    try:env=build_env(symbols);left=EX.evaluate(ast_lhs,env);right=EX.evaluate(ast_rhs,env)
    except Exception as exc:return CheckResult('dimension',fname,False,'量纲检查：'+str(exc),error=str(exc),evaluation_status=exception_status(exc))
    ok=left.dim==right.dim
    return CheckResult('dimension',fname,ok,'左端%s，右端%s'%(left.dim,right.dim),evaluation_status='success' if ok else 'dimension_conflict',result_dimension=str(right.dim),reference_dimension=str(left.dim))


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


def _signed_default_limit(scale):
    """「没有声明精度时」的默认允许差：纯相对 1e-6。
    另外保留 0.5 倍信号的上限 —— 容差绝不能大到把答案整个吞掉
    （参考量很小时，若还按 max(1,|ref|) 给绝对容差，容差会比答案本身还大）。"""
    return min(_DEFAULT_RTOL*scale,.5*scale)


def _MAX_DECLARED_LIMIT(scale):
    """源可以自己放宽到的上限：默认精度的 100 倍（相对 1e-4）。"""
    return _DEFAULT_RTOL*100*scale


def comparison(a,b,spec):
    """对称精度：新字段优先（但不能宽于默认），旧tol有界兼容，默认纯相对。"""
    scale=max(abs(a),abs(b));zero=spec.get('expected_zero',False)
    if zero:
        if not spec.get('zero_basis') or 'atol' not in spec:raise ValueError('理论零必须声明物理依据和绝对精度atol')
        atol=float(spec['atol']);rtol=float(spec.get('rtol',0))
        if rtol!=0:raise ValueError('理论零的rtol应为0')
        limit=atol;policy='有依据理论零：两端分别与0比较'
    elif 'rtol' in spec or 'atol' in spec:
        rtol=float(spec.get('rtol',1e-6));atol=float(spec.get('atol',0));declared=max(atol,rtol*scale)
        # ★ 质检方护栏（2026-10-05 加）：源里「显式声明」的精度只能让检查**更严**，不能让它**更松**。
        #   理由是：若允许放宽，内容只要多写一行 "rtol": 1e-3，就能把这条检查的标准放宽一千倍，
        #   等于内容自己给自己开后门 —— 校验器就形同虚设（这正是「检查能否被绕过」的核心问题）。
        #   想要更严 → 照你更严的来；想要更松 → 一律夹回默认上限，并在下面写明「已收紧」。
        #   理论零走它自己的绝对容差通道，不受这条影响。
        default_limit=_signed_default_limit(scale)
        # 有依据 + 有上限的例外：有些检查手算参考值只写到 6 位有效数字（如 osc-03 频率），
        #   这时候若逼它对齐默认百万分之一，等于逼内容把「手算参考」改写成机器精度，反而不像独立核对。
        #   所以允许放宽，但三条缺一不可：① 必须写明 precision_basis（为什么可以松）
        #   ② 最多放宽到默认的 100 倍（相对 1e-4），再松就是自己给自己开后门
        #   ③ 实际用掉的额度会写进 diagnostics，事后能查是谁用了、用了多少
        basis=str(spec.get('precision_basis') or '').strip()
        if declared>default_limit and basis and declared<=_MAX_DECLARED_LIMIT(scale):
            limit=declared;policy='源声明并有依据的放宽（不超过默认 100 倍）：'+basis
        elif declared>default_limit:
            limit=default_limit;policy='源想放宽但%s，已收紧到默认' % ('没有写明依据' if not basis else '超出允许的 100 倍上限')
        else:limit=declared;policy='显式rtol/atol（严于默认，照此执行）'
    elif 'tol' in spec:
        rtol=float(spec['tol']);atol=0;limit=min(rtol*max(1.0,scale),.5*scale);policy='旧tol有界兼容：对称量级，不超过非零信号一半'
    else:rtol=_DEFAULT_RTOL;atol=0;limit=_signed_default_limit(scale);policy='默认纯相对：rtol=1e-6，atol=0'
    if not all(math.isfinite(x) and x>=0 for x in [rtol,atol,limit]):raise ValueError('容差必须为有限非负数')
    if not all(math.isfinite(x) for x in [a,b]):raise ValueError('比较值必须为有限实数')
    ok=max(abs(a),abs(b))<=atol if zero else abs(a-b)<=limit
    # declared_limit 记的是「源头原本想要多少」，effective_limit 是实际执行的；两者不同就说明被夹过，方便事后查谁想放宽
    declared_limit=max(atol,rtol*scale)
    return ok,{'rtol':rtol,'atol':atol,'effective_limit':limit,'declared_limit':declared_limit,'capped_by_default':declared_limit>limit,'scale':scale,'policy':policy,'expected_zero':zero,'zero_basis':spec.get('zero_basis')}


def exception_status(exc):
    """按类型和结构化字段分类，不检索中文报错。

    ★ 2026-10-05 质检方补：定义域与溢出的兜底分类。
      expr.py 里的函数已经显式抛 ExprError(domain_error/overflow_error)；
      Python 自带的 math 抛出的 ValueError/OverflowError 在这里兜住，
      免得它们掉进笼统的 evaluation_error 里、被当成"没测成"。
    """
    if isinstance(exc,DimError):return 'dimension_conflict'
    if isinstance(exc,UnitError):return 'unit_error'
    if isinstance(exc,EX.ExprError):return getattr(exc,'evaluation_status','syntax_error')
    if isinstance(exc,ZeroDivisionError):return 'domain_error'
    if isinstance(exc,OverflowError):return 'overflow_error'
    if isinstance(exc,ValueError):return 'domain_error'
    return 'evaluation_error'


def _declared_dim(symbols, name):
    """从符号表取出某个量声明的量纲；没声明单位或解析不了就返回 None（表示没法比）。"""
    info = symbols.get(name)
    if not isinstance(info, dict):
        return None
    unit = info.get("unit", "")
    if not unit:
        return None
    try:
        return parse_unit(unit)
    except UnitError:
        return None


def _dim_text(dim):
    """把量纲写成人能读的样子，报错时用。"""
    try:
        return str(dim)
    except Exception:
        return "未知量纲"


def _zero_limit(spec, tol):
    """理论零检查用的绝对容差：源给了 atol 就用 atol，否则退回 tol。

    「理论零」不是「算出来恰好是 0」，而是物理上真值就是 0：
    临界速度下的绳张力、逃逸临界处的总机械能、半周期处的正弦电压、竖直最高点的竖直分速度。
    这类检查的正确结果是 1e-15 级的浮点残差，**没有「参考值的比例」可谈**，
    只能问「离 0 够不够近」，所以必须用绝对容差，且不能套比例护栏。
    """
    v = spec.get("atol")
    return v if v is not None else tol


def check_numeric(fname,ast_lhs,ast_rhs,lhs_var,symbols,spec):
    metadata={'primary_expression':spec.get('expr') or EX.to_plain(ast_rhs),'target_expression':spec.get('target_expr') or EX.to_plain(ast_lhs),'follows_primary_formula_changes':not bool(spec.get('expr')),'primary_source_rhs':EX.to_plain(ast_rhs),'numeric_reference':spec.get('ref')}
    try:
        actual_ast=_spec_ast(spec,ast_rhs);target_ast=EX.parse(spec['target_expr']) if spec.get('target_expr') else ast_lhs
        metadata.update(actual_variables=sorted(EX.collect_vars(actual_ast)),target_variables=sorted(EX.collect_vars(target_ast)))
        values=spec['_scenario_values'];env=build_env(symbols,values);actual=EX.evaluate(actual_ast,env);target=EX.evaluate(target_ast,env)
        if actual.dim!=target.dim:return CheckResult('numeric',fname,False,'被测结果与实际目标量纲不同',evaluation_status='dimension_conflict',result_dimension=str(actual.dim),reference_dimension=str(target.dim),diagnostics=metadata)
        miss=_missing_for(actual_ast,symbols,values)
        if miss or actual.value is None:return CheckResult('numeric',fname,False,'缺少数值变量：'+'、'.join(miss),evaluation_status='missing_variable',result_dimension=str(actual.dim),reference_dimension=str(target.dim),diagnostics=metadata)
        ok,precision=comparison(actual.value,float(spec['ref']),spec);metadata.update(precision);metadata.update(actual_value=actual.value,reference_value=spec['ref'])
    except Exception as exc:return CheckResult('numeric',fname,False,'数值检查：'+str(exc),error=str(exc),evaluation_status=exception_status(exc),diagnostics=metadata)
    return CheckResult('numeric',fname,ok,'情境「%s」：算得%s=%.12g；参考%.12g；有效容差%.6g'%(spec.get('scenario'),spec.get('target') or metadata['target_expression'],actual.value,spec['ref'],precision['effective_limit']),note=spec.get('note',''),evaluation_status='success' if ok else 'numeric_mismatch',result_dimension=str(actual.dim),reference_dimension=str(target.dim),diagnostics=metadata)


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
        return CheckResult("direction", fname, False, "检查项里的表达式写不通：%s" % exc, evaluation_status=exception_status(exc))

    try:
        base_q = build_env(symbols, base)
    except Exception as exc:
        return CheckResult("direction", fname, False, "组装环境时出错：%s" % exc, evaluation_status=exception_status(exc))

    # 被改变的那个变量取它的量纲（没声明过就当作无量纲）
    var_dim = base_q[var].dim if var in base_q else Dim()

    def value_at(x):
        env = dict(base_q)
        env[var] = Quantity(float(x), var_dim)
        return EX.evaluate(ast, env).value

    try:
        v_lo, v_hi = value_at(lo), value_at(hi)
    except Exception as exc:
        return CheckResult("direction", fname, False, "取参数值算账时出错：%s" % exc, evaluation_status=exception_status(exc))

    if v_lo is None or v_hi is None:
        # 结果算不出数值，通常不是"被改变的那个变量"没给值，
        # 而是情境里其他变量缺了值 —— 这里把真正缺的变量报出来，别误导。
        miss = _missing_for(ast, symbols, spec["_scenario_values"])
        if miss:
            return CheckResult("direction", fname, False,
                               "情境里缺少这些变量的值：%s" % "、".join(miss), evaluation_status="missing_variable")
        return CheckResult("direction", fname, False,
                           "结果算不出数值，无法判断方向", evaluation_status="missing_variable")

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
        note=spec.get("note", ""), evaluation_status="physical_mismatch")


# ============================================================
# 六、检查 5：关系一致核对
# 默认执行源公式右端；显式expr/with_expr会完整替代相应右端。
# 结果必须记录实际表达式、依赖变量和源绑定；覆盖后未执行的源式不能宣称已验证。
# 分类按源声明保留：关系核对、设计情境核对、同一关系的计算实现核对。
# 两端相同数值不证明来自不同物理原理；共同错误需另有独立物理证据和教师审核。

def check_consistency(fname,ast_rhs,symbols,spec,other_formulas):
    name=spec.get('with');other=other_formulas.get(name)
    metadata={'primary_expression':spec.get('expr') or EX.to_plain(ast_rhs),'primary_source_rhs':EX.to_plain(ast_rhs),'primary_formula_binding':not bool(spec.get('expr')),'reference_formula':name,'reference_formula_binding':not bool(spec.get('with_expr')),'reference_relation_follows_formula_changes':not bool(spec.get('with_expr')),'verification_kind':spec.get('verification_kind','未声明类型')}
    if not other:return CheckResult('consistency',fname,False,'参考公式不存在',evaluation_status='missing_formula',diagnostics=metadata)
    try:
        a_ast=_spec_ast(spec,ast_rhs);b_ast=_spec_ast(spec,other['rhs'],'with_expr');metadata.update(reference_expression=spec.get('with_expr') or EX.to_plain(other['rhs']),reference_source_rhs=EX.to_plain(other['rhs']),primary_variables=sorted(EX.collect_vars(a_ast)),reference_variables=sorted(EX.collect_vars(b_ast)))
        env=build_env(symbols,spec['_scenario_values']);a=EX.evaluate(a_ast,env);b=EX.evaluate(b_ast,env)
        if a.dim!=b.dim:return CheckResult('consistency',fname,False,'一致性两端量纲不同',evaluation_status='dimension_conflict',result_dimension=str(a.dim),reference_dimension=str(b.dim),diagnostics=metadata)
        miss=sorted(set(_missing_for(a_ast,symbols,spec['_scenario_values']))|set(_missing_for(b_ast,symbols,spec['_scenario_values'])))
        if miss or a.value is None or b.value is None:return CheckResult('consistency',fname,False,'缺少数值变量：'+'、'.join(miss),evaluation_status='missing_variable',result_dimension=str(a.dim),reference_dimension=str(b.dim),diagnostics=metadata)
        ok,precision=comparison(a.value,b.value,spec);metadata.update(precision);metadata.update(primary_value=a.value,reference_value=b.value)
    except Exception as exc:return CheckResult('consistency',fname,False,'一致性检查：'+str(exc),error=str(exc),evaluation_status=exception_status(exc),diagnostics=metadata)
    detail='情境「%s」：本式%.12g，参考关系%.12g；有效容差%.6g'%(spec.get('scenario'),a.value,b.value,precision['effective_limit'])
    if spec.get('with_expr'):detail+='；显式参考关系不自动跟随参考公式修改'
    if spec.get('expr'):detail+='；主端已覆盖，未绑定所属公式右端'
    return CheckResult('consistency',fname,ok,detail,note=spec.get('note',''),evaluation_status='success' if ok else 'numeric_mismatch',result_dimension=str(a.dim),reference_dimension=str(b.dim),diagnostics=metadata)


# ============================================================
# 七、检查 6：极端参数扫描
# ============================================================
# 做法：让参数在一个区间里连续取很多值，看会不会崩、会不会算出 NaN / 无穷。
# 这是最省事的一条 —— 什么都不用提供，只要有公式就行。

def check_scan(fname,ast_rhs,symbols,spec):
    """扫描必须实际求值；缺变量、语法或量纲错误不可显示全程正常。"""
    rows=[]
    try:
        var=spec['var'];lo=spec['lo'];hi=spec['hi'];n=spec.get('n',60)
        if n<1:return CheckResult('scan',fname,False,'扫描次数必须为正',evaluation_status='invalid_spec')
        env=build_env(symbols,spec['_scenario_values'])
        if var not in env:return CheckResult('scan',fname,False,'扫描变量未声明',evaluation_status='missing_variable')
        for k in range(n+1):
            x=lo+(hi-lo)*k/n;step=dict(env);step[var]=Quantity(float(x),env[var].dim)
            try:
                q=EX.evaluate(ast_rhs,step)
                state='missing_variable' if q.value is None else 'success' if math.isfinite(q.value) else 'domain_error'
                rows.append({'parameter':x,'evaluation_status':state,'value':q.value,'dimension':str(q.dim)})
            except Exception as exc:rows.append({'parameter':x,'evaluation_status':exception_status(exc),'error':str(exc)})
    except Exception as exc:return CheckResult('scan',fname,False,'扫描设置或环境错误：'+str(exc),evaluation_status=exception_status(exc))
    failed=[r for r in rows if r['evaluation_status']!='success'];status=failed[0]['evaluation_status'] if failed else 'success'
    return CheckResult('scan',fname,not failed,'%s在%g~%g扫描%d个参数，失败%d处'%(var,lo,hi,len(rows),len(failed)),note=spec.get('note',''),evaluation_status=status,diagnostics={'scan':rows,'primary_expression':EX.to_plain(ast_rhs)})


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
        return CheckResult("compare", fname, False, "检查项里的表达式写不通：%s" % exc, evaluation_status=exception_status(exc))

    miss = sorted(set(_missing_for(ast_a, symbols, values))
                  | set(_missing_for(ast_b, symbols, values)))
    if miss:
        return CheckResult("compare", fname, False,
                           "情境「%s」缺少这些变量的值：%s" % (scenario_name, "、".join(miss)), evaluation_status="missing_variable")

    try:
        env = build_env(symbols, values)
        a = EX.evaluate(ast_a, env).value
        b = EX.evaluate(ast_b, env).value
    except Exception as exc:
        return CheckResult("compare", fname, False, "比较时出错：%s" % exc, evaluation_status=exception_status(exc))

    if a is None or b is None:
        return CheckResult("compare", fname, False, "比较时有一边没有数值", evaluation_status="missing_variable")

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
        note=note, evaluation_status="physical_mismatch")


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
                                   "公式本身写不通：%s" % exc, error=str(exc),
                                   evaluation_status="syntax_error"))
        return results, None

    lhs_vars = EX.collect_vars(lhs)
    if len(lhs_vars) != 1:
        results.append(CheckResult(
            "dimension", fname, False,
            "等号左边必须正好是一个变量，实际是 %s"
            % ("、".join(sorted(lhs_vars)) if lhs_vars else "空"),
            evaluation_status="invalid_formula_shape"))
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
            "公式里用了没在符号表中声明的变量：%s" % "、".join(undeclared),
            evaluation_status="missing_variable"))
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
                    "引用了不存在的情境「%s」" % sc,
                    evaluation_status="missing_scenario"))
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
                "不认识的检查类型：%r（可选：numeric / direction / consistency / scan / compare）" % ctype,
                evaluation_status="invalid_spec"))

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
