# -*- coding: utf-8 -*-
"""
quantity.py —— 物理量（数值 + 量纲）
====================================

【这个文件解决什么问题】

要让机器判断一条物理公式"写对了没有"，最有力的武器是**量纲**。

任何物理量都可以拆成 7 个基本量的指数组合：

    M 质量   L 长度   T 时间   I 电流   K 温度   N 物质的量   J 发光强度

例如：
    速度   = L / T          →  L 指数 1，T 指数 -1
    力     = M·L / T²       →  M 1，L 1，T -2
    能量   = M·L² / T²      →  M 1，L 2，T -2

两个物理量只要 7 个指数全部相同，就是同一类量，可以相加减。
公式左右两边的量纲必须完全一样 —— 不一样，公式一定是错的。

【关键设计：Quantity 允许「只知道量纲、不知道数值」】

生成内容时，很多时候我们只想先查量纲（不需要给具体数字）。
所以 Quantity 的 value 可以是 None，表示"符号量"。
- 查量纲：只需要量纲，数值全部设成 None 也能算
- 查数值 / 查变化方向：必须把用到的变量都填上具体数字

这样做的好处是：**同一份公式，不做任何改动，就能同时用于三种检查。**

作者备注：本文件不依赖任何第三方库，只用 Python 自带的 math。
"""

import math

# 相对误差容忍度。浮点运算会有极小的误差，比较时用这个阈值兜底。
EPS = 1e-9


# ============================================================
# 一、量纲（Dim）—— 7 个基本量的指数组合
# ============================================================

class Dim:
    """一个量纲。内部就是 7 个基本量的指数，例如 {'M':1, 'L':1, 'T':-2}。"""

    # 7 个基本量的固定顺序。做成固定顺序是为了打印时输出稳定，方便肉眼比对。
    BASES = ("M", "L", "T", "I", "K", "N", "J")

    # 基本量代号 → 中文名，报错时用来说人话
    BASE_NAMES = {
        "M": "质量", "L": "长度", "T": "时间", "I": "电流",
        "K": "温度", "N": "物质的量", "J": "发光强度",
    }

    __slots__ = ("e",)

    def __init__(self, **exps):
        # 指数为 0 的项直接丢掉，这样比较和打印都干净（L^1 · T^0 就等于 L）
        self.e = {k: float(v) for k, v in exps.items() if abs(v) > 1e-12}

    # ---------- 乘除：指数相加减 ----------
    def __mul__(self, other):
        out = dict(self.e)
        for k, v in other.e.items():
            out[k] = out.get(k, 0.0) + v
        return Dim(**out)

    def __truediv__(self, other):
        out = dict(self.e)
        for k, v in other.e.items():
            out[k] = out.get(k, 0.0) - v
        return Dim(**out)

    def __pow__(self, power):
        # 开平方就是 0.5 次方，所以指数可以直接乘
        return Dim(**{k: v * power for k, v in self.e.items()})

    # ---------- 加减：只有同类量才能相加减 ----------
    def __add__(self, other):
        if self != other:
            raise DimError("量纲不同的量不能相加减：%s 与 %s" % (self, other))
        return self

    __sub__ = __add__
    __radd__ = __add__

    # ---------- 相等判断 ----------
    def __eq__(self, other):
        if not isinstance(other, Dim):
            return NotImplemented
        keys = set(self.e) | set(other.e)
        return all(abs(self.e.get(k, 0.0) - other.e.get(k, 0.0)) < EPS for k in keys)

    def __hash__(self):
        return hash(tuple(sorted(self.e.items())))

    # ---------- 打印成人能看的样子 ----------
    def __repr__(self):
        if not self.e:
            return "无量纲"
        parts = []
        for k in self.BASES:
            if k in self.e:
                v = self.e[k]
                parts.append(k if abs(v - 1) < 1e-12 else "%s^%g" % (k, v))
        return "·".join(parts)

    def diff(self, other):
        """把"我比对方多了什么、少了什么"讲清楚，用于定位错误。"""
        parts = []
        for k in self.BASES:
            d = self.e.get(k, 0.0) - other.e.get(k, 0.0)
            if abs(d) > EPS:
                who = self.BASE_NAMES.get(k, k)
                parts.append("%s%s^%g" % (who, k, d))
        return "、".join(parts) if parts else "无差异"


class DimError(Exception):
    """量纲运算错误（例如把量纲不同的两个量相加）。"""
    pass


# ============================================================
# 二、物理量（Quantity）—— 数值 + 量纲
# ============================================================

