# -*- coding: utf-8 -*-
"""
expr.py —— 公式解析器 / 计算器 / 数学排版器
==========================================

【这个文件解决什么问题】

整个流水线有一个"单一真相"原则：

    ★ 一条公式只写一遍（就是那串朴素文本，如 "T = 2*pi*sqrt(L/g)"），
      然后由这一个来源同时派生出三样东西：
        1. 量纲检查用的表达式树
        2. 数值代入算账用的表达式树
        3. 网页上显示的数学排版（MathML，浏览器原生渲染，不需要任何外部库）

这样做的意义：**显示出来的样子和机器检查的东西永远是同一个东西**，
不会出现"网页上是对的、实际校验的是另一个式子"这种漂移。

【为什么用 MathML 而不是 KaTeX】

硬约束要求"断网可用、单文件、小于 300 KB"。KaTeX 光 JS + 字体就要 1 MB 以上，
塞不进单文件。而 MathML 是浏览器的原生能力（Chrome 109+ / Firefox / Safari 都支持），
零体积、零依赖，正好合适。

【支持的写法】

    数字        2   0.5   3.14
    变量        v_0   theta   E_k   R_max
    常量        pi   π
    运算        + - * / ^   （** 等同于 ^）
    函数        sqrt sin cos tan asin acos atan abs ln log exp
    括号        ( )

下标用下划线写：v_0 会显示成 v₀，E_k 显示成 E_k（k 用斜体）。
希腊字母可以直接写英文名（theta），也可以直接写符号（θ）。
"""

import math

from .quantity import Dim, Quantity, DimError, EPS


# ============================================================
# 一、词法分析（把一串字符切成一个个"词"）
# ============================================================

class ExprError(Exception):
    """公式写法有问题（语法错误、变量未定义等）。"""
    pass


# 希腊字母：英文名 → 符号。这样公式里写 theta 或 θ 都行。
GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω",
    "Delta": "Δ", "Gamma": "Γ", "Theta": "Θ", "Lambda": "Λ", "Sigma": "Σ",
    "Phi": "Φ", "Omega": "Ω", "Psi": "Ψ",
}

# 常量表：名字 → 数值（常量一律无量纲）
CONSTANTS = {
    "pi": math.pi,
    "π": math.pi,
}

# 支持的函数名
FUNCS = {
    "sqrt", "cbrt", "sin", "cos", "tan", "asin", "acos", "atan",
    "abs", "ln", "log", "exp",
}

# 函数的中文说明，报错时好用
FUNC_DOC = {
    "sqrt": "开平方", "cbrt": "开立方",
    "sin": "正弦", "cos": "余弦", "tan": "正切",
    "asin": "反正弦", "acos": "反余弦", "atan": "反正切",
    "abs": "绝对值", "ln": "自然对数", "log": "常用对数", "exp": "e 的幂",
}


def tokenize(text):
    """
    把公式文本切成词。返回 [(类型, 值, 位置), ...]。

    类型有：NUM（数字）/ NAME（名字，含变量名和函数名）/ OP（运算符）
    """
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        # 空白直接跳过
        if ch.isspace():
            i += 1
            continue

        # 数字：123 / 0.5 / .5
        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            j = i
            seen_dot = False
            while j < n and (text[j].isdigit() or (text[j] == "." and not seen_dot)):
                if text[j] == ".":
                    seen_dot = True
                j += 1
            tokens.append(("NUM", float(text[i:j]), i))
            i = j
            continue

        # ** 先于单个 * 处理
        if text.startswith("**", i):
            tokens.append(("OP", "^", i))
            i += 2
            continue

        # 单个运算符
        if ch in "+-*/^(),=":
            tokens.append(("OP", ch, i))
            i += 1
            continue

        # 名字：字母开头（含希腊字母），后面可以跟字母、数字、下划线
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(("NAME", text[i:j], i))
            i = j
            continue

        raise ExprError("第 %d 个字符处出现了不认识的符号：%r" % (i + 1, ch))

    return tokens


