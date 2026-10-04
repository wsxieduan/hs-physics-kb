# -*- coding: utf-8 -*-
"""
prose_math.py —— 正文散文里「机器写法公式」的排版还原
======================================================

【它解决什么问题】

知识库源文件（kb/*.json）里的公式会出现在两个地方：

  1. `expr` 字段 —— 机器可读形式，专门喂给校验器。**必须保持原样。**
  2. 正文散文 —— `definition` / `meaning` / `derivation` / 例题 /
     「常见错误」的说明文字。这些是写给人读的。

问题出在第 2 类：作者写作时有时会顺手敲成机器写法，于是页面上出现

    T = 2pi sqrt(L/g)          x = A cos(omega t + phi)
    F_合 = m a                 E = k A^2/2

教材上从来不会这么写。本模块负责把它们还原成正常样子：

    T = 2pi sqrt(L/g)        ->  T = 2π √(L/g)
    x = A cos(omega t + phi) ->  x = A cos(ω t + φ)
    F_合 = m a               ->  F<sub>合</sub> = m a
    E = k A^2/2              ->  E = k A²/2

【为什么改渲染层，而不去改内容】

  · 校验器比的是 `expr` 字段，正文公式**不参与任何校验** —— 所以这个改动
    不可能影响「校验通过」的结论；
  · 转换规则全部是「查表 + 正则」，不含任何物理判断，因此不可能把式子的
    含义改掉（它只改写法，不改内容）；
  · 一处规则覆盖全部知识点，不必让作者逐条手写规范 —— 规范化由流水线统一
    负责，而不是靠人自觉。

【安全约定】

  · 只输出 HTML 转义后的文本，外加本模块自己生成的 <sub> / <sup> 标签；
  · 幂等：已经是 Unicode 数学符号（π ω μ √ ²）的内容不会再被改写；
  · 只认「确定的机器写法」（`sqrt(` `omega` `^2` `_下标` `*`），
    不做任何猜测式的改写。

【怎么自测】
    python prose_math.py

Copyright 提示：本模块只参与「出成品」的渲染，不属于冻结的校验器。
它的改动不需要重冻质检基线，但要在 work/校验器变更.md 里登记。
"""

import html
import re
import xml.etree.ElementTree as ET
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps

# 同一机器后缀在不同卡片含义不同，显示时必须带上卡片上下文。
# 未登记的符号保持原样，不能凭 p 猜测末态；计算变量始终不变。
_POINT = ContextVar('排版卡片', default=None)
P_DISPLAY = {
    'mom-03': {'v1_p': ('v1', '′'), 'v2_p': ('v2', '′')},
    'mom-04': {'v1_p': ('v1', '′'), 'v2_p': ('v2', '′')},
    'osc-07': {'f_p': ('f', '接收'), 'T_p': ('T', '接收')},
    'dc-05': {'R_p': ('R', '并')},
    'iad-06': {'v1_p': ('v1', '稳'), 'v2_p': ('v2', '稳')},
    'iad-07': {'v_p': ('v', '′')},
    'wop-03': {'I_p': ('I', '透')},
    'qz-08': {'l_p': ('λ', '′')},
    'exp-04': {'v1_p': ('v1', '′'), 'v2_p': ('v2', '′'), 'p_p': ('p', '′')},
    'mdl-06': {'v_p': ('v', '′')},
    'mdl-07': {'x_p': ('x', '人')},
    'mdl-13': {'v_p': ('v', '近')},
    'mdl-22': {'U_p': ('U', '初级'), 'N_p': ('N', '初级')},
    'mdl-23': {'U_p': ('U', '′')},
    'mdl-28': {'a_p': ('a', '物'), 'T_p': ('T', '绳')},
    'mdl-29': {'W_p': ('W', '等压')},
    'mdl-36': {'a_p': ('a', '板')},
    'mdl-37': {'t_p': ('t', '飞行')},
    'mdl-38': {'t_p': ('t', '飞行')},
    'exp-11': {'v_p': ('v', '′')},
    'exp-12': {'t_p': ('t', '平抛'), 'x_p': ('x', '平抛'), 'R_p': ('R', '斜抛')},
    'exp-18': {'T_p': ('T', '摆'), 'L_p': ('L', '摆'), 'N_p': ('N', '周期数')},
    'exp-25': {'K_p': ('K', '′')},
}

@contextmanager
def symbol_context(point_id):
    """只在一张卡片的显示过程中启用映射，结束时还原，避免串卡。"""
    token = _POINT.set(point_id)
    try:
        yield
    finally:
        _POINT.reset(token)

def card_symbols(first_is_id=False):
    """给已有卡片渲染入口补上下文，不改公式与校验器。"""
    def decorate(func):
        @wraps(func)
        def wrapped(first, *args, **kwargs):
            pid = first if first_is_id else first.get('id')
            with symbol_context(pid):
                return func(first, *args, **kwargs)
        return wrapped
    return decorate

def _p_display(name, point_id=None, mathml=False):
    """正文和 MathML 共用唯一的物理语义表。"""
    pid = point_id if point_id is not None else _POINT.get()
    name = re.sub(r'^v_([0-9]+)_p$', r'v\1_p', name)
    spec = P_DISPLAY.get(pid, {}).get(name)
    # 无上下文的旧接口兼容已存在的 v 系排版测试；实际页面均传卡片。
    if spec is None and pid is None and re.fullmatch(r'v[0-9]*_p', name):
        spec = (name[:-2], '′')
    if spec is None:
        return None
    base, suffix = spec
    numbered = re.fullmatch(r'([A-Za-z])([0-9]+)', base)
    if mathml:
        if suffix == '′':
            if numbered:
                return '<msubsup><mi>%s</mi><mn>%s</mn><mo>′</mo></msubsup>' % numbered.groups()
            return '<msup><mi>%s</mi><mo>′</mo></msup>' % base
        if numbered:
            base = numbered[1]
            suffix = numbered[2] + suffix
        return '<msub><mi>%s</mi><mi mathvariant="normal">%s</mi></msub>' % (base,suffix)
    if numbered:
        base = numbered[1] + '<sub>' + numbered[2] + '</sub>'
    return base + ('′' if suffix == '′' else '<sub>' + suffix + '</sub>')

__all__ = ["render", "display_mathml"]


# ============================================================
# 一、词表
# ============================================================

# 机器名 -> 数学符号。顺序有讲究：长的放前面，避免被短的抢先切走。
_WORDS = [
    ("wavelength", "λ"),
    ("omega", "ω"),
    ("theta", "θ"),
    ("lambda", "λ"),
    ("alpha", "α"),
    ("gamma", "γ"),
    ("delta", "δ"),
    ("sigma", "σ"),
    ("Omega", "Ω"),
    ("Theta", "Θ"),
    ("Lambda", "Λ"),
    ("Delta", "Δ"),
    # ★ 2026-09-29 补：`nu` 频率与 `Phi` 磁通量。
    #   这两个在库里出现得极多（nu 18 处、Phi 40 处），却一直没进词表，
    #   于是页面上直接印着英文字母 —— 这是"机器名残留"里最显眼的一批。
    ("Phi", "Φ"),     # 磁通量（大写，教材写 Φ）
    ("phi", "φ"),
    ("rho", "ρ"),
    ("tau", "τ"),
    ("eta", "η"),
    ("psi", "ψ"),
    ("zeta", "ζ"),
    ("nu", "ν"),      # 频率（教材写 ν；physkit 的 MathML 路径本来就已经转了）
    ("mu", "μ"),
    ("pi", "π"),
    ("beta", "β"),
]