class Quantity:
    """
    一个物理量。包含两部分：
        value —— 数值，可以是 None（表示"只知道量纲，数值待定"）
        dim   —— 量纲

    支持四则运算和幂运算，运算时量纲自动跟着走。
    只要参与运算的任何一方数值是 None，结果的数值就是 None —— 这样
    "只查量纲"和"查数值"可以共用同一套表达式。
    """

    __slots__ = ("value", "dim")

    def __init__(self, value, dim):
        self.value = value
        self.dim = dim

    # ---------- 工厂方法：写起来短一点 ----------
    @staticmethod
    def plain(value):
        """无量纲的数（纯数字）。"""
        return Quantity(value, Dim())

    # ---------- 加减：量纲必须一致 ----------
    def __add__(self, other):
        self.dim + other.dim          # 量纲不一致会在这里抛异常
        return Quantity(_vadd(self.value, other.value), self.dim)

    __radd__ = __add__

    def __sub__(self, other):
        # 减法就是"加上相反数"。量纲同样必须一致。
        self.dim + other.dim
        return Quantity(_vadd(self.value, _vneg(other.value)), self.dim)

    def __neg__(self):
        return Quantity(_vneg(self.value), self.dim)

    def __rsub__(self, other):
        # other - self
        return other.__add__(self.__neg__())

    # ---------- 乘除：量纲相乘除 ----------
    def __mul__(self, other):
        return Quantity(_vmul(self.value, other.value), self.dim * other.dim)

    __rmul__ = __mul__

    def __truediv__(self, other):
        return Quantity(_vdiv(self.value, other.value), self.dim / other.dim)

    def __rtruediv__(self, other):
        return other.__truediv__(self)

    # ---------- 乘方 ----------
    def __pow__(self, power):
        # power 可以是数字，也可以是 Quantity（但必须无量纲、且数值已知）
        if isinstance(power, Quantity):
            if power.dim != Dim():
                raise DimError("指数必须是无量纲的，实际是 %s" % power.dim)
            p = power.value
            if p is None:
                # 数值未知时，只有整数指数才能确定量纲
                return _pow_symbolic(self, power)
            return Quantity(_vpow(self.value, p), self.dim ** p)
        return Quantity(_vpow(self.value, power), self.dim ** power)

    # ---------- 开平方（写成函数是因为它比 **0.5 更可读） ----------
    def sqrt(self):
        return Quantity(_vsqrt(self.value), self.dim ** 0.5)

    def __repr__(self):
        if self.value is None:
            return "<%s>" % self.dim
        return "%.6g [%s]" % (self.value, self.dim)


# --- 数值运算的小工具：统一处理 value 为 None 的情况 ---

def _vadd(a, b):
    if a is None or b is None:
        return None
    return a + b


def _vneg(a):
    """取相反数。数值未知时仍然是"数值未知"，不能当成 0 处理。"""
    if a is None:
        return None
    return -a


def _vmul(a, b):
    if a is None or b is None:
        return None
    return a * b


def _vdiv(a, b):
    if a is None or b is None:
        return None
    if abs(b) < 1e-300:
        raise ZeroDivisionError("除以零")
    return a / b


def _vpow(a, p):
    if a is None:
        return None
    if a < 0 and abs(p - round(p)) > EPS:
        raise ValueError("负数开偶次方：%g 的 %g 次方" % (a, p))
    return a ** p


def _vsqrt(a):
    if a is None:
        return None
    if a < 0:
        raise ValueError("负数开平方：%g" % a)
    return math.sqrt(a)


def _pow_symbolic(base, power_q):
    """
    数值未知时的幂运算。
    只有整数指数可以确定量纲（L^2 可以，L^x 不行）。
    """
    v = power_q.value
    if v is None:
        raise DimError("数值未知时，指数只能是具体的整数")
    if abs(v - round(v)) > EPS:
        raise DimError("数值未知时，指数只能是整数，实际是 %g" % v)
    return Quantity(None, base.dim ** v)


# ============================================================
# 三、单位表 —— 把"m/s²"这样的字符串翻译成量纲
# ============================================================
# 这里的键是"单位符号"，值是它的量纲。
# 只要公式里声明了变量的单位，就能自动检查"单位写得对不对"。

M = Dim(M=1)
L = Dim(L=1)
T = Dim(T=1)
I = Dim(I=1)
K = Dim(K=1)
N_ = Dim(N=1)
J_ = Dim(J=1)
NONE = Dim()          # 无量纲