# ============================================================
# 二、语法分析（把词序列搭成一棵树）
# ============================================================
# 树节点统一用「元组」表示，简单直观：
#   ("num", 2.0)              数字
#   ("var", "v_0")            变量
#   ("neg", A)                取负
#   ("add"/"sub"/"mul"/"div"/"pow", A, B)
#   ("call", "sqrt", [A])     函数调用

class Parser:
    """递归下降解析器。每一层对应一个优先级。"""

    def __init__(self, tokens):
        self.tk = tokens
        self.i = 0

    # ---------- 小工具 ----------
    def peek(self):
        return self.tk[self.i] if self.i < len(self.tk) else None

    def next(self):
        t = self.peek()
        if t is None:
            raise ExprError("公式意外结束，后面还缺内容")
        self.i += 1
        return t

    def eat_op(self, op):
        t = self.peek()
        if t and t[0] == "OP" and t[1] == op:
            self.i += 1
            return True
        return False

    # ---------- 各优先级 ----------
    def parse(self):
        node = self.parse_expr()
        if self.peek() is not None:
            t = self.peek()
            raise ExprError("第 %d 个字符附近有多余的内容：%r" % (t[2] + 1, t[1]))
        return node

    def parse_expr(self):
        """加减（优先级最低）"""
        node = self.parse_term()
        while True:
            if self.eat_op("+"):
                node = ("add", node, self.parse_term())
            elif self.eat_op("-"):
                node = ("sub", node, self.parse_term())
            else:
                return node

    def parse_term(self):
        """乘除"""
        node = self.parse_unary()
        while True:
            if self.eat_op("*"):
                node = ("mul", node, self.parse_unary())
            elif self.eat_op("/"):
                node = ("div", node, self.parse_unary())
            else:
                return node

    def parse_unary(self):
        """一元正负号：-x 要先于乘方结合，即 -x^2 理解为 -(x^2)"""
        if self.eat_op("-"):
            return ("neg", self.parse_unary())
        if self.eat_op("+"):
            return self.parse_unary()
        return self.parse_power()

    def parse_power(self):
        """乘方：右结合，所以 2^3^2 = 2^(3^2)"""
        base = self.parse_atom()
        if self.eat_op("^"):
            return ("pow", base, self.parse_unary())
        return base

    def parse_atom(self):
        """最基本的单位：数字 / 变量 / 函数调用 / 括号"""
        t = self.next()

        if t[0] == "NUM":
            return ("num", t[1])

        if t[0] == "NAME":
            name = t[1]
            # 函数调用：名字后面紧跟左括号
            nxt = self.peek()
            if nxt and nxt[0] == "OP" and nxt[1] == "(":
                if name not in FUNCS:
                    raise ExprError(
                        "不认识函数 %s%s（目前支持：%s）"
                        % (name, "（%s）" % FUNC_DOC.get(name, ""),
                           "、".join(sorted(FUNCS)))
                    )
                self.next()                       # 吃掉 (
                args = [self.parse_expr()]
                while self.eat_op(","):
                    args.append(self.parse_expr())
                if not self.eat_op(")"):
                    raise ExprError("函数 %s 的括号没有闭合" % name)
                return ("call", name, args)
            return ("var", name)

        if t[0] == "OP" and t[1] == "(":
            node = self.parse_expr()
            if not self.eat_op(")"):
                raise ExprError("括号没有闭合")
            return node

        raise ExprError("第 %d 个字符处的 %r 放错了位置" % (t[2] + 1, t[1]))


def parse(text):
    """把公式文本解析成表达式树。"""
    if not text or not text.strip():
        raise ExprError("公式是空的")
    return Parser(tokenize(text)).parse()


def parse_equation(text):
    """
    解析"方程"，即带一个等号的公式，如 "v = v_0 + a*t"。
    返回 (左树, 右树)。
    """
    if text is None or "=" not in text:
        raise ExprError("公式必须写成「左边 = 右边」的形式，实际是：%r" % text)
    parts = text.split("=")
    if len(parts) != 2:
        raise ExprError("公式里只能有一个等号，实际有 %d 个：%r" % (len(parts) - 1, text))
    return parse(parts[0]), parse(parts[1])