# 只替换「独立成词」的机器名：前后都不能紧挨着字母。
# 例：`omega*T` 会替换，`spin` 里的 `pi` 不会（前面是字母 s）。
_WORD_MAP = dict(_WORDS)
_WORDS_RE = re.compile(
    "|".join(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(w) for w, _ in _WORDS)
)

# ---- ★ 内部代号 → 教材写法（2026-09-19 人工复核发现的问题） ----
#
# 为什么必须单独列这一条：
#
#   `physkit/quantity.py` 的单位表里，**特斯拉的键写作 "T_"** ——
#   因为 `T` 已经被「时间」的量纲代号占用了，只能加个下划线避让。
#   那是**校验器内部的命名**，本来不该出现在给人看的地方。
#   但内容里也照抄了这个键（`"unit": "T_"`），显示层又不认识它，
#   于是页面上直接印出了 `T_` —— 读者看到一个不存在的单位符号。
#   实测：成品里 24 处，源文件 19 处。（`ohm` 同理，教材应写 Ω。）
#
# ⚠ 规则必须**带前瞻断言**，不能用简单的 replace：
#   正文里 `T_0` 是「周期 T₀」，不是特斯拉！`1/T_0` 若被改成 `T0` 就是新的错误。
#   所以只替换「后面不再接字母数字下划线」的那种 `T_`（即独立成单位的样子）。
_ALIAS = [
    (re.compile(r"(?<![A-Za-z])ohm(?![A-Za-z])"), "\u03a9"),   # ohm -> Ω（欧姆）
    (re.compile(r"T_(?![0-9A-Za-z_])"), "T"),                 # T_  -> T（特斯拉）
]

# 上标字符表（用 Unicode 上标，比 <sup> 更贴合行内文字流）
_SUP = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "n": "ⁿ", "i": "ⁱ",
}

# ★ 希腊字母区（U+0370–U+03FF）。
# 必须单独列出来 —— 替换会把 `omega` 变成 `ω`、`mu` 变成 `μ`，
# 而 ω / μ / δ 都不在 ASCII 的 [A-Za-z] 里，后面的乘号、下标规则
# 如果只认 ASCII，就会认不出它们（这是实测踩过的坑）。
_GK = r"\u0370-\u03ff"

# `sqrt(` —— 函数名换成根号，括号保留
_RE_SQRT = re.compile(r"(?<![A-Za-z])sqrt\s*\(")

# 乘号：只有两侧都是「运算对象」的 `*` 才换成 `·`
# ★ 2026-09-29：前瞻里补上 `√` —— 原先只认字母/数字/左括号，
#   于是 `2*pi*sqrt(...)` 里的第二个 `*` 后面紧跟根号，没被认成乘号，
#   页面上就留下了 `2·π*√(…)` 这种半机器写法（实测 21 处里占了一半）。
_RE_MUL = re.compile(
    r"(?<=[A-Za-z0-9" + _GK + r"\u00b2\u00b3\u2070-\u209f\u4e00-\u9fff\)])"
    r"\s*\*\s*"
    r"(?=[A-Za-z0-9\(\u221a" + _GK + r"\u4e00-\u9fff])"
)

# 数字上标：^2 -> ²、^-1 -> ⁻¹
_RE_SUP_NUM = re.compile(r"\^(-?\d+)")
# 单字母上标：^n -> ⁿ（表里没有的退化成 <sup>）
_RE_SUP_VAR = re.compile(r"\^([A-Za-z])")
# ★ 括号上标：^(1/3) -> <sup>1/3</sup>（`R = r_0·A^(1/3)` 这类分数指数）
_RE_SUP_PAREN = re.compile(r"\^\(([^()]+)\)")

# ★ markdown 加粗：`**有**` -> <strong>有</strong>
#   正文里作者用了 markdown 记号，但成品页面不跑 markdown 渲染，
#   于是星号原样印出来。这一条只认「成对」的 ** ，不会碰算式里的乘号
#   （乘号两侧是运算对象，而 `**` 的左邻是另一个 `*`，构不成乘号）。
_RE_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")

# ★ 2026-09-29 晚（第六批）：`0.5*` 是机器写法的系数，教材写 ½（二分之一）。
#   主人反馈：动能那块印成 0.5mv²，"0.5 不常用，常用的是二分之一"。
#   只认「0.5 后紧跟乘号、乘号后是符号/左括号/根号」的形态 —— 那一定是公式
#   系数；`0.5 × 3`（算式）、`0.5 m/s`（测量值）、`0.5 s`（题目数据）不动。
_RE_HALF = re.compile(
    r"(?<![A-Za-z0-9.])0\.5\s*\*\s*(?=[A-Za-z\u0370-\u03ff(\u221a])")

# ------------------------------------------------------------------
# 下标词表（2026-09-29 全量补齐）
#
# 为什么非得先把词表穷举出来：下标改成「定长记号」匹配以后，凡是**不在词表里**
# 的多字母下标就再也匹配不上，页面上会留下 `X_abc` 这种半机器写法 —— 比吞字母
# 更难看。所以先把库里实际出现的下标词穷举一遍（实测 152 种，脚本 _qc_subdump.py），
# 再逐个判定它该怎么显示；今后新增符号必须同步登记，否则自查脚本会报出来。
# ------------------------------------------------------------------

# 改成「上加横线」的下标（教材里"平均值"的标准写法：v̄）
_OVERLINE_SUBS = {"avg"}

# 教材里**根本不写下标**的那些：直接还原成裸符号。
#   emf/const —— 电源电动势就写 E，万有引力常量就写 G
#   level     —— `n_level` 是主量子数，教材就写 n（E_n = E_1/n²）
#   db        —— `lambda_db` 是德布罗意波长，教材就写 λ
# ⚠ 这个集合要手工确认过才加：去掉下标会让符号"变短"，
#   万一同一章里另有一个同名符号就会撞名。以上四个都已逐个核对无冲突。
_SUB_DROP = {"emf", "const", "level", "db"}