UNITS = {
    # ---- 7 个基本单位 ----
    "kg": M, "m": L, "s": T, "A": I, "K": K, "mol": N_, "cd": J_,

    # ---- 角度（弧度/度都是无量纲） ----
    "rad": NONE, "°": NONE, "degree": NONE, "度": NONE,

    # ---- 高频导出单位 ----
    "N": M * L / T ** 2,                 # 牛顿
    "J": M * L ** 2 / T ** 2,            # 焦耳
    "W": M * L ** 2 / T ** 3,            # 瓦特
    "Pa": M / L / T ** 2,                # 帕斯卡
    "Hz": Dim(T=-1),                     # 赫兹
    "C": I * T,                          # 库仑
    "V": M * L ** 2 / T ** 3 / I,        # 伏特
    "F": I ** 2 * T ** 4 / M / L ** 2,   # 法拉
    "Ω": M * L ** 2 / T ** 3 / I ** 2,   # 欧姆
    "ohm": M * L ** 2 / T ** 3 / I ** 2,
    "T_": M / T ** 2 / I,                # 特斯拉（内部用 T_ 避免和时间 T 撞名）
    "Wb": M * L ** 2 / T ** 2 / I,       # 韦伯
    "H": M * L ** 2 / T ** 2 / I ** 2,   # 亨利

    # ---- 常用非标准写法 ----
    # 注意：量纲只管"种类"，不管"大小"。克和千克的量纲都是 M，
    # 1000 这个换算系数不影响量纲，所以这里不写进去。
    "g": M,
    "km": L, "cm": L, "mm": L, "dm": L,
    "min": T, "h": T, "ms": T,
    "km/h": L / T, "m/s": L / T,
    "kWh": M * L ** 2 / T ** 2,

    # ---- 微观 / 近代物理用到的单位（2026-09-19 加）----
    #
    # ★ 为什么要单独加这一组（不是随手补，是**机制上必需**）：
    #   高中物理的「波粒二象性与原子结构」和「原子核与核能」两章，
    #   教材一律用 nm / eV / MeV / u 来表达。这些单位原先不在表里，
    #   于是**任何一条用它们标注的公式都过不了量纲检查**。
    #   而 `physkit/**` 属冻结范围、执行方**明令禁止修改** ——
    #   结果是这两章在机制上根本产不出来（不是写不写得好，是写不出来）。
    #
    # ★ 这属于「**开能力**」，不是「松检查」：
    #   它只是让校验器多认识几个单位名。任何一条检查的严格程度都没有变化 ——
    #   写错单位的公式照样会被量纲对不上抓出来。
    #
    # 量纲只记"种类"、不记换算系数，所以：
    #   1 eV = 1.602e-19 J  → 量纲同 J，即 M·L²/T²（keV / MeV 同理）
    #   1 u  = 1.66e-27 kg  → 量纲同 kg，即 M
    #   1 nm = 1e-9 m       → 量纲同 m，即 L
    "nm": L, "μm": L,                    # 纳米、微米（波长常用）
    "eV": M * L ** 2 / T ** 2,           # 电子伏
    "keV": M * L ** 2 / T ** 2,          # 千电子伏
    "MeV": M * L ** 2 / T ** 2,          # 兆电子伏
    "u": M,                              # 原子质量单位

    # ---- 初中物理常用单位（2026-09-23 加）----
    #
    # ★ 为什么要专门加这一组（**和上面 eV/MeV/u 那次是同一个坑**）：
    #   开始做「初中物理（苏科版）」时，质检方把教材附录里的常用单位**逐个实测**，
    #   发现有 18 个校验器不认识。于是任何用它们标注的公式都会判「不认识这个单位」，
    #   而 `physkit/**` 是冻结文件、执行方禁止修改 → **初中第一章就会卡住**。
    #   → 每换一个学段/领域开工前，都要先做一次「该领域高频单位」的可用性体检。
    #
    # ★ 这属于「开能力」，不是「松检查」：只是让校验器多认识几个单位名，
    #   任何一条检查的严格程度都没变 —— 写错单位照样会被量纲对不上抓出来。
    #
    # 量纲只记「种类」不记换算系数：
    #   1 L = 1 dm³ = 1e-3 m³  → L³ ；1 mL = 1 cm³ → L³
    #   1 t = 1000 kg / 1 mg = 1e-6 kg → M
    #   1 kPa = 1000 Pa → M·L⁻¹·T⁻² ；1 kJ = 1000 J ；1 kW = 1000 W
    #   1 kW·h（「度」）= 3.6e6 J → 量纲同 J
    #   1 mA = 1e-3 A → I ；1 mV = 1e-3 V ；1 kΩ = 1000 Ω
    #   1 ℃ 与 1 K 只差一个常数，量纲都是 K
    #   dB（分贝）是**两个同类量之比的常用对数**，本身无量纲
    "L": L ** 3, "mL": L ** 3,           # 升、毫升（体积）
    "mg": M, "t": M,                     # 毫克、吨（质量）
    "um": L,                             # 微米（ASCII 写法；μm 上面已有）
    "kPa": M / L / T ** 2,               # 千帕（气压）
    "kJ": M * L ** 2 / T ** 2,           # 千焦（热量）
    "kW": M * L ** 2 / T ** 3,           # 千瓦（功率）
    "kW*h": M * L ** 2 / T ** 2,         # 千瓦时，即「度」（电能）
    "kWh": M * L ** 2 / T ** 2,          # 同上，不带星号的常见写法
    "mA": I, "mV": M * L ** 2 / T ** 3 / I,   # 毫安、毫伏（电表量程常见）
    "kohm": M * L ** 2 / T ** 3 / I ** 2,     # 千欧（ASCII 写法）
    "kΩ": M * L ** 2 / T ** 3 / I ** 2,       # 千欧
    "Mohm": M * L ** 2 / T ** 3 / I ** 2,     # 兆欧
    "MΩ": M * L ** 2 / T ** 3 / I ** 2,       # 兆欧
    "dB": NONE,                          # 分贝（声强级，无量纲）
    "℃": K,                              # 摄氏度（量纲同开尔文）
}