# ============================================================
# 三、求值（把树算成一个 Quantity）
# ============================================================

def collect_vars(node, out=None):
    """把一棵树里用到的所有变量名收集起来（用于检查有没有漏声明）。"""
    if out is None:
        out = set()
    kind = node[0]
    if kind == "var":
        out.add(node[1])
    elif kind == "neg":
        collect_vars(node[1], out)
    elif kind == "call":
        for a in node[2]:
            collect_vars(a, out)
    elif kind in ("add", "sub", "mul", "div", "pow"):
        collect_vars(node[1], out)
        collect_vars(node[2], out)
    return out


def evaluate(node, env):
    """
    把表达式树算成一个 Quantity。

    参数 env 是「变量名 → Quantity」的字典。
    Quantity 的数值可以是 None，表示"只知道量纲、不知道数值"，
    这时所有算出来的结果数值也都是 None —— 于是"只查量纲"也能跑通。
    """
    kind = node[0]

    if kind == "num":
        return Quantity.plain(node[1])

    if kind == "var":
        name = node[1]
        if name in CONSTANTS:                  # 常量（pi 等）
            return Quantity.plain(CONSTANTS[name])
        if name in GREEK and name not in env:  # 希腊字母英文名 → 符号名
            name = GREEK[name]
        if name in env:
            q = env[name]
            if not isinstance(q, Quantity):
                raise ExprError("变量 %s 的值不是物理量" % name)
            return q
        raise ExprError("公式里用到了变量 %s，但没有说明它是什么（请在 vars 里声明）" % name)

    if kind == "neg":
        return -evaluate(node[1], env)

    if kind in ("add", "sub", "mul", "div", "pow"):
        a = evaluate(node[1], env)
        b = evaluate(node[2], env)
        try:
            if kind == "add":
                return a + b
            if kind == "sub":
                return a - b
            if kind == "mul":
                return a * b
            if kind == "div":
                return a / b
            return a ** b
        except DimError as exc:
            # 把量纲错误包成公式错误，报告里好读
            raise ExprError("%s" % exc)
        except ZeroDivisionError:
            raise ExprError("公式里出现了除以零")

    if kind == "call":
        name = node[1]
        args = [evaluate(a, env) for a in node[2]]
        return _call_func(name, args, node)

    raise ExprError("无法处理的表达式节点：%s" % (kind,))


def _call_func(name, args, node):
    """函数求值。注意每个函数对参数的量纲有要求，这里逐条把关。"""
    if name == "sqrt":
        _need_args(name, args, 1)
        return args[0].sqrt()

    if name == "cbrt":
        _need_args(name, args, 1)
        return Quantity(_sqrt3(args[0].value), args[0].dim ** (1.0 / 3))

    if name == "abs":
        _need_args(name, args, 1)
        v = args[0].value
        return Quantity(None if v is None else abs(v), args[0].dim)

    # 三角函数：自变量必须无量纲，结果也是无量纲
    if name in ("sin", "cos", "tan"):
        _need_args(name, args, 1)
        _need_dimensionless(name, args[0])
        v = args[0].value
        fn = {"sin": math.sin, "cos": math.cos, "tan": math.tan}[name]
        return Quantity(None if v is None else fn(v), Dim())

    # 反三角函数：自变量无量纲，结果是无量纲（弧度）
    if name in ("asin", "acos", "atan"):
        _need_args(name, args, 1)
        _need_dimensionless(name, args[0])
        v = args[0].value
        if v is not None and name in ("asin", "acos") and not (-1 <= v <= 1):
            raise ExprError("%s 的自变量必须在 -1 到 1 之间，实际是 %g" % (name, v))
        fn = {"asin": math.asin, "acos": math.acos, "atan": math.atan}[name]
        return Quantity(None if v is None else fn(v), Dim())

    # 对数与指数：都要求无量纲
    if name in ("ln", "log", "exp"):
        _need_args(name, args, 1)
        _need_dimensionless(name, args[0])
        v = args[0].value
        if v is None:
            return Quantity(None, Dim())
        if name == "exp":
            return Quantity(math.exp(v), Dim())
        if v <= 0:
            raise ExprError("对数的自变量必须大于 0，实际是 %g" % v)
        return Quantity(math.log(v) if name == "ln" else math.log10(v), Dim())

    raise ExprError("不认识函数 %s" % name)