# 机器名 -> 中文下标（键是内容里的内部写法，值是给人看的写法）
_SUB_CN = {
    # 实验与模型新增记号：只改展示，内部变量和物理校验保持不变。
    "cnt": "计数", "gate": "遮光", "belt": "带", "true": "校正",
    "s_min": "静临界", "main": "主尺", "fixed": "固定",
    "bottom": "底", "start": "初", "end": "末", "board": "板",
    "pul": "绳", "chord": "弦", "node": "节", "iso": "等压",
    "isoP": "等压", "isoV": "等容", "opt": "光程", "limit": "限",
    "display": "示值", "digit": "末位", "div": "格", "sol": "溶液",
    "power": "功率", "pv": "初", "mot": "反", "ground": "地",
    # 新补录卡的来源词与复合下标，统一使用学生能直接读懂的含义。
    "rope": "绳", "eq": "等效", "rel0": "相对初", "ratio": "比", "2_ratio": "比",
    "total": "总", "tot": "总",
    "ind": "感", "induced": "感",
    "net": "合", "sum": "合",
    "eff": "有效",
    "half": "1/2",
    "before": "前", "after": "后",
    "orbit": "轨", "path": "路", "ext": "外",
    "heat": "热", "eddy": "涡", "drive": "驱",
    "terminal": "端", "turn": "匝", "coil": "线圈",
    "cap": "容", "charge": "荷",
    "gas": "气", "out": "出", "move": "移", "source": "源",
    "line": "线", "rod": "棒", "send": "送", "molecule": "分子",
    "abs": "大小", "signed": "代",
    "left": "左", "right": "右", "front": "前", "back": "后",
    "upper": "上", "lower": "下", "in": "入", "loss": "损",
    "field": "场", "outer": "外", "inner": "内", "inside": "内",
    # ---- 第二批：把「剩余缩写」逐个按实际含义定下来（2026-09-19 晚）----
    "absorb": "吸", "release": "放",          # 低温物体吸热 / 高温物体放热
    "latent": "潜", "sensible": "显热",        # 潜热 / 显热
    "attr": "引", "rep": "斥", "mag": "安",    # 引力 / 斥力 / 安培作用力
    "restore": "回复", "spring": "弹",         # 回复力 / 弹力做功
    "common": "共", "rel": "相对", "rate": "速率",
    "decay": "衰变", "molecular": "分子",
    "series": "串", "shunt": "并",             # 串联分压电阻 / 并联分流电阻
    "test": "试探", "order": "级",             # 试探电荷 / 条纹级次
    "sat": "饱和", "dot": "面积",              # 饱和汽压 / 面积速度
    "perp": "有效", "sep": "分离", "oi": "物像",
    "top": "顶", "far": "远", "near": "近", "push": "拉",
    # ---- 第三批：2026-09-29 自查补的 30 多种（依据同样是每个符号的 desc）----
    "use": "有用",        # W_use 有用功 / E_use 用掉的电能
    "mech": "机械",       # E_mech 机械能
    "sym": "符号",        # R_sym 符号率（信息传输）
    "pull": "拉",         # F_pull 拉力
    "range": "量程",      # I_range / U_range 电表量程
    "loop": "回路",       # R_loop 回路电阻
    "read": "读数",       # I_read / U_read 表的读数
    "old": "旧", "new": "新",                 # 旧设备 / 新设备耗电
    "nucleus": "核", "atom": "原子",          # d_nucleus 核直径 / d_atom 原子直径
    "load": "物",         # G_load 物重 / h_load 物体上升高度
    "info": "信息",       # t_info 信息传递时间
    "gained": "吸", "lost": "放",             # Q_gained 吸热 / Q_lost 放热
    "save": "节",         # E_save 节约的电能
    "water": "水",        # c_water 水的比热容 / Q_water 水吸收的热
    "el": "电",           # E_el 电能 / P_el 电功率
    "ref": "参考",        # Tb_ref 标准沸点 / Tm_ref 标准熔点
    "run": "运行",        # t_run 运行时间
    "gap": "间距",        # s_gap 车间距
    "mean": "平均",       # I_mean 平均电流
    "photon": "光子",     # n_photon 光子数
    "high": "高", "low": "低",                # E_high 高能级 / E_low 低能级
    "solid": "固",        # m_solid 固态质量
    "int": "内",          # E_int 内能
    "x2": "x/2", "t2": "t/2",                 # v_x2 位移中点速度 / v_t2 时间中点速度
    "path1": "路1", "path2": "路2",           # W_path1 / W_path2 两条路径
    # 复合下标（一层嵌套）：整体定名，比拼两段更好读
    "int_gain": "内增",               # E_int_gain 增加的内能
    "mech_before": "机前", "mech_after": "机后",   # 初态 / 末态机械能
    # ★ 以下这些**故意不改**（教材本来就这么写）：
    #   AB / BA —— F_AB 就是「A 对 B 的力」，标准写法
    #   max / min / rms —— 教材标准
    #   kg / cm / km / nm / kWh —— 单位符号，本来就是拉丁字母
}

# 认得出来、但就**按原样**当下标印的词
_SUB_KEEP = {
    "max", "min", "rms", "AB", "BA", "ABx", "BAx",
    "Ep", "Ek",                                # 只在 delta_Ep / delta_Ek 里出现
    "kg", "cm", "km", "nm", "kWh",             # 单位
}

# 词表全集 = 四类下标词的并集（供下标记号正则使用）
_SUB_LEX = set(_SUB_CN) | set(_SUB_DROP) | set(_OVERLINE_SUBS) | _SUB_KEEP


# ------------------------------------------------------------------
# 下标：X_abc -> X<sub>abc</sub>
#
# ★★ 2026-09-29 重写（这是本次最要紧的一处修 bug）★★
#
# 旧写法是「贪婪字符类」：`_([A-Za-z0-9希腊中文]+)` 见到什么吃多少。
# 只要作者忘了在两个相乘的符号之间加空格/乘号，它就会把**后一个符号一起吃掉**：
#
#     k_eQ      -> k<sub>eQ</sub>      应为 k<sub>e</sub>Q
#     q_1q_2    -> q<sub>1q</sub>_2    应为 q<sub>1</sub>q<sub>2</sub>
#     B_1B_2    -> B<sub>1B</sub>_2    应为 B<sub>1</sub>B<sub>2</sub>
#     I_gR_g    -> I<sub>gR</sub>_g    应为 I<sub>g</sub>R<sub>g</sub>
#     Nk_BT     -> Nk<sub>BT</sub>     应为 Nk<sub>B</sub>T
#
# 也就是用户反馈的「公式里正常的字母被当成了角标」。实测 6 处。
#
# 新写法：**下标必须是「一个合法记号」**，由下列形式按优先级择一匹配：
#   1) 词表里的词（最长的先试）—— 见下面的 _SUB_LEX / _SUB_CN
#   2) X_yyy 这种「带一层嵌套」的复合下标（delta_E_total、E_int_gain）
#   3) 字母 + 数字（v_x2、W_path1）
#   4) 纯数字（v_0、R_1）
#   5) 单个拉丁字母（F_N、v_t）
#   6) 单个希腊字母（δ_φ、Δ_Φ）
#   7) 中文（F_合）
#
# 于是 `q_1q_2` 只会吃掉 `1`，剩下的 `q_2` 交给下一轮继续处理
# （见 render() 里的多轮：因为正则的后视断言会挡住紧跟在数字后面的符号）。
# ------------------------------------------------------------------
def _build_sub_body():
    """拼出「下标记号」的正则片段（不含分组）。"""
    words = sorted(_SUB_LEX, key=len, reverse=True)   # 长词优先，避免被短词抢切
    alt = "|".join(re.escape(w) for w in words)
    # ⚠ `%` 比 `+` 结合得更紧，字符串必须先拼完再格式化，否则只有最后一段参与 %
    inner = (r"(?:%s|[A-Za-z]+\d+|\d+|[A-Za-z]|[" + _GK + r"]|[\u4e00-\u9fff]+)")
    inner = inner % alt
    # 复合：`E_total` / `int_gain` —— 只允许再套一层，避免重新变回贪婪
    return r"(?:[A-Za-z]_%s|%s)" % (inner, inner)


# ⚠ 后视断言里**故意不放数字**：`2v_0`、`2Nk_B` 这种"数字紧贴符号"的写法
#   在库里是常态，若把数字也挡住就整条匹配不上，反而留下 `v_0` 原样。
#   挡住的只是字母/下划线/希腊字母 —— 那些才是"标识符还没结束"的信号。
_RE_SUB = re.compile(
    r"(?<![A-Za-z_" + _GK + r"])"
    r"([A-Za-z" + _GK + r"][A-Za-z0-9" + _GK + r"]*)"
    r"_(" + _build_sub_body() + r")"
)

