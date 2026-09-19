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
    ("phi", "φ"),
    ("rho", "ρ"),
    ("tau", "τ"),
    ("eta", "η"),
    ("psi", "ψ"),
    ("zeta", "ζ"),
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
_RE_MUL = re.compile(
    r"(?<=[A-Za-z0-9" + _GK + r"\u00b2\u00b3\u2070-\u209f\u4e00-\u9fff\)])"
    r"\s*\*\s*"
    r"(?=[A-Za-z0-9\(" + _GK + r"\u4e00-\u9fff])"
)

# 数字上标：^2 -> ²、^-1 -> ⁻¹
_RE_SUP_NUM = re.compile(r"\^(-?\d+)")
# 单字母上标：^n -> ⁿ（表里没有的退化成 <sup>）
_RE_SUP_VAR = re.compile(r"\^([A-Za-z])")

# 下标：X_abc -> X<sub>abc</sub>
# 基名允许是 ASCII 或希腊字母（ω_drive、δ_r），下标允许数字/字母/中文（F_合）
# ★ 2026-09-19 晚补：**下标也要允许希腊字母**。
#   原先的字符类只写了 [A-Za-z0-9中文]，于是 `delta_phi` 变成 `δ_φ` 之后就卡住了
#   （φ 不在类里）→ 页面上残留 `δ_φ` 这种半机器写法，没有生成真正的下标。
_RE_SUB = re.compile(
    r"(?<![A-Za-z0-9_" + _GK + r"])"
    r"([A-Za-z" + _GK + r"][A-Za-z0-9" + _GK + r"]*)"
    r"_([A-Za-z0-9" + _GK + r"\u4e00-\u9fff]+)"
)

# ★ 作者有时在正文里**直接手写小写 δ**（如「速度的变化量，δv = v − v₀」）。
#   这类「δ 紧跟一个字母」的写法在本库里一律是"变化量"前缀，还原成 Δ。
#   ⚠ 只认「δ 后面紧跟字母」这一种：`δ_φ` 这种带下标的由 _do_sub 处理，
#     而单独一个 δ（后面是空格或中文）不动 —— 万一将来要表示微小量 δ。
_RE_DELTA_PREFIX = re.compile(r"\u03b4(?=[A-Za-z" + _GK + r"])")


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
    # 小写 δ 在本库里一律表示「变化量」，还原成 Δ（与 MathML 那条规则同一口径）
    if base == "\u03b4":
        base = "\u0394"
    if sub in _SUB_DROP:
        return base                                   # E_emf → E、G_const → G
    if sub in _OVERLINE_SUBS:
        # 正文里用行内样式画横线：与公式框的 MathML mover 视觉上一致
        return '<span style="text-decoration:overline">%s</span>' % base
    if base == "\u0394" and len(sub) == 2 and sub[0] == "E":
        return "\u0394E<sub>%s</sub>" % sub[1]        # delta_Ek → ΔE<sub>k</sub>
    cn = _SUB_CN.get(sub)
    return "%s<sub>%s</sub>" % (base, cn if cn else sub)


# ============================================================
# 三、对外接口
# ============================================================

def render(text):
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
    s = _RE_SQRT.sub("√(", s)
    s = _RE_MUL.sub("·", s)
    s = _RE_SUP_NUM.sub(_do_sup_num, s)
    s = _RE_SUP_VAR.sub(_do_sup_var, s)
    s = _RE_SUB.sub(_do_sub, s)

    return s


# ============================================================
# 四、数学标记的「教材写法」还原（给人看的那一层）
# ============================================================
# 起因（需求方 2026-09-19 的原话）：
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

# 改成「上加横线」的下标
_OVERLINE_SUBS = {"avg"}

# 改成中文的下标（键是内容里的内部写法，值是给人看的写法）
_SUB_CN = {
    "total": "总", "tot": "总",
    "ind": "感", "induced": "感",
    "net": "合",
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
    "field": "场", "outer": "外", "inner": "内",
    # ---- 第二批：把「剩余缩写」逐个按实际含义定下来（2026-09-19 晚）----
    # 依据是每个符号在内容里的 desc（先用脚本把 34 种全列出来看过一遍再定的），
    # 不是照单词硬译。
    "absorb": "吸", "release": "放",          # Q_absorb 低温物体吸热 / Q_release 高温物体放热
    "latent": "潜", "sensible": "显热",        # 潜热 / 显热
    "attr": "引", "rep": "斥", "mag": "安",    # 引力 / 斥力 / 安培阻力
    "restore": "回复", "spring": "弹",         # 回复力 / 弹力做功
    "common": "共", "rel": "相对", "rate": "速率",
    "decay": "衰变", "molecular": "分子",
    "series": "串", "shunt": "并",             # 串联分压电阻 / 并联分流电阻
    "test": "试探", "order": "级",             # 试探电荷 / 条纹级次
    "sat": "饱和", "dot": "面积",              # 饱和汽压 / 面积速度
    "perp": "有效", "sep": "分离", "oi": "物像",
    "top": "顶", "far": "远", "near": "近", "inside": "内", "push": "拉",
    # ★ 以下这些**故意不改**（教材本来就这么写）：
    #   AB / BA —— F_AB 就是「A 对 B 的力」，这是标准写法
    #   max / min —— 教材标准
    #   Ek / Ep —— 见下面 _RE_DELTA_E 的注释，它们的问题不在缩写而在下标层次
}