def _need_args(name, args, n):
    if len(args) != n:
        raise ExprError("函数 %s 需要 %d 个参数，实际给了 %d 个" % (name, n, len(args)))


def _need_dimensionless(name, q):
    if q.dim != Dim():
        raise ExprError(
            "函数 %s 的自变量必须是无量纲的数，实际是 %s"
            "（常见原因：角度忘了用弧度，或者公式写错了）" % (name, q.dim)
        )


def _sqrt3(v):
    if v is None:
        return None
    return math.copysign(abs(v) ** (1.0 / 3), v)


# ============================================================
# 四、数学排版（把树变成 MathML）
# ============================================================
# MathML 是浏览器的原生能力，不需要加载任何外部脚本或字体。
# 这里把表达式树翻译成 MathML，让网页上显示出正规的数学公式。

# 下标里的内容如果是这些，要用正体显示（表示它是标记而不是变量）。
# 否则 F_N 里的 N 会显示成斜体，看起来像另一个物理量。
SUBSCRIPT_NORMAL = {"max", "min", "eff", "tot", "avg", "rms", "net", "N", "n"}


def _mi(name):
    """把一个名字变成 <mi>。单个字母用斜体（数学惯例），多个字母用正体。"""
    greek = GREEK.get(name)
    if greek:
        return "<mi>%s</mi>" % greek
    if len(name) == 1:
        return "<mi>%s</mi>" % _esc(name)
    return '<mi mathvariant="normal">%s</mi>' % _esc(name)


def _mn(value):
    """把一个数字变成 <mn>。整数不显示小数点。"""
    if abs(value - round(value)) < EPS and abs(value) < 1e15:
        return "<mn>%d</mn>" % int(round(value))
    s = ("%g" % value)
    return "<mn>%s</mn>" % s


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _symbol_mathml(name):
    """变量名 → MathML。支持下划线当下标，如 v_0 → v₀。"""
    # 希腊字母英文名整体替换
    if name in GREEK and "_" not in name:
        return "<mi>%s</mi>" % GREEK[name]

    if "_" in name:
        base, _, sub = name.partition("_")
        if sub:
            return "<msub>%s%s</msub>" % (_mi(base), _sub_mathml(sub))
    return _mi(name)


def _sub_mathml(sub):
    """下标内容：纯数字当数字，特殊标记用正体，其余当名字。"""
    if sub.isdigit():
        return "<mn>%s</mn>" % sub
    if sub in SUBSCRIPT_NORMAL:
        return '<mi mathvariant="normal">%s</mi>' % _esc(sub)
    return _mi(sub)


def to_mathml(node):
    """表达式树 → MathML 字符串。"""
    kind = node[0]

    if kind == "num":
        return _mn(node[1])

    if kind == "var":
        return _symbol_mathml(node[1])

    if kind == "neg":
        return "<mrow><mo>−</mo>%s</mrow>" % _wrap(node[1])

    if kind in ("add", "sub"):
        op = "+" if kind == "add" else "−"
        return "<mrow>%s<mo>%s</mo>%s</mrow>" % (_wrap(node[1]), op, _wrap(node[2]))

    if kind == "mul":
        return "<mrow>%s%s%s</mrow>" % (
            _wrap(node[1]),
            # 数字和符号之间用"隐式乘号"，其余情况用圆点
            "&#x2062;" if _is_numberish(node[1]) or _is_numberish(node[2]) else "&#x22C5;",
            _wrap(node[2]),
        )

    if kind == "div":
        return "<mfrac>%s%s</mfrac>" % (_wrap(node[1]), _wrap(node[2]))

    if kind == "pow":
        base, exp = node[1], node[2]
        # x^0.5 显示成根号
        if exp[0] == "num" and abs(exp[1] - 0.5) < EPS:
            return "<msqrt>%s</msqrt>" % _wrap(base)
        # x^-1 显示成 1/x
        if exp[0] == "num" and exp[1] == -1:
            return "<mfrac><mn>1</mn>%s</mfrac>" % _wrap(base)
        if exp[0] == "neg" and exp[1][0] == "num":
            return "<mfrac><mn>1</mn>%s</mfrac>" % _wrap(("pow", base, exp[1]))
        return "<msup>%s%s</msup>" % (_wrap(base), _wrap(exp))

    if kind == "call":
        name, args = node[1], node[2]
        if name == "sqrt":
            return "<msqrt>%s</msqrt>" % _wrap(args[0])
        if name == "abs":
            return "<mrow><mo>|</mo>%s<mo>|</mo></mrow>" % _wrap(args[0])
        fn = {"ln": "ln", "log": "lg", "exp": "e"}.get(name, name)
        inner = "<mo>,</mo>".join(_wrap(a) for a in args)
        return ('<mrow>%s<mo>&#x2061;</mo><mo>(</mo>%s<mo>)</mo></mrow>'
                % (_mi(fn), inner))

    raise ExprError("无法排版这个表达式节点：%s" % (kind,))