# ★ 作者有时在正文里**直接手写小写 δ**（如「速度的变化量，δv = v − v₀」）。
#   这类「δ 紧跟一个字母」的写法在本库里一律是"变化量"前缀，还原成 Δ。
#   ⚠ 只认「δ 后面紧跟字母」这一种：`δ_φ` 这种带下标的由 _do_sub 处理，
#     而单独一个 δ（后面是空格或中文）不动 —— 万一将来要表示微小量 δ。
_RE_DELTA_PREFIX = re.compile(r"\u03b4(?=[A-Za-z" + _GK + r"])")

# ------------------------------------------------------------------
# ★ 2026-09-29 晚（第六批）：正文里的平排「字母+数字」名 → 教材下标写法
#
#   h1、h2、T1、V2、Ek2 …… 教材写 h₁、h₂、T₁、V₂、E_k2。
#   源数据公式里都写作 h_1 / Ek1（正规写法），但**正文散文**里作者经常
#   顺手写成平排，页面上就成了 h1、Ek2。
#
#   范围用「字母集 + 单个数字」刻画；字母集是全库实际出现过的首字母
#   （E F I R T U V W / d f h l p q s t u v x），逐类核对过正文语境：
#   全部是「同一物理量的第几个 / 初末态」，无一歧义。
#
#   ⚠ 两个刻意的例外：
#   · **不含 m** —— 初中压强一章正文里 m2 大多是「平方米」（0.02 m2、
#     S 用 m2、1 m2=10 000 cm2），转成 m₂ 就是制造错误。公式框 MathML
#     里的 m1/m2 全部来自动量/引力章的「质量1/质量2」，那边照转
#     （见 _RE_MI_NUM 的注释）—— 两条路径各管各的语境。
#   · **小写 e 不在集内** —— 1e-6 这类科学计数法不能被拆成 1ₑ-₆。
#   已核查正文里不存在「数字+字母+数字」的 jammed 形态（全库唯一一处
#   2T3 恰好也是真下标），所以「前一字符是数字」不用设防。
# ------------------------------------------------------------------
_RE_PROSE_SUB = re.compile(
    r"(?<![A-Za-z_.])"
    r"(Ek1|Ek2|Ep1|Ep2|Ek|Ep|([EFIRTUVWdfhlpqstuvx])[0-9])"
    r"(?![0-9_<])"
)


def _do_prose_sub(m):
    tok = m.group(1)
    if tok.startswith("E") and len(tok) >= 2 and tok[1] in "kp":
        return "E<sub>%s</sub>" % tok[1:]           # Ek2 -> E<sub>k2</sub>
    return "%s<sub>%s</sub>" % (tok[0], tok[1:])    # h1  -> h<sub>1</sub>


# ============================================================
# 二、替换件
# ============================================================

def _do_word(m):
    return _WORD_MAP[m.group(0)]


def _do_sup_num(m):
    digits = m.group(1)
    neg = digits.startswith("-")
    if neg:
        digits = digits[1:]
    out = "".join(_SUP.get(c, c) for c in digits)
    return ("⁻" if neg else "") + out


def _do_sup_var(m):
    ch = m.group(1)
    if ch in _SUP:
        return _SUP[ch]
    return "<sup>%s</sup>" % ch


def _do_sub(m):
    """下标还原：`X_abc` → 教材写法。

    ★ 2026-09-19 晚补：**正文里也有同样的缩写问题**。
      之前只修了公式框（走 MathML），漏了正文（走本模块）。
      实测正文里残留 883 处英文缩写下标（total 83 / ind 66 / cap 51 / net 45 / avg 44 …），
      而且正文里的 `delta_r` 也还是 δ_r。这一处补齐后，两条路径口径才一致。
    """
    base, sub = m.group(1), m.group(2)
    if base == "T" and sub == "dot":
        return "T<sub>打点</sub>"
    # 小写 δ 在本库里一律表示「变化量」，还原成 Δ（与 MathML 那条规则同一口径）
    if base == "\u03b4":
        base = "\u0394"
    # 复合下标（E_int_gain、mech_before）先查「整体」有没有定名；查不到再逐段换，
    # 绝不能让下划线留在成品里（那正是"残留机器写法"最难看的一种）。
    if sub not in _SUB_CN and "_" in sub:
        sub = "".join(_SUB_CN.get(part, part) for part in sub.split("_"))

    # ---- Δ 是**前缀算子**，不是带下标的基名 ----
    # `Delta_Phi` 的含义是「磁通量的变化量」，教材写 **ΔΦ**（两个符号并排），
    # 旧实现却印成 Δ<sub>Φ</sub> —— 把 Φ 压成了角标。这正是用户反馈的
    # 「正常字母被当成角标」里最典型的一类（Phi 全库 40 处）。
    # 规则：Δ 后面若是一个短记号（1–2 个字符，拉丁/希腊），就并排写；
    #      若是中文词（如"分离"）则仍作下标，读起来才顺。
    if base == "\u0394":
        if sub.startswith("E_") or (len(sub) == 2 and sub[0] == "E"):
            # ΔE_k / ΔE_总：Δ 挂在 E 上，后面的才是下标（命名系统只有一层下标，
            # `delta_Ek` 只能拆成 base=delta、sub=Ek，只能在显示这步掰回来）
            rest = sub[2:] if sub.startswith("E_") else sub[1:]
            return "\u0394E<sub>%s</sub>" % _SUB_CN.get(rest, rest)
        if sub in _SUB_DROP:
            return "\u0394"
        cn = _SUB_CN.get(sub)
        if cn:
            return "\u0394<sub>%s</sub>" % cn
        if len(sub) <= 2:
            return "\u0394" + sub                     # Δr / Δt / ΔΦ / ΔE
        return "\u0394<sub>%s</sub>" % sub

    if sub in _SUB_DROP:
        return base                                   # E_emf → E、G_const → G
    if sub in _OVERLINE_SUBS:
        # 正文里用行内样式画横线：与公式框的 MathML mover 视觉上一致
        return '<span style="text-decoration:overline">%s</span>' % base
    cn = _SUB_CN.get(sub)
    return "%s<sub>%s</sub>" % (base, cn if cn else sub)


# ============================================================
# 三、对外接口
# ============================================================