# 上面 "g" 写成克，但物理里 g 也常作重力加速度。这里只在**单位字符串**里解释，
# 所以不影响公式中的变量名解析。


class UnitError(Exception):
    """单位解析错误。"""
    pass


def parse_unit(text):
    """
    把单位字符串解析成量纲。

    支持的写法：
        "m/s"          → L·T^-1
        "m/s^2"        → L·T^-2
        "kg*m/s^2"     → M·L·T^-2
        "N"            → M·L·T^-2（查导出单位表）
        ""  或  "1"    → 无量纲

    做法：先按 * 和 / 切分，遇到 ^ 取指数。词法很简单，够用。
    """
    if text is None:
        raise UnitError("单位为空")
    s = text.strip()
    if s in ("", "1", "无", "无量纲"):
        return NONE
    return _parse_unit_terms(s)


def _parse_unit_terms(s):
    """
    解析 "分子/分母" 形式。乘除以同级、从左到右处理，
    所以先把整个串按 * 和 / 切开，记录每一段是"乘"还是"除"。
    例："kg*m/s^2" → [('*','kg'), ('*','m'), ('/','s^2')]
    """
    if "/" not in s and "*" not in s:
        # 可能带指数，如 "s^2"
        return _parse_unit_atom(s)

    # 切分：保留分隔符
    tokens = []
    buf = ""
    op = "*"                       # 第一段默认是"乘"
    for ch in s:
        if ch in "*/":
            tokens.append((op, buf))
            op = ch
            buf = ""
        else:
            buf += ch
    tokens.append((op, buf))

    # 第一段（op == "*" 且是开头那一段）作为基准
    result = None
    for i, (o, seg) in enumerate(tokens):
        seg = seg.strip()
        if seg == "":
            continue
        d = _parse_unit_atom(seg)
        if result is None:
            result = d
            continue
        result = result * d if o == "*" else result / d
    if result is None:
        raise UnitError("无法解析单位：%s" % s)
    return result


def _parse_unit_atom(seg):
    """
    解析单个单位（可能带指数），如 "m"、"s^2"、"m^3"。
    也支持 "m2" 这种省略 ^ 的写法（不少人这么写）。
    """
    seg = seg.strip()
    if seg == "":
        return NONE

    # 显式指数："s^2" / "s**2"
    if "^" in seg or "**" in seg:
        base, _, exp = seg.replace("**", "^").partition("^")
        try:
            e = float(exp)
        except ValueError:
            raise UnitError("指数不是数字：%s" % seg)
        return _parse_unit_atom(base) ** e

    # 省略 ^ 的写法："m2"、"s2"（只有单位和数字）
    if len(seg) > 1 and seg[-1].isdigit() and seg[-2] not in "0123456789":
        if seg[:-1] in UNITS:
            return UNITS[seg[:-1]] ** int(seg[-1])

    if seg in UNITS:
        return UNITS[seg]

    raise UnitError("不认识这个单位：%s（如果它是新单位，请加进 quantity.py 的 UNITS 表）"
                    % seg)