def _wrap(node):
    """需要加括号保护时用，简单场景直接返回。"""
    return to_mathml(node)


def _is_numberish(node):
    """判断一个节点是不是"数字"，决定乘号怎么显示。"""
    return node[0] == "num"


def equation_to_mathml(lhs, rhs):
    """整条方程 → MathML（等号两侧）。"""
    return "<mrow>%s<mo>=</mo>%s</mrow>" % (_wrap(lhs), _wrap(rhs))


# ============================================================
# 五、纯文本显示（报告里用，便于在不支持 MathML 的地方阅读）
# ============================================================

# 纯文本显示时用到的优先级表：数字越小结合越松。
# 作用是在必要的地方补上括号，避免显示成有歧义的样子。
# 例如 (v_0 + v)/2 必须显示成 (v_0 + v)/2，不能显示成 v_0 + v/2。
_PLAIN_PREC = {
    "add": 1, "sub": 1, "mul": 2, "div": 2, "neg": 3, "pow": 4,
    "call": 5, "var": 6, "num": 6,
}


def _plain_child(node, parent_prec):
    """生成子表达式的纯文本，必要时加括号。"""
    s = to_plain(node)
    if _PLAIN_PREC.get(node[0], 6) < parent_prec:
        return "(%s)" % s
    return s


def to_plain(node):
    """表达式树 → 人能读的纯文本，如 T = 2π√(L/g)。"""
    kind = node[0]
    if kind == "num":
        return "%g" % node[1]
    if kind == "var":
        name = node[1]
        if name in GREEK:
            return GREEK[name]
        return name
    if kind == "neg":
        return "-" + _plain_child(node[1], 3)
    if kind == "add":
        return "%s + %s" % (_plain_child(node[1], 1), _plain_child(node[2], 1))
    if kind == "sub":
        return "%s - %s" % (_plain_child(node[1], 1), _plain_child(node[2], 2))
    if kind == "mul":
        a, b = _plain_child(node[1], 2), _plain_child(node[2], 2)
        if node[1][0] == "num" or node[2][0] == "num":
            return "%s%s" % (a, b)
        return "%s·%s" % (a, b)
    if kind == "div":
        return "%s/%s" % (_plain_child(node[1], 2), _plain_child(node[2], 3))
    if kind == "pow":
        exp = node[2]
        if exp[0] == "num" and abs(exp[1] - 0.5) < EPS:
            return "√(%s)" % to_plain(node[1])
        if exp[0] == "num":
            return "%s^%g" % (_plain_child(node[1], 4), exp[1])
        return "%s^(%s)" % (_plain_child(node[1], 4), to_plain(exp))
    if kind == "call":
        name = node[1]
        inner = ", ".join(to_plain(a) for a in node[2])
        if name == "sqrt":
            return "√(%s)" % inner
        if name == "abs":
            return "|%s|" % inner
        return "%s(%s)" % (name, inner)
    return "?"


def equation_to_plain(lhs, rhs):
    return "%s = %s" % (to_plain(lhs), to_plain(rhs))