def render(text, point_id=None):
    """把一段正文文本还原成教材习惯的写法，返回 HTML 片段。

    · 输入里的 & < > 会被转义；
    · 输出的 <sub> / <sup> 是本模块自己生成的，是唯一允许出现的标签；
    · 空输入返回空串。
    """
    if not text:
        return ""

    s = html.escape(str(text), quote=False)

    # 顺序不能乱：
    #   0) 先把内部代号换成正式符号（T_ -> T、ohm -> Ω）。
    #      **放在最前面**，因为要在下标规则之前判断 —— 否则 `T_` 会先被
    #      当成「T 带空下标」处理，规则就再也认不出它了。
    #   1) 先认机器名（omega_drive -> ω_drive），否则下标规则会把词切碎；
    #   2) 再换成根号（sqrt 内部还有下标/上标，留给后面两步行）；
    #   3) 乘号换成 ·
    #   4) 数字上标（v_0^2 -> v_0²）
    #   5) 最后处理下标（v_0² -> v<sub>0</sub>²），
    #      放最后是因为它会插入标签，避免前面的规则再动这些标签。
    for pat, rep in _ALIAS:
        s = pat.sub(rep, s)
    s = _WORDS_RE.sub(_do_word, s)
    s = _RE_DELTA_PREFIX.sub("\u0394", s)   # 手写的 δv / δφ → Δv / Δφ
    s = _RE_HALF.sub("\u00bd", s)           # 0.5* → ½（只认公式系数形态）
    s = _RE_SQRT.sub("√(", s)
    s = _RE_MUL.sub("·", s)
    s = _RE_SUP_NUM.sub(_do_sup_num, s)
    s = _RE_SUP_PAREN.sub(lambda m: "<sup>%s</sup>" % m.group(1), s)
    s = _RE_SUP_VAR.sub(_do_sup_var, s)
    s = _RE_BOLD.sub(r"<strong>\1</strong>", s)
    # 先按卡片语义处理，平抛、初级、并联等与末态分开。
    s = re.sub(r'(?<![A-Za-z0-9_])([A-Za-z]_?[0-9]*_p)(?![A-Za-z0-9_])',
               lambda m: _p_display(m[1], point_id) or m[1], s)
    # ★ 平排「字母+数字」名 → 教材下标（h1→h₁、Ek2→E_k2；m 系不转，
    #   见 _RE_PROSE_SUB 的注释——正文里 m2 多数是"平方米"）
    s = _RE_PROSE_SUB.sub(_do_prose_sub, s)
    # 下标要**跑多轮**：记号化以后 `R_1R_2` 第一轮只能吃到 `R_1`
    # （后视断言会挡住紧跟在数字后面的 `R_2`），第二轮才算得完。
    # 最多三轮兜底；没有下划线残留时自然停住，是幂等的。
    for _ in range(3):
        new = _RE_SUB.sub(_do_sub, s)
        if new == s:
            break
        s = new

    return s


# ============================================================
# 四、数学标记的「教材写法」还原（给人看的那一层）
# ============================================================
# 起因（主人 2026-09-19 的原话）：
#   「实际学习中没人会把 v平均 写成 vavg 的，都是 v 上面加个横线……
#     给人看的那部分，符号重复或者说下标用中文，都比现在好」
#
# 问题在哪：**符号名是给机器用的**（要能进表达式、能查表），所以作者写成了
#   v_avg / T_half / m_before / F_net 这种英文缩写。
#   显示层把这些缩写原样当下标印出来，页面上就成了程序员的记号，不是教材的记号。
#
# 做法：**名字一个字都不改**（改了就要重跑全部 1321 项校验，风险极大），
#   只在「渲染成 MathML」这一步把下标换成人话。
#   → 显示与校验彻底解耦：机器认识 v_avg，人看到 v̄。
#
# 三类处理：
#   1) 上加横线：avg → v̄（教材里"平均值"的标准写法）
#   2) 中文下标：total→总 / half→1/2 / before→前 / after→后 …
#   3) 保持原样：max / min / rms / AB 这类教材本来就这么写

# ⚠ 词表（_OVERLINE_SUBS / _SUB_DROP / _SUB_CN / _SUB_KEEP / _SUB_LEX）
#   已经上移到「一、词表」那一节，因为下标记号正则在导入时就要用它。
#   两条路径（正文 HTML 与公式框 MathML）共用同一份词表，口径才能保证一致。

# physkit 的 _mi 对「长度 > 1 的名字」会给正体，样子是：
#     <msub><mi>v</mi><mi mathvariant="normal">avg</mi></msub>
#
# ★ 2026-09-29：把匹配放宽成「任意 msub + 两个 mi」。
#   旧正则要求下标**必须带 mathvariant="normal" 且至少两个 ASCII 字母**，
#   于是 `Delta_Phi`（physkit 给的是 <msub><mi>Δ</mi><mi>Φ</mi></msub>）
#   整条漏掉 —— 公式框里就一直印着 Δ_Φ，Φ 被压成角标。
#   单字母下标（E_n、F_N）会走 _do_sub_display 的"保持原样"分支，不受影响。
_SUB_PAT = re.compile(
    r'<msub><mi(?: mathvariant="normal")?>([^<]+)</mi>'
    r'<mi(?: mathvariant="normal")?>([^<]+)</mi></msub>')

# 中文下标（physkit 会给单字加斜体 —— 中文用斜体不合适，这里改成正体）
_SUB_CN_PAT = re.compile(r'<msub><mi>([^<]+)</mi><mi>([\u4e00-\u9fff]+)</mi></msub>')

# ★★ 比"缩写"更严重的一类：**大小写**（2026-09-19 排查时发现）
#
#   `delta_r` 会被渲染成 **δ**_r，而它的含义是「两列波到达某点的路程差」——
#   应当是 **Δ**r。δ（小写）与 Δ（大写）在物理里**是两个不同的符号**，
#   这不是风格问题，是记号错了。
#
#   实测：全库有 **16 处 `delta_*`** 与 **16 处 `Delta_*`**，
#   而 16 处小写的**含义全部是「变化量 / 增量 / 差」**，没有一处是微小量 δ。
#   → 也就是说这是五五开的**混乱**，不是有意的区分。（成品里 δ 出现 35 处。）
#
#   处置：显示层把作为符号基底的小写 δ 还原成 Δ。
#   ⚠ 这是一个**语义假设**：「δ 在本库里一律表示变化量」。已在成品里逐条核对，
#     16/16 成立。日后若真有章节要用小写 δ 表示微小量，从这条规则里排除即可。
_RE_DELTA = re.compile(r'<mi>\u03b4</mi>')


def _do_sub_display(m):
    base, sub = m.group(1), m.group(2)
    mapped = _p_display(base + '_' + sub, mathml=True)
    if mapped:
        return mapped
    # 老卡片的 S_dot 是面积速度，新卡片的 T_dot 是打点周期，不共用含义。
    if base == "T" and sub == "dot":
        return '<msub><mi>T</mi><mi mathvariant="normal">打点</mi></msub>'
    # 小写 δ 在本库里一律表示「变化量」，与正文那条规则同一口径
    if base == "\u03b4":
        base = "\u0394"

    # ---- Δ / δ 是前缀算子：Δr、Δt、ΔΦ 都是两个符号并排，不是 Δ 带下标 ----
    # （与正文 _do_sub 里的规则完全对称，两条路径口径一致）
    if base == "\u0394":
        if sub.startswith("E_") or (len(sub) == 2 and sub[0] == "E"):
            rest = sub[2:] if sub.startswith("E_") else sub[1:]
            return ('<msub><mi>\u0394E</mi><mi>%s</mi></msub>'
                    % _SUB_CN.get(rest, rest))
        if sub in _SUB_DROP:
            return '<mi>\u0394</mi>'
        cn = _SUB_CN.get(sub)
        if cn:
            return ('<msub><mi>\u0394</mi><mi mathvariant="normal">%s</mi></msub>'
                    % cn)
        if len(sub) <= 2 and not re.search(r"[\u4e00-\u9fff]", sub):
            return '<mi>\u0394</mi><mi>%s</mi>' % sub
        return '<msub><mi>\u0394</mi><mi mathvariant="normal">%s</mi></msub>' % sub

    if sub in _SUB_DROP:
        # 教材里不写下标的：还原成裸符号（E_emf → E，G_const → G）
        return '<mi>%s</mi>' % base
    if sub in _OVERLINE_SUBS:
        # v̄：mover + accent，让横线自动撑满底下的符号
        return '<mover accent="true"><mi>%s</mi><mo>\u00af</mo></mover>' % base
    cn = _SUB_CN.get(sub)
    if cn:
        return ('<msub><mi>%s</mi><mi mathvariant="normal">%s</mi></msub>'
                % (base, cn))
    return m.group(0)