# ★ 教材里**根本不写下标**的那些：直接还原成裸符号。
#   例：电源电动势教材就写 E（不写 E_emf）；万有引力常量就写 G（不写 G_const）。
#   ⚠ 这个集合要手工确认过才加 —— 去掉下标会让符号"变短"，
#     万一同一章里另有一个真叫 G 的符号就会撞名。目前这两个都确认无冲突。
_SUB_DROP = {"emf", "const"}

# physkit 的 _mi 对「长度 > 1 的名字」会给正体，样子是：
#     <msub><mi>v</mi><mi mathvariant="normal">avg</mi></msub>
_SUB_PAT = re.compile(
    r'<msub><mi>([^<]+)</mi><mi mathvariant="normal">([A-Za-z]{2,})</mi></msub>')

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
    if sub in _SUB_DROP:
        # 教材里不写下标的：还原成裸符号（E_emf → E，G_const → G）
        return '<mi>%s</mi>' % base
    if sub in _OVERLINE_SUBS:
        # v̄：mover + accent，让横线自动撑满底下的符号
        return '<mover accent="true"><mi>%s</mi><mo>\u00af</mo></mover>' % base
    # ΔE_k 这个特例：名字 `delta_Ek` 渲染出来是 Δ_Ek，但教材写的是 **ΔE_k**
    # ——「Δ 挂在 E 上、k 才是下标」，不是「Δ 的下标是 Ek」。
    # 根源是命名系统只支持一层下标（`delta_Ek` 拆成 base=delta、sub=Ek），
    # 所以只能在显示这一步把它掰回教材的样子。
    if base == "\u03b4" and len(sub) == 2 and sub[0] == "E":
        return ('<msub><mi>\u0394E</mi><mi>%s</mi></msub>' % sub[1])
    cn = _SUB_CN.get(sub)
    if cn:
        return ('<msub><mi>%s</mi><mi mathvariant="normal">%s</mi></msub>'
                % (base, cn))
    return m.group(0)


def display_mathml(text):
    """把 physkit 生成的 MathML 里的「机器下标」换成人看的写法。

    ⚠ 只动**下标的名字怎么写**：不改数值、不改结构、不改符号顺序，
      也不会碰到 <mn> 里的数字。查不到映射的原样返回。
    """
    if not text:
        return text
    s = _SUB_PAT.sub(_do_sub_display, text)
    s = _SUB_CN_PAT.sub(
        lambda m: '<msub><mi>%s</mi><mi mathvariant="normal">%s</mi></msub>'
                  % (m.group(1), m.group(2)), s)
    s = _RE_DELTA.sub('<mi>\u0394</mi>', s)
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
    ("delta_r=n lambda", "Δ<sub>r</sub>=n λ"),   # ★ 小写 delta 一律还原成 Δ
    ("delta_Ek = 4 J", "ΔE<sub>k</sub> = 4 J"),  # ★ ΔE_k（Δ 挂在 E 上，不是 Δ 的下标是 Ek）
    ("W_G = m*g*(h2-h1)", "W<sub>G</sub> = m·g·(h2-h1)"),
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
    ("delta_phi = 0", "Δ<sub>φ</sub> = 0"),      # 下标是希腊字母，以前不进 <sub>
    ("delta_v = v - v_0", "Δ<sub>v</sub> = v - v<sub>0</sub>"),
    ("速度变化量 δv = 3 m/s", "速度变化量 Δv = 3 m/s"),   # 手写的前缀形式
    ("两点之差 δφ = 0", "两点之差 Δφ = 0"),
    # 不应被误伤的（单字母 / 数字 / 中文 / 教材标准缩写）
    ("E_k = 9 J", "E<sub>k</sub> = 9 J"),
    ("v_0 = 2 m/s", "v<sub>0</sub> = 2 m/s"),
    ("F_合 = 6 N", "F<sub>合</sub> = 6 N"),
    ("W_AB = 3 J", "W<sub>AB</sub> = 3 J"),
    ("v_max = 8 m/s", "v<sub>max</sub> = 8 m/s"),
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
    print("自测：%d / %d 通过" % (len(_CASES) - bad, len(_CASES)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