# ------------------------------------------------------------------
# ★★ 2026-09-29 晚（第六批）：公式框 MathML 的「结构化」修补 ★★
#
# 主人看线上页面发现三件事（原话）：
#   1) 重力做功印成 W_G = mgh1 − h2，"h1h2 外面没有括号"；
#   2) 弹力做功印成 0.5kx1² − x2²，"x1 的平方和 x2 的平方外面也没有括号"；
#   3) 动能印成 Ek = 0.5mv²，"0.5 不常用，常用的是二分之一"。
#
# 查证结果：**源数据全是对的**（`m*g*(h1 - h2)`、`0.5*k*(x1^2 - x2^2)`），
# 锅在渲染 —— physkit 生成的 MathML **不输出围栏括号**：和/差因子只是个
# <mrow>，浏览器把 mrow 平铺渲染，括号就"隐形"了。这不只是难看：
# mg(h₁−h₂) 变成 mgh₁−h₂ 是**公式含义被改变**，显示层 bug 里最严重的一类。
# physkit 是冻结基线不能改，所以在这里做「渲染后修补」：
# 把 MathML 解析成树（ElementTree），按结构规则改，再序列化回去。
#
# 树状规则（都不碰 <mn> 里的数值、不碰物理结构，只补"给人看"的记号）：
#   ① 围栏：mrow 的直接子层含 +/−，且与左右邻居靠乘号相连 → 它是
#      「乘法里的和/差因子」，补 <mo>(</mo>…<mo>)</mo>。
#      靠 + − = 相连的位置不补 —— 教材本来就不加（W_总 = Ek2 − Ek1）。
#      ⚠ physkit 的乘号（· 与不可见乘号 ⁢）是**裸文本节点**，挂在前一个
#        元素的 tail 上，不是 <mo> —— 找乘号必须查 Element.tail。
#   ② 乘点清理：紧贴围栏的显式乘点删掉（k·( … ) → k( … )），教材乘法靠紧排。
#   ③ 0.5 → 二分之一：<mn>0.5</mn> → <mfrac><mn>1</mn><mn>2</mn></mfrac>。
#      公式框里的 0.5 全部来自 expr 的系数（0.5*m、0.5*k），没有别的语境；
#      只认数值**恰好**是 0.5 的 <mn>，0.55、1.5 都不受影响。
#
# 树状处理之后还有两条正则规则（见 display_mathml）：
#   ④ <mi mathvariant="normal">h1</mi> → h₁ —— 单字母+数字的平排正体名拆下标。
#      ⚠ MathML 里的 m1/m2 全部是「质量1/质量2」，照转；正文里的 m2 大多
#        是"平方米"，prose 层不转（见 _RE_PROSE_SUB 的注释）。
#   ⑤ <mi mathvariant="normal">Ek2</mi> → E 下标 k2（教材写 E_k2）。
#      全库 expr 标识符普查：E 系只有 Ek/Ep/Ek1/Ek2/Ep1/Ep2 六种形态。
# ------------------------------------------------------------------

_MUL_CHARS = ("\u22c5", "\u2062")    # · 显式乘点 / ⁢ 不可见乘号（physkit 里是裸文本节点）
_ADD_CHARS = ("+", "\u2212")         # + −（这两个才是真正的 <mo> 元素）


def _mathml_fence_half(s):
    """MathML 树状修补：① 乘法因子里的和/差 mrow 补围栏括号；
    ② 删掉紧贴围栏的显式乘点；③ <mn>0.5</mn> → 二分之一分数。
    解析失败就原样返回（回退到纯正则修补，不影响出页）。"""
    try:
        root = ET.fromstring(s)
    except ET.ParseError:
        return s
    changed = [False]

    def first_of(el):
        e = el
        while e is not None and e.tag == "mrow" and len(e):
            e = e[0]
        return e

    def last_of(el):
        e = el
        while e is not None and e.tag == "mrow" and len(e):
            e = e[len(e) - 1]
        return e

    def starts_paren(el):
        e = first_of(el)
        return e is not None and e.tag == "mo" and e.text == "("

    def ends_paren(el):
        e = last_of(el)
        return e is not None and e.tag == "mo" and e.text == ")"

    def is_numberish(el):
        e = first_of(el)
        return e is not None and e.tag == "mn"

    def has_top_add(row):
        """row 的直接子层是否为和/差（直接子里有 <mo>+ 或 −</mo>）。"""
        return any(ch.tag == "mo" and ch.text in _ADD_CHARS for ch in row)

    def is_mul_join(prev_el, mid_el, next_el):
        """prev 与 next 之间靠乘号相连吗？乘号两种形态都认：
        裸文本节点（挂在 prev.tail 或 mid_el.tail 上）与 <mo> 元素。"""
        if prev_el is not None:
            if prev_el.tail is not None and prev_el.tail.strip() in _MUL_CHARS:
                return True
            if prev_el.tag == "mo" and prev_el.text in _MUL_CHARS:
                return True
        if mid_el is not None:
            if mid_el.tail is not None and mid_el.tail.strip() in _MUL_CHARS:
                return True
        if next_el is not None:
            if next_el.tag == "mo" and next_el.text in _MUL_CHARS:
                return True
        return False

    def walk(node):
        for ch in node:
            walk(ch)
        cs = list(node)
        # ① 围栏：乘法因子位置上的和/差 mrow
        for i, ch in enumerate(cs):
            if ch.tag != "mrow" or not has_top_add(ch):
                continue
            left = cs[i - 1] if i > 0 else None
            right = cs[i + 1] if i + 1 < len(cs) else None
            if not is_mul_join(left, ch, right):
                continue                      # 靠 + − = 连接的位置不补括号
            if len(ch) >= 2 and starts_paren(ch) and ends_paren(ch):
                continue                      # 已有围栏，不重复加
            o = ET.Element("mo"); o.text = "("
            c = ET.Element("mo"); c.text = ")"
            ch.insert(0, o)
            ch.append(c)
            changed[0] = True
        # ② 乘点清理：k·( … ) → k( … )、( … )·v → ( … )v
        #   （只删 U+22C5 显式点，⁢ 本来就不可见；括号后紧跟数字的保留，
        #     免得 (a+b)·2 变成 (a+b)2 读不开。）
        cs = list(node)
        for i, ch in enumerate(cs):
            if not ch.tail or "\u22c5" not in ch.tail:
                continue
            nxt = cs[i + 1] if i + 1 < len(cs) else None
            if nxt is None:
                continue
            if starts_paren(nxt) or (ends_paren(ch) and not is_numberish(nxt)):
                ch.tail = ch.tail.replace("\u22c5", "")
                changed[0] = True

    walk(root)

    # ③ 0.5 → ½（先收集再改，避免迭代中动树）
    halves = [el for el in root.iter("mn") if el.text == "0.5"]
    for el in halves:
        el.tag = "mfrac"
        el.text = None
        one = ET.SubElement(el, "mn"); one.text = "1"
        two = ET.SubElement(el, "mn"); two.text = "2"

    if not changed[0] and not halves:
        return s                              # 什么都没改 → 原串返回，旧路径逐字节一致
    return ET.tostring(root, encoding="unicode")


# ④ 单字母+数字的平排正体名 → 下标（h1 → h₁、T0 → T₀、m1 → m₁ …）。
#    全库 expr 标识符普查共 38 种，全部是「同一物理量的第几个/初末态」。
#    (?<!<msub>) 防止命中 msub 的**底数**位置（底数若是多字符名，拆了会嵌套）。
_RE_MI_NUM = re.compile(
    r'(?<!<msub>)<mi mathvariant="normal">([A-Za-z])([0-9]+)</mi>')

# ⑤ Ek / Ep / Ek1 / Ek2 / Ep1 / Ep2 → E 下标（教材写 E_k、E_k2）。
#    同样跳过「作为别的符号底数」的位置：Ep_s 的底是 Ep，不能拆，
#    否则会嵌套出双层下标（E 的下标 p，p 再带下标 s）。
_RE_MI_EKP = re.compile(
    r'(?<!<msub>)<mi mathvariant="normal">E([kp][0-9]*)</mi>')


def display_mathml(text, point_id=None):
    """把 physkit 生成的 MathML 里的「机器记号」换成人看的教材写法。

    ⚠ 只动**记号怎么写**：不改数值、不改结构、不改符号顺序。
      查不到映射的原样返回。
    ★ 第六批起先做一遍树状修补（补围栏括号 / 0.5→½ 分数），
      再做正则修补（下标改名 / δ→Δ / 平排名字拆下标）。
    """
    if not text:
        return text
    s = _mathml_fence_half(text)               # ★ 树状：围栏 + 0.5→½
    with symbol_context(point_id if point_id is not None else _POINT.get()):
        s = _SUB_PAT.sub(_do_sub_display, s)
    s = _SUB_CN_PAT.sub(
        lambda m: '<msub><mi>%s</mi><mi mathvariant="normal">%s</mi></msub>'
                  % (m.group(1), m.group(2)), s)
    s = _RE_DELTA.sub('<mi>\u0394</mi>', s)
    s = _RE_MI_NUM.sub(r'<msub><mi>\1</mi><mn>\2</mn></msub>', s)    # ★ h1→h₁
    s = _RE_MI_EKP.sub(
        r'<msub><mi>E</mi><mi mathvariant="normal">\1</mi></msub>', s)  # ★ Ek→E_k
    return s


# ============================================================
# 五、自测
# ============================================================

_CASES = [
    # (输入, 期望输出)
    ("T = 2pi sqrt(L/g)", "T = 2π √(L/g)"),
    ("x = A cos(omega t + phi)", "x = A cos(ω t + φ)"),
    ("F_合 = m a", "F<sub>合</sub> = m a"),
    ("E = k A^2/2", "E = k A²/2"),
    ("f = μ F_N", "f = μ F<sub>N</sub>"),
    ("x = v_0*t + a*t^2/2", "x = v<sub>0</sub>·t + a·t²/2"),
    ("v=wavelength/T", "v=λ/T"),
    ("omega*T=2pi", "ω·T=2π"),
    ("T_orbit = 2π√(r³/GM)", "T<sub>轨</sub> = 2π√(r³/GM)"),
    ("Q = f s_rel", "Q = f s<sub>相对</sub>"),
    ("v_min = √(gr)", "v<sub>min</sub> = √(gr)"),
    ("sqrt(v_0^2 + v^2)", "√(v<sub>0</sub>² + v²)"),
    ("v = √(2GM/r)", "v = √(2GM/r)"),          # 已是 Unicode，幂等
    ("a = g sin(theta) = 3.6 m/s²", "a = g sin(θ) = 3.6 m/s²"),
    ("Ep_s = 1/2 kx²", "Ep<sub>s</sub> = 1/2 kx²"),
    # ★ 小写 delta 一律还原成 Δ；且 Δ 是前缀算子，与后面的符号**并排**写
    ("delta_r=n lambda", "Δr=n λ"),
    ("delta_Ek = 4 J", "ΔE<sub>k</sub> = 4 J"),  # ★ ΔE_k（Δ 挂在 E 上，不是 Δ 的下标是 Ek）
    ("W_G = m*g*(h2-h1)", "W<sub>G</sub> = m·g·(h<sub>2</sub>-h<sub>1</sub>)"),
    ("spin 里不该替换", "spin 里不该替换"),      # 英文单词里的 pi 不动
    ("f = mu*F_N", "f = μ·F<sub>N</sub>"),
    ("A_dot = rv_t/2", "A<sub>面积</sub> = rv<sub>t</sub>/2"),
    # HTML 转义
    ("a < b & c > d", "a &lt; b &amp; c &gt; d"),
    # ---- 内部代号还原（2026-09-19 加，符号表单位与检查说明都走这条）----
    ("T_", "T"),                            # 特斯拉：内部键 T_ 不该给读者看到
    ("ohm", "Ω"),                           # 欧姆
    ("ohm*m", "Ω\u00b7m"),                  # 电阻率
    ("kg*m/s^2", "kg\u00b7m/s\u00b2"),      # 组合单位
    ("B (T_)", "B (T)"),
    ("'T_'", "'T'"),
    # ★★ 反向保护：绝不能被误伤的
    #    正文里的 T_0 是「周期 T₀」，不是特斯拉。改错就是制造新 bug。
    ("f_0 = 1/T_0", "f<sub>0</sub> = 1/T<sub>0</sub>"),
    ("T_0^2 = 4pi^2 L/g", "T<sub>0</sub>\u00b2 = 4\u03c0\u00b2 L/g"),
    # ---- 正文里的缩写下标还原（2026-09-19 晚补：之前只修了公式框那条路径）----
    ("v_avg = x/t", '<span style="text-decoration:overline">v</span> = x/t'),
    ("T_half = 5 s", "T<sub>1/2</sub> = 5 s"),
    ("F_net = m a", "F<sub>合</sub> = m a"),
    ("E_ind = q/t", "E<sub>感</sub> = q/t"),
    ("R_series = 9000 ohm", "R<sub>串</sub> = 9000 Ω"),
    ("q_test = 1e-6 C", "q<sub>试探</sub> = 1e-6 C"),
    ("E_emf = 1.5 V", "E = 1.5 V"),          # ★ 教材不写这个下标，直接去掉
    ("G_const = 6.67e-11", "G = 6.67e-11"),
    ("Q_absorb = 5 J", "Q<sub>吸</sub> = 5 J"),
    ("F_restore = -kx", "F<sub>回复</sub> = -kx"),
    # ---- 希腊字母下标 + 手写 δ 前缀（2026-09-19 晚补的两处漏网）----
    ("delta_phi = 0", "Δφ = 0"),                 # 下标是希腊字母，以前不进 <sub>
    ("delta_v = v - v_0", "Δv = v - v<sub>0</sub>"),
    ("速度变化量 δv = 3 m/s", "速度变化量 Δv = 3 m/s"),   # 手写的前缀形式
    ("两点之差 δφ = 0", "两点之差 Δφ = 0"),
    # ---- ★★ 2026-09-29：下标不再吞掉相邻的正常字母 ----
    ("F = k_e|q_1q_2|/r", "F = k<sub>e</sub>|q<sub>1</sub>q<sub>2</sub>|/r"),
    ("B_net=B_1B_2", "B<sub>合</sub>=B<sub>1</sub>B<sub>2</sub>"),
    ("R_s=R_1R_2", "R<sub>s</sub>=R<sub>1</sub>R<sub>2</sub>"),
    ("I=2nq_0v_dS", "I=2nq<sub>0</sub>v<sub>d</sub>S"),
    ("U_V 大于表头满偏电压 I_gR_g", "U<sub>V</sub> 大于表头满偏电压 I<sub>g</sub>R<sub>g</sub>"),
    ("p = 2Nk_BT/V", "p = 2Nk<sub>B</sub>T/V"),
    ("E = k_eQ/r²", "E = k<sub>e</sub>Q/r²"),
    # ---- ★ 2026-09-29：补进词表的机器名 ----
    ("E = h*nu", "E = h·ν"),                     # 频率 ν
    ("Delta_Phi = Phi_2 - Phi_1", "ΔΦ = Φ<sub>2</sub> - Φ<sub>1</sub>"),   # 磁通量 Φ
    ("E_n = E_1/n_level^2", "E<sub>n</sub> = E<sub>1</sub>/n²"),            # 主量子数 n
    ("lambda_db = h/p", "λ = h/p"),                                        # 德布罗意波长
    ("Q_water = c_water*m_water", "Q<sub>水</sub> = c<sub>水</sub>·m<sub>水</sub>"),
    ("E_mech_before = E_mech_after+E_int_gain",
     "E<sub>机前</sub> = E<sub>机后</sub>+E<sub>内增</sub>"),                 # 复合下标
    ("delta_E_total = E_after - E_before", "ΔE<sub>总</sub> = E<sub>后</sub> - E<sub>前</sub>"),
    ("v_x2 = sqrt((v_0^2 + v^2)/2)", "v<sub>x/2</sub> = √((v<sub>0</sub>² + v²)/2)"),
    ("m_kg = m_g/1000", "m<sub>kg</sub> = m<sub>g</sub>/1000"),              # 单位下标原样保留
    # ---- ★ 乘号 / 分数指数 / markdown 加粗 ----
    ("T = 2*pi*sqrt(L/g)", "T = 2·π·√(L/g)"),   # 以前第二个 * 变不成 ·
    ("R = r_0·A^(1/3)", "R = r<sub>0</sub>·A<sup>1/3</sup>"),
    ("宏观物体**有**波动性", "宏观物体<strong>有</strong>波动性"),
    # 不应被误伤的（单字母 / 数字 / 中文 / 教材标准缩写）
    ("E_k = 9 J", "E<sub>k</sub> = 9 J"),
    ("v_0 = 2 m/s", "v<sub>0</sub> = 2 m/s"),
    ("F_合 = 6 N", "F<sub>合</sub> = 6 N"),
    ("W_AB = 3 J", "W<sub>AB</sub> = 3 J"),
    ("v_max = 8 m/s", "v<sub>max</sub> = 8 m/s"),
    # ---- ★★ 2026-09-29 晚（第六批）：0.5→½ 与 平排名下标 ----
    ("Ek = 0.5*m*v^2", "E<sub>k</sub> = ½m·v²"),
    ("W = 0.5*k*(x1^2 - x2^2)", "W = ½k·(x<sub>1</sub>² - x<sub>2</sub>²)"),
    ("0.5 × 3 = 1.5", "0.5 × 3 = 1.5"),                    # 算式里的 0.5 不动
    ("速率为 0.5 m/s", "速率为 0.5 m/s"),                   # 测量值不动
    ("h1、h2 使用同一高度基准", "h<sub>1</sub>、h<sub>2</sub> 使用同一高度基准"),
    ("Delta_T = T2-T1 = 70 K", "ΔT = T<sub>2</sub>-T<sub>1</sub> = 70 K"),
    ("动能 Ek2 = 0", "动能 E<sub>k2</sub> = 0"),
    ("S 用 m2、F 用 N", "S 用 m2、F 用 N"),                 # ★ m2=平方米，绝不转
    ("质量分别为 m1、m2", "质量分别为 m1、m2"),             # prose 层 m 系整体不转
    ("p1V1=p2V2", "p<sub>1</sub>V<sub>1</sub>=p<sub>2</sub>V<sub>2</sub>"),
]

# MathML 路径的自测：display_mathml(输入) 必须包含/必须不包含
_MML_CASES = [
    # ③ 0.5 → 二分之一分数
    ("<mrow><mn>0.5</mn>\u2062<mi>k</mi></mrow>",
     "<mfrac><mn>1</mn><mn>2</mn></mfrac>", "<mn>0.5</mn>"),
    # 0.55 / 2.5 这类不是 0.5 的数，不动
    ("<mrow><mn>0.55</mn><mi>k</mi></mrow>", "<mn>0.55</mn>", "<mfrac>"),
    # ① 围栏：mg(h₁−h₂) —— 乘法因子里的和差 mrow 必须补括号
    ("<mrow><mrow><mi>m</mi>\u22c5<mi>g</mi></mrow>\u22c5"
     "<mrow><msub><mi>h</mi><mn>1</mn></msub><mo>\u2212</mo>"
     "<msub><mi>h</mi><mn>2</mn></msub></mrow></mrow>",
     "<mo>(</mo>", ""),
    # ① 反例：W = E2 − E1 —— 和差在「= 右侧整项」位置，不补括号
    ("<mrow><mi>W</mi><mo>=</mo>"
     "<mrow><msub><mi>E</mi><mn>2</mn></msub><mo>\u2212</mo>"
     "<msub><mi>E</mi><mn>1</mn></msub></mrow></mrow>",
     "", "<mo>(</mo>"),
    # ④ 平排正体名 x1 → x₁ 下标
    ("<msup><mi mathvariant=\"normal\">x1</mi><mn>2</mn></msup>",
     "<msub><mi>x</mi><mn>1</mn></msub>", "x1"),
    # ⑤ Ek2 → E 下标 k2
    ("<mrow><mi mathvariant=\"normal\">Ek2</mi><mo>\u2212</mo>"
     "<mi mathvariant=\"normal\">Ek1</mi></mrow>",
     "<msub><mi>E</mi><mi mathvariant=\"normal\">k2</mi></msub>", ""),
    # ⑤ 反例：Ep_s 的底 Ep 绝不能拆（否则嵌套出双层下标）
    ("<msub><mi mathvariant=\"normal\">Ep</mi><mi>s</mi></msub>",
     "", "<msub><msub"),
]


def _selftest():
    bad = 0
    for src, want in _CASES:
        got = render(src)
        if got != want:
            bad += 1
            print("✘ 输入：%s" % src)
            print("    期望：%s" % want)
            print("    实际：%s" % got)
    for src, must_have, must_not in _MML_CASES:
        got = display_mathml(src)
        ok = (not must_have or must_have in got) and \
             (not must_not or must_not not in got)
        if not ok:
            bad += 1
            print("✘ MML 输入：%s" % src)
            print("    须含：%s  须无：%s" % (must_have, must_not))
            print("    实际：%s" % got)
    context_total = 0
    # 每个实际语义同时测试正文与 MathML，包括已处理 v_p 的非末态反向保护。
    for pid, mapping in P_DISPLAY.items():
        for name, (base, suffix) in mapping.items():
            for is_math in (False, True):
                context_total += 1
                src = name
                if is_math:
                    a,b = name.split('_',1)
                    src = '<msub><mi>%s</mi><mi>%s</mi></msub>' % (a,b)
                got = display_mathml(src,pid) if is_math else render(src,pid)
                want = _p_display(name,pid,is_math)
                if got != want:
                    bad += 1
                    print('✘ 卡片语义：',pid,name,got,want)
    for pid,name in [('dc-05','S_p'),('mdl-22','v_p'),('mdl-13','v1_p')]:
        context_total += 1
        if '′' in render(name,pid):
            bad += 1
            print('✘ 未定义符号被改成一撇：',pid,name)
    total = len(_CASES) + len(_MML_CASES) + context_total
    print("自测：%d / %d 通过" % (total - bad, total))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
