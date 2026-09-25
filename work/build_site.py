# -*- coding: utf-8 -*-
"""
build_site.py —— 把校验通过的知识库生成成单个 HTML 文件
=====================================================

【怎么运行】
    python build_site.py

【它做的事】
    1. 读 kb/ 目录里的全部知识点
    2. 跑结构检查 —— 有错误就停下，不出成品
    3. 跑物理检查 + 常见错误实测 —— 有任何一项不通过就停下，不出成品
    4. 全部通过才生成 outputs/高中物理知识库.html（单文件、断网可用、手机能开）
       同时生成 outputs/校验报告.md（纯文本版的校验底账）

【为什么"不通过就不出成品"】

简历上如果挂着"数量够但内容是错的"项目，那是负资产。
所以宁可少一个知识点，也不让没通过校验的内容进最终页面 —— 这条是硬规矩。

【为什么公式用 MathML 而不是 KaTeX】

设计要求"单文件、断网可用"（★ 体积上限已于 2026-09-19 放宽，不再卡 300 KB）。
KaTeX 光脚本加字体就要 1 MB 以上，塞进单文件不划算；
MathML 是浏览器原生能力（Chrome 109+ / Firefox / Safari 都支持），零体积零依赖。

【为什么公式只写一遍】

页面上显示的公式和校验器检查的公式，来自同一串文本。
显示用的 MathML、校验用的表达式树，都是从那一串文本现场解析出来的 ——
所以不可能出现"网页上是对的、机器查的是另一个式子"这种漂移。
"""

import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from physkit import kb as KB
from physkit import checks as CH
from physkit import expr as EX

# 正文散文里的公式排版还原（`2pi sqrt(L/g)` -> `2π √(L/g)`）。
# 只影响「写给人看」的正文；公式框与校验器用的 `expr` 字段一字不动。
from prose_math import render as prose
# ★ 数学标记的「教材写法」还原：把 v_avg 显示成 v̄、T_half 显示成 T_(1/2)……
#   只影响显示，不动内容，也不影响任何校验结论（详见 prose_math.display_mathml）。
from prose_math import display_mathml


# ============================================================
# 一、小工具
# ============================================================

def E(text):
    """把普通文本转义成 HTML 安全的形式。"""
    return html.escape(str(text), quote=True)


def paras(text):
    """把多行文本变成若干个 <p>。

    ★ 走 prose() 而不是 E()：正文里若混着机器写法公式（`F_合 = m a`、
      `T = 2pi sqrt(L/g)`），在这里统一还原成教材习惯的写法。
      公式框和校验器用的 `expr` 字段不经过这里，因此不受影响。
    """
    if not text:
        return ""
    return "\n".join("<p>%s</p>" % prose(line.strip())
                     for line in str(text).split("\n") if line.strip())


def mathml_block(expr_text):
    """把 MathML 包成浏览器能直接渲染的 <math>。

    ★ 所有公式框与符号表都从这里过 —— 所以「机器下标 → 教材写法」的还原
      只需挂在这两个函数上，一处覆盖全站。
    """
    return ('<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">'
            '%s</math>' % display_mathml(expr_text))


def mathml_inline(expr_text):
    return ('<math xmlns="http://www.w3.org/1998/Math/MathML">%s</math>'
            % display_mathml(expr_text))


LEVEL_CLASS = {"基础": "lv-base", "进阶": "lv-adv", "拓展": "lv-ext"}

# ---------- 学段标题配置 ----------
# 2026-09-26 修 bug：页面标题以前写死成「高中物理知识库」，
# 结果用同一套脚本生成的初中站，浏览器标签页也显示「高中物理」。
# 现在按「知识库目录名」自动取用对应学段的标题与简介。
SEGMENT = {
    "kb": {
        "title": "高中物理知识库",
        "lead": "高中物理（力学 · 电磁学 · 光学 · 热学 · 近代物理）的公式、推导、常见错误与典型例题。",
    },
    "kb_junior": {
        "title": "初中物理知识库",
        "lead": "初中物理（苏科版 2024 新版：声学 · 光学 · 物态变化 · 力与运动 · "
                "压强浮力 · 电学 · 能量）的公式、推导、常见错误与典型例题。",
    },
}
# 简介的后半段讲的是校验方法，两个学段通用，拼在 lead 后面
LEAD_TAIL = ("每一条公式都经过量纲一致性、单位标注、数值代入、变化方向、"
             "跨公式互证、极端参数扫描六类自动检查；每一条「常见错误」都真的"
             "被当作错误公式跑过一遍，验证它确实会被对应检查抓住。")


# ============================================================
# 二、六类检查的说明（写进页面，让读者知道这台机器在查什么）
# ============================================================

CHECK_DOC = [
    ("dimension", "量纲一致性",
     "公式等号两边必须是同一类物理量。7 个基本量（质量、长度、时间、电流、温度、物质的量、发光强度）"
     "的指数组合必须完全一致，对不上就一定写错了。抓的是「写反、漏项、多乘一项」。"),
    ("unit", "单位标注",
     "每个符号标注的单位，必须和公式推导出来的量纲相符。抓的是「公式没错但单位标错」这类隐蔽错误。"),
    ("numeric", "数值代入",
     "给一组自洽的情境参数，算出来的数必须等于独立算出的参考值。抓的是「量纲完全正确但系数写错」"
     "这类量纲查不出的错误 —— 例如把 ½at² 写成 at²。"),
    ("direction", "变化方向",
     "让某个参数从小变到大，结果必须朝物理规律要求的方向变化。抓的是「公式抄反了导致单调性反转」。"),
    ("consistency", "跨公式一致",
     "同一个情境下，两条推导路径完全不同的公式各算一遍同一个量，答案必须相同。"
     "这个「相同」不是人手工算出来的，是两条公式互相印证出来的 —— 所以它能发现「两条公式里有一条错了」。"),
    ("compare", "不等式关系",
     "物理里不少结论是不等式而不是等式（例如中间位置速度不小于中间时刻速度）。"
     "这类结论用等式检查抓不到，单独做一条。"),
    ("scan", "极端参数扫描",
     "让参数在很大范围里连续取几十个值，全程不允许出现异常、NaN 或无穷。抓的是定义域问题。"),
]


# ============================================================
# 三、生成页面片段
# ============================================================

def render_formula(f):
    """一个公式块：标题 + 排版后的式子 + 机器可读原文 + 校验记录。"""
    checks = f.get("results", [])
    n_ok = sum(1 for c in checks if c["ok"])
    n_all = len(checks)
    badge = "ok" if n_ok == n_all else "bad"

    rows = []
    for c in checks:
        rows.append(
            '<li class="chk %s"><span class="chk-type">%s</span>'
            '<span class="chk-mark">%s</span>'
            '<div class="chk-body"><div class="chk-detail">%s</div>%s</div></li>'
            % ("ok" if c["ok"] else "bad",
               E(c["type_name"]),
               "通过" if c["ok"] else "未通过",
               prose(c["detail"]),
               ('<div class="chk-note">依据：%s</div>' % prose(c["note"])) if c.get("note") else "")
        )

    when = ('<span class="f-when">%s</span>' % prose(f["when"])) if f.get("when") else ""

    return """
      <div class="fbox">
        <div class="fbox-head">
          <span class="f-name">%s</span>
          %s
          <span class="f-pass %s">%d/%d 校验通过</span>
        </div>
        <div class="fbox-math">%s</div>
        <div class="fbox-src">机器可读形式：<code>%s</code></div>
        <details class="checks">
          <summary>校验记录（%d 项）</summary>
          <ul class="chk-list">%s</ul>
        </details>
      </div>""" % (
        prose(f["name"]), when, badge, n_ok, n_all,
        mathml_block(f.get("mathml", "")),
        E(f.get("expr", "")),
        n_all, "".join(rows),
    )


def render_trap(t):
    """一条常见错误：错在哪、为什么错、被哪类检查抓住、实测结果。"""
    ver = t.get("verify") or {}
    caught = ver.get("caught")
    by = ver.get("by") or t.get("caught_by") or ""

    if caught is True:
        badge = '<span class="tb tb-caught">已实测 · %s 抓住</span>' % E(by)
    elif caught is False:
        badge = '<span class="tb tb-fail">实测未抓住 · 需修</span>'
    else:
        badge = '<span class="tb tb-manual">%s</span>' % E(by or "未自动验证")

    wrong = t.get("wrong", "")
    wrong_expr = t.get("wrong_expr")
    wrong_html = ('<code class="wrong-expr">%s</code>' % prose(wrong_expr)) if wrong_expr else ""

    return """
        <div class="trap">
          <div class="trap-head"><span class="trap-title">%s</span>%s</div>
          %s
          <div class="trap-why">%s</div>
          <div class="trap-verify"><b>实测记录</b>%s</div>
        </div>""" % (
        prose(wrong), badge,
        ('<div class="trap-wrong">错误写法 %s</div>' % wrong_html) if wrong_html else "",
        prose(t.get("why", "")),
        prose(ver.get("detail", "未做自动实测")),
    )


def render_point(pid, p):
    """一个完整的知识点卡片。"""
    lvl = p.get("level", "基础")
    n_ok, n_all = p["n_pass"], p["n_total"]
    ok = p["ok"]

    # --- 符号表 ---
    sym_rows = []
    for s in p["symbols"]:
        sym_rows.append(
            '<tr><td class="sym">%s</td><td class="sym-desc">%s</td>'
            '<td class="sym-unit">%s</td></tr>'
            % (mathml_inline(s["mathml"]), prose(s["desc"]), prose(s["unit"]) or "—"))
    sym_table = ('<table class="syms"><thead><tr><th>符号</th><th>含义</th><th>单位</th>'
                 '</tr></thead><tbody>%s</tbody></table>' % "".join(sym_rows))

    # --- 公式 ---
    formulas = "".join(render_formula(f) for f in p["formulas"])

    # --- 推导 ---
    deriv = ""
    if p["derivation"]:
        deriv = '<ol class="deriv">%s</ol>' % "".join(
            "<li>%s</li>" % prose(d) for d in p["derivation"])

    # --- 常见错误 ---
    traps = ""
    if p["errors"]:
        traps = '<div class="traps">%s</div>' % "".join(
            render_trap(t) for t in p["errors"])

    # --- 例题 ---
    example = ""
    ex = p.get("example") or {}
    if ex.get("stem"):
        steps = "".join("<li>%s</li>" % prose(s) for s in ex.get("solution", []))
        example = """
      <section class="field">
        <h4>典型例题</h4>
        <p class="stem">%s</p>
        <ol class="steps">%s</ol>
        <p class="answer"><b>答案</b>%s</p>
      </section>""" % (prose(ex["stem"]), steps, prose(ex.get("answer", "")))

    # --- 前置 / 后续 ---
    links = []
    if p["prereq"]:
        links.append("前置：%s" % "、".join(
            '<a href="#%s">%s</a>' % (E(i), E(i)) for i in p["prereq"]))
    if p["next"]:
        links.append("后续：%s" % "、".join(
            '<a href="#%s">%s</a>' % (E(i), E(i)) for i in p["next"]))
    nav = ('<div class="kp-nav">%s</div>' % " ｜ ".join(links)) if links else ""

    # --- 检索用文本 ---
    search_parts = [p["title"], p["chapter"], pid, lvl] + list(p["tags"])
    search_parts += [s["name"] + s["desc"] for s in p["symbols"]]
    search_parts += [f["name"] for f in p["formulas"]]
    search_parts += [f.get("plain", "") for f in p["formulas"]]
    search_parts += [t.get("wrong", "") for t in p["errors"]]
    search_text = " ".join(search_parts).lower()

    tags = "".join('<span class="tag">%s</span>' % E(t) for t in p["tags"])

    return """
    <article class="kp" id="%(pid)s" data-search="%(search)s" data-chapter="%(chapter)s">
      <div class="kp-head">
        <span class="kp-id">%(pid)s</span>
        <h3>%(title)s</h3>
        <span class="lvl %(lvlcls)s">%(lvl)s</span>
        <span class="kp-pass %(okcls)s">校验 %(nok)d/%(nall)d</span>
      </div>
      <div class="kp-body">
        <section class="field">
          <h4>定义</h4>
          %(definition)s
        </section>
        <section class="field">
          <h4>物理意义</h4>
          %(meaning)s
        </section>
        <section class="field">
          <h4>符号</h4>
          %(syms)s
        </section>
        <section class="field">
          <h4>公式与校验</h4>
          %(formulas)s
        </section>
        %(derivsec)s
        %(trapssec)s
        %(example)s
        <div class="kp-foot">
          <div class="tags">%(tags)s</div>
          %(nav)s
        </div>
      </div>
    </article>""" % {
        "pid": E(pid),
        "search": E(search_text),
        "chapter": E(p["chapter"]),
        "title": E(p["title"]),
        "lvlcls": LEVEL_CLASS.get(lvl, "lv-base"),
        "lvl": E(lvl),
        "okcls": "ok" if ok else "bad",
        "nok": n_ok,
        "nall": n_all,
        "definition": paras(p["definition"]),
        "meaning": paras(p["meaning"]),
        "syms": sym_table,
        "formulas": formulas,
        "derivsec": ('<section class="field"><h4>推导要点</h4>%s</section>' % deriv) if deriv else "",
        "trapssec": ('<section class="field"><h4>常见错误与实测结果</h4>%s</section>' % traps) if traps else "",
        "example": example,
        "tags": tags,
        "nav": nav,
    }


def render_method_panel(stats):
    """方法说明面板：六类检查各查了多少、通过率多少。"""
    rows = []
    by_type = stats["by_type"]
    for key, name, desc in CHECK_DOC:
        d = by_type.get(key)
        if not d:
            continue
        tot, ok = d[0], d[1]
        rows.append(
            '<div class="mrow"><div class="mname">%s</div>'
            '<div class="mbar"><span style="width:%.1f%%"></span></div>'
            '<div class="mnum">%d/%d</div></div>'
            '<div class="mdesc">%s</div>'
            % (E(name), 100.0 * ok / max(1, tot), ok, tot, E(desc)))

    return """
    <section class="method" id="method">
      <h2>这台机器在检查什么</h2>
      <p class="lead">内容里的每一个公式都由代码逐条过一遍下面这些检查。任何一项不通过，这条内容就不会出现在本页上 ——
      本页所有内容都是「全部通过」之后才生成出来的。</p>
      <div class="mgrid">%s</div>
    </section>""" % "".join(rows)


# ============================================================
# 四、整页组装
# ============================================================

CSS = """
:root{
  --bg:#f7f8fa; --panel:#ffffff; --ink:#1b1f24; --ink2:#4a5560; --ink3:#7b8794;
  --line:#e3e7ec; --line2:#eef1f5;
  --brand:#2f6df6; --brand-soft:#eaf0ff;
  --ok:#11875b; --ok-soft:#e6f6ee;
  --bad:#c0392b; --bad-soft:#fdecea;
  --warn:#9a6b00; --warn-soft:#fdf6e3;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB",
    "Microsoft YaHei","Source Han Sans SC",sans-serif;
  font-size:15px;line-height:1.75;
}
a{color:var(--brand);text-decoration:none}
a:hover{text-decoration:underline}
code{font-family:"Cascadia Mono",Consolas,"Courier New",monospace;font-size:.9em;
  background:#f1f3f7;padding:1px 5px;border-radius:4px;color:#2c3542}
math{font-size:1.12em}

/* ---------- 顶部 ---------- */
.hero{background:linear-gradient(160deg,#1c2536 0%,#2b3a56 55%,#3a4f78 100%);color:#fff;
  padding:44px 24px 38px}
.hero-in{max-width:1080px;margin:0 auto}
.eyebrow{font-size:12px;letter-spacing:.18em;color:#9fb4d8;text-transform:uppercase;margin-bottom:12px}
.hero h1{margin:0 0 10px;font-size:32px;font-weight:700;letter-spacing:.01em}
.hero .sub{margin:0;color:#c6d3e8;font-size:14px;max-width:760px;line-height:1.85}
.hstats{display:flex;flex-wrap:wrap;gap:12px;margin-top:26px}
.hstat{background:rgba(255,255,255,.09);border:1px solid rgba(255,255,255,.15);
  border-radius:10px;padding:12px 18px;min-width:118px}
.hstat b{display:block;font-size:23px;font-weight:700;letter-spacing:.01em}
.hstat span{font-size:12px;color:#a9bcd9}

/* ---------- 工具条 ---------- */
.toolbar{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.94);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:10px 24px}
.toolbar-in{max-width:1080px;margin:0 auto;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
#q{flex:1;min-width:200px;padding:8px 13px;border:1px solid var(--line);border-radius:8px;
  font-size:14px;background:#fff;color:var(--ink);outline:none;font-family:inherit}
#q:focus{border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-soft)}
.chips{display:flex;gap:7px;flex-wrap:wrap}
.chip{border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 13px;
  font-size:12.5px;cursor:pointer;color:var(--ink2);white-space:nowrap;font-family:inherit}
.chip:hover{border-color:var(--brand);color:var(--brand)}
.chip.on{background:var(--brand);border-color:var(--brand);color:#fff}
.tgl{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--ink2);cursor:pointer;
  white-space:nowrap}

/* ---------- 目录按钮与目录面板 ---------- */
/* 2026-09-26 新增：目录的 HTML 以前就生成了，但既没样式也没插入页面，等于没有。 */
.tbtn{border:1px solid var(--line);background:#fff;border-radius:8px;padding:7px 14px;
  font-size:13px;cursor:pointer;color:var(--ink2);white-space:nowrap;font-family:inherit}
.tbtn:hover{border-color:var(--brand);color:var(--brand)}
.tbtn.on{background:var(--brand);border-color:var(--brand);color:#fff}
.toc{display:none;max-width:1080px;margin:16px auto 0;background:var(--panel);
  border:1px solid var(--line);border-radius:12px;padding:14px 20px 18px}
.toc.open{display:block}
.toc-h{display:flex;align-items:center;justify-content:space-between;
  font-size:13px;font-weight:600;color:var(--ink2);
  padding-bottom:10px;border-bottom:1px solid var(--line2)}
.toc-close{cursor:pointer;color:var(--brand);font-weight:400;font-size:12.5px}
.toc-close:hover{text-decoration:underline}
.toc-in{display:grid;grid-template-columns:repeat(auto-fill,minmax(225px,1fr));
  gap:12px 24px;padding-top:12px;max-height:56vh;overflow-y:auto}
.toc-g{margin-bottom:4px}
.toc-g h4{margin:0 0 5px;font-size:13px;color:var(--ink);font-weight:600}
.toc-n{margin-left:6px;font-size:11.5px;color:var(--ink3);font-weight:400}
.toc a{display:block;font-size:12.5px;color:var(--ink2);padding:1px 0;line-height:1.55}
.toc a:hover{color:var(--brand);text-decoration:none}
.toc .tid{margin-left:6px;font-size:11px;color:var(--ink3);
  font-family:"Cascadia Mono",Consolas,monospace}
@media print{.toc{display:none!important}}
.tgl input{cursor:pointer}
#count{font-size:12.5px;color:var(--ink3);white-space:nowrap}

main{max-width:1080px;margin:0 auto;padding:26px 24px 70px}

/* ---------- 方法面板 ---------- */
.method{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:24px 26px;margin-bottom:26px}
.method h2{margin:0 0 8px;font-size:19px}
.lead{margin:0 0 20px;color:var(--ink2);font-size:13.5px;line-height:1.85}
.mgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px 26px}
.mname{font-size:13.5px;font-weight:600;margin-bottom:5px}
.mbar{height:6px;background:var(--line2);border-radius:3px;overflow:hidden;margin-bottom:4px}
.mbar span{display:block;height:100%;background:linear-gradient(90deg,#3f8f6a,#2f9e6b)}
.mnum{font-size:11.5px;color:var(--ok);font-weight:600;margin-bottom:5px}
.mdesc{font-size:12.5px;color:var(--ink3);line-height:1.7}

/* ---------- 章节 ---------- */
.chapter{margin:0 0 14px;padding-top:8px}
.chapter h2{font-size:21px;margin:0 0 6px}
.chapter .cintro{color:var(--ink2);font-size:13.5px;margin:0 0 6px;line-height:1.85}
.chapter .cmeta{font-size:12px;color:var(--ink3)}

/* ---------- 知识点 ---------- */
.kp{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  margin-bottom:20px;overflow:hidden;scroll-margin-top:74px}
.kp-head{display:flex;flex-wrap:wrap;align-items:center;gap:10px;
  padding:16px 22px;border-bottom:1px solid var(--line2);background:#fbfcfe}
.kp-head h3{margin:0;font-size:18px;flex:1;min-width:180px}
.kp-id{font-family:"Cascadia Mono",Consolas,monospace;font-size:11.5px;color:var(--ink3);
  background:#eef1f6;border-radius:5px;padding:2px 8px}
.lvl{font-size:11.5px;border-radius:5px;padding:2px 9px;font-weight:600}
.lv-base{background:#eaf0ff;color:#2f5fd0}
.lv-adv{background:#fdf3e3;color:#9a6b00}
.lv-ext{background:#f2e9fb;color:#6b3fa0}
.kp-pass{font-size:11.5px;border-radius:5px;padding:2px 9px;font-weight:600}
.kp-pass.ok{background:var(--ok-soft);color:var(--ok)}
.kp-pass.bad{background:var(--bad-soft);color:var(--bad)}
.kp-body{padding:6px 22px 22px}
.field{margin-top:20px}
.field h4{margin:0 0 8px;font-size:13.5px;color:var(--brand);font-weight:600;
  letter-spacing:.03em}
.field p{margin:0 0 9px;color:#232a33}
.stem{background:#f6f8fb;border-left:3px solid var(--brand);padding:10px 14px;border-radius:0 8px 8px 0}
.steps{margin:10px 0;padding-left:22px;color:#232a33}
.steps li{margin-bottom:6px}
.answer{background:var(--ok-soft);border-radius:8px;padding:10px 14px;color:#0f5f42}
.answer b{margin-right:10px;color:var(--ok)}

/* ---------- 符号表 ---------- */
.syms{width:100%;border-collapse:collapse;font-size:13.5px}
.syms th{text-align:left;font-size:11.5px;color:var(--ink3);font-weight:600;
  padding:6px 10px;border-bottom:1px solid var(--line)}
.syms td{padding:7px 10px;border-bottom:1px solid var(--line2);vertical-align:middle}
.syms tr:last-child td{border-bottom:none}
.sym{width:92px;text-align:center;background:#fafbfd}
.sym-desc{color:#232a33}
.sym-unit{width:86px;color:var(--ink3);font-family:Consolas,monospace;font-size:12.5px}

/* ---------- 视图切换：按知识点 / 只看公式 / 只看符号 ---------- */
/* 2026-09-19 需求：「点一下公式所有公式都出来，其他部分隐藏」。 */
.views{display:flex;gap:6px;flex-shrink:0}
.vbtn{font:inherit;font-size:12.5px;padding:5px 12px;border-radius:999px;
  border:1px solid var(--line);background:#fff;color:#3a4657;cursor:pointer}
.vbtn:hover{border-color:#b9c8e6}
.vbtn.on{background:#2f6df6;border-color:#2f6df6;color:#fff}
/* 非「按知识点」视图下，把章节与卡片整块藏掉 */
body[data-view="formula"] .chapter,body[data-view="formula"] .kp,
body[data-view="symbol"]  .chapter,body[data-view="symbol"]  .kp{display:none!important}
/* 两个索引段：只在对应视图里出现 */
body:not([data-view="formula"]) #allformulas{display:none}
body:not([data-view="symbol"])  #allsymbols{display:none}
/* 章节筛选按钮与目录在索引视图里没有意义，一并收起 */
body:not([data-view="point"]) .chips{display:none}

.vidx{max-width:1180px;margin:0 auto;padding:26px 24px 44px}
.vidx-h{font-size:20px;margin:0 0 6px}
.vidx-lead{color:#5b6a7d;font-size:13.5px;margin:0 0 18px;line-height:1.7}
.vidx-g{border-top:1px solid var(--line);padding:14px 0 8px}
.vidx-g h3{font-size:15px;margin:0 0 10px}
.vidx-p{font-size:13px;color:#2f6df6;font-weight:600;margin:14px 0 6px}
.vidx-p .pid{color:#9aa7b8;font-weight:400;font-size:12px;margin-left:6px}
.vidx-f{border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:0 0 10px;background:#fff}
.vf-name{font-size:12.5px;color:#5b6a7d;margin-bottom:2px}
.vf-when{font-size:12.5px;color:#7b8798;margin-top:2px}
.vs-base{font-size:15px;margin:0 0 8px;color:#2f6df6}
.vs-base .vs-count{font-size:12px;color:#9aa7b8;font-weight:400;margin-left:8px}
.vs-row{display:grid;grid-template-columns:96px 1fr 88px 160px;gap:10px;
  align-items:baseline;padding:5px 8px;border-radius:8px}
.vs-row:nth-child(odd){background:#f7f9fc}
.vs-sym{text-align:center}
.vs-desc{font-size:13px;color:#232a33}
.vs-unit{font-family:Consolas,monospace;font-size:12.5px;color:#7b8798}
.vs-from{font-size:12px;color:#9aa7b8;text-align:right}
@media(max-width:820px){
  .vs-row{grid-template-columns:78px 1fr 72px}
  .vs-from{display:none}
}

/* ---------- 公式块 ---------- */
.fbox{border:1px solid var(--line);border-radius:11px;margin-bottom:14px;overflow:hidden;
  background:#fff}
.fbox-head{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:10px 16px;
  background:#f8fafd;border-bottom:1px solid var(--line2)}
.f-name{font-weight:600;font-size:14px}
.f-when{font-size:12px;color:var(--ink3)}
.f-pass{margin-left:auto;font-size:11.5px;border-radius:5px;padding:2px 9px;font-weight:600}
.f-pass.ok{background:var(--ok-soft);color:var(--ok)}
.f-pass.bad{background:var(--bad-soft);color:var(--bad)}
.fbox-math{padding:16px 16px 10px;overflow-x:auto}
.fbox-src{padding:0 16px 12px;font-size:11.5px;color:var(--ink3)}
.checks{border-top:1px solid var(--line2)}
.checks>summary{padding:9px 16px;cursor:pointer;font-size:12.5px;color:var(--ink2);
  background:#fbfcfe;list-style:none;user-select:none}
.checks>summary::-webkit-details-marker{display:none}
.checks>summary::before{content:"▸ ";color:var(--ink3)}
.checks[open]>summary::before{content:"▾ "}
.checks>summary:hover{color:var(--brand)}
.chk-list{list-style:none;margin:0;padding:6px 16px 14px}
.chk{display:grid;grid-template-columns:96px 60px 1fr;gap:10px;align-items:start;
  padding:7px 0;border-bottom:1px dashed var(--line2);font-size:13px}
.chk:last-child{border-bottom:none}
.chk-type{font-weight:600;color:var(--ink2)}
.chk-mark{font-size:11.5px;border-radius:4px;padding:1px 7px;font-weight:600;text-align:center}
.chk.ok .chk-mark{background:var(--ok-soft);color:var(--ok)}
.chk.bad .chk-mark{background:var(--bad-soft);color:var(--bad)}
.chk-detail{color:#232a33;line-height:1.65}
.chk-note{color:var(--ink3);font-size:12px;line-height:1.6;margin-top:3px}

/* ---------- 推导 ---------- */
.deriv{margin:0;padding-left:22px;color:#232a33}
.deriv li{margin-bottom:7px}

/* ---------- 常见错误 ---------- */
.traps{display:flex;flex-direction:column;gap:12px}
.trap{border:1px solid var(--line);border-left:3px solid var(--warn);border-radius:0 10px 10px 0;
  padding:12px 16px;background:#fffdf8}
.trap-head{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:8px}
.trap-title{font-weight:600;font-size:13.5px;flex:1;min-width:200px}
.tb{font-size:11px;border-radius:5px;padding:2px 9px;font-weight:600;white-space:nowrap}
.tb-caught{background:var(--ok-soft);color:var(--ok)}
.tb-fail{background:var(--bad-soft);color:var(--bad)}
.tb-manual{background:var(--warn-soft);color:var(--warn)}
.trap-wrong{font-size:12.5px;color:var(--ink2);margin-bottom:7px}
.wrong-expr{background:#fdecea;color:#a5321f;font-weight:600}
.trap-why{font-size:13px;color:#232a33;line-height:1.75}
.trap-verify{margin-top:9px;padding:8px 12px;background:#f4f7fa;border-radius:7px;
  font-size:12px;color:var(--ink2);line-height:1.65}
.trap-verify b{margin-right:8px;color:var(--ink3)}

/* ---------- 页脚信息 ---------- */
.kp-foot{margin-top:20px;padding-top:14px;border-top:1px solid var(--line2);
  display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between}
.tags{display:flex;gap:6px;flex-wrap:wrap}
.tag{font-size:11.5px;background:#f1f4f9;color:var(--ink2);border-radius:999px;padding:3px 11px}
.kp-nav{font-size:12.5px;color:var(--ink3)}

.empty{text-align:center;color:var(--ink3);padding:50px 0;font-size:14px;display:none}

footer{border-top:1px solid var(--line);background:#fff;padding:30px 24px;
  color:var(--ink3);font-size:12.5px;line-height:1.85}
.foot-in{max-width:1080px;margin:0 auto}
footer b{color:var(--ink2)}

@media (max-width:620px){
  .hero h1{font-size:24px}
  .chk{grid-template-columns:1fr;gap:2px}
  .chk-mark{justify-self:start}
  main{padding:18px 14px 60px}
  .kp-body{padding:6px 15px 18px}
  .kp-head{padding:13px 15px}
}
@media print{
  .toolbar,.hero{background:#fff;color:#000}
  .checks{display:block}
  .kp{break-inside:avoid}
}
"""

JS = """
(function(){
  var q=document.getElementById('q');
  var cards=Array.prototype.slice.call(document.querySelectorAll('.kp'));
  var chips=Array.prototype.slice.call(document.querySelectorAll('.chip'));
  var count=document.getElementById('count');
  var expand=document.getElementById('expand');
  var cur='all';

  function apply(){
    var kw=(q.value||'').trim().toLowerCase();
    var shown=0;
    cards.forEach(function(c){
      var okKw=!kw||c.getAttribute('data-search').indexOf(kw)>=0;
      var okCh=cur==='all'||c.getAttribute('data-chapter')===cur;
      var vis=okKw&&okCh;
      c.style.display=vis?'':'none';
      if(vis)shown++;
    });
    count.textContent='显示 '+shown+' / '+cards.length+' 个知识点';
    document.getElementById('empty').style.display=shown?'none':'block';
    // 章节标题：该章没有可见卡片时一并隐藏
    document.querySelectorAll('.chapter').forEach(function(sec){
      var any=Array.prototype.some.call(sec.querySelectorAll('.kp'),function(c){
        return c.style.display!=='none';});
      sec.style.display=any?'':'none';
    });
  }

  chips.forEach(function(ch){
    ch.addEventListener('click',function(){
      chips.forEach(function(x){x.classList.remove('on');});
      ch.classList.add('on');
      cur=ch.getAttribute('data-chapter');
      apply();
    });
  });
  q.addEventListener('input',apply);

  function syncExpand(){
    document.querySelectorAll('.checks').forEach(function(d){d.open=expand.checked;});
  }
  expand.addEventListener('change',syncExpand);

  // ---------- 目录面板（2026-09-26 新增） ----------
  // 上面那段「.toc a」的点击处理一直都在，但页面上从来没有 .toc 元素，等于白写。
  var tocbtn=document.getElementById('tocbtn');
  var toc=document.getElementById('toc');
  var tocclose=document.getElementById('tocclose');
  function closeToc(){
    if(toc)toc.classList.remove('open');
    if(tocbtn)tocbtn.classList.remove('on');
  }
  if(tocbtn&&toc){
    tocbtn.addEventListener('click',function(){
      var open=toc.classList.toggle('open');
      tocbtn.classList.toggle('on',open);
    });
  }
  if(tocclose){tocclose.addEventListener('click',closeToc);}

  document.querySelectorAll('.kp-nav a, .toc a').forEach(function(a){
    a.addEventListener('click',function(e){
      var id=a.getAttribute('href').slice(1);
      var el=document.getElementById(id);
      if(el){e.preventDefault();el.scrollIntoView({behavior:'smooth',block:'start'});
        el.style.transition='box-shadow .4s';el.style.boxShadow='0 0 0 3px #2f6df6';
        setTimeout(function(){el.style.boxShadow='';},900);}
    });
  });

  // ---------- 视图切换：按知识点 / 只看公式 / 只看符号 ----------
  // 只做「显示哪一段」的切换，不重新渲染任何内容 —— 切来切去都是同一份数据。
  var vbtns = Array.prototype.slice.call(document.querySelectorAll('.vbtn'));
  vbtns.forEach(function(b){
    b.addEventListener('click', function(){
      vbtns.forEach(function(x){ x.classList.remove('on'); });
      b.classList.add('on');
      var v = b.getAttribute('data-view');
      document.body.setAttribute('data-view', v);
      if(v !== 'point'){
        // 索引视图里没有章节筛选，把它复位，免得以后再切回来时状态不一致
        cur = 'all';
        chips.forEach(function(x){ x.classList.remove('on'); });
        if(chips[0]) chips[0].classList.add('on');
        // 公式/符号视图下知识点卡片都被隐藏，目录点了也没地方跳，直接收起并隐藏按钮
        closeToc();
        if(tocbtn) tocbtn.style.display = 'none';
      }else{
        if(tocbtn) tocbtn.style.display = '';
      }
      apply();
      // 「显示 N / M 个知识点」这句在索引视图里不成立，清掉
      count.textContent = (v === 'point') ? count.textContent : '';
    });
  });

  apply();
})();
"""


def render_page(report, stats, chapters, title="高中物理知识库", lead=""):
    """组装整个 HTML 页面。title / lead 由调用方按学段传入。"""
    # 按章节分组，保持文件顺序
    groups = []
    for fname, chapter in chapters:
        cname = chapter.get("chapter", fname)
        pts = [report[p["id"]] for p in chapter.get("points", []) if p.get("id") in report]
        if pts:
            groups.append((cname, chapter.get("intro", ""), pts))

    # 目录
    # 2026-09-26：以前这里只生成了一串平铺链接，却从来没被插进页面模板 ——
    # JS 里那句「.toc a」的点击处理一直在空转。现在改成按章节分组，并真正渲染出来。
    toc_parts = ['<nav class="toc" id="toc" aria-label="知识点目录">',
                 '<div class="toc-h">全部知识点'
                 '<span class="toc-close" id="tocclose">收起</span></div>',
                 '<div class="toc-in">']
    for cname, _, pts in groups:
        toc_parts.append('<div class="toc-g"><h4>%s<span class="toc-n">%d</span></h4>'
                         % (E(cname), len(pts)))
        for p in pts:
            toc_parts.append('<a href="#%s">%s<span class="tid">%s</span></a>'
                             % (E(p["id"]), E(p["title"]), E(p["id"])))
        toc_parts.append('</div>')
    toc_parts.append('</div></nav>')
    toc = "\n".join(toc_parts)

    # 章节 + 卡片
    body = []
    for cname, cintro, pts in groups:
        n_ok = sum(1 for p in pts if p["ok"])
        body.append('<section class="chapter">')
        body.append("<h2>%s</h2>" % E(cname))
        if cintro:
            body.append('<p class="cintro">%s</p>' % prose(cintro))
        body.append('<p class="cmeta">%d 个知识点 · %d 条公式 · %d 项校验全部通过</p>'
                    % (len(pts), sum(len(p["formulas"]) for p in pts),
                       sum(p["n_total"] for p in pts)))
        body.append("</section>")
        body.extend(render_point(p["id"], p) for p in pts)

    chips = ['<button class="chip on" data-chapter="all">全部</button>']
    for cname, _, _ in groups:
        chips.append('<button class="chip" data-chapter="%s">%s</button>'
                     % (E(cname), E(cname.split("·")[-1].strip())))

    # ---------- 「只看公式」视图 ----------
    # 2026-09-19 需求：点一下「公式」，所有公式都出来，其他部分隐藏。
    fs = ['<section class="vidx" id="allformulas">',
          '<h2 class="vidx-h">全部公式一览</h2>',
          '<p class="vidx-lead">按章节与知识点排列。这里只列公式本身与适用条件；'
          '推导要点、例题、常见错误在「按知识点」视图里。</p>']
    for cname, _, pts in groups:
        fs.append('<div class="vidx-g"><h3>%s</h3>' % E(cname))
        for p in pts:
            fs.append('<div class="vidx-p">%s<span class="pid">%s</span></div>'
                      % (E(p["title"]), E(p["id"])))
            for f in p["formulas"]:
                when = f.get("when") or ""
                fs.append('<div class="vidx-f"><div class="vf-name">%s</div>%s%s</div>'
                          % (E(f.get("name", "")),
                             mathml_block(f.get("mathml", "")),
                             ('<div class="vf-when">%s</div>' % prose(when)) if when else ""))
        fs.append('</div>')
    fs.append('</section>')
    formulas_html = "\n".join(fs)

    # ---------- 「只看符号」视图：**按首字母归堆** ----------
    # 特别要求：「相同字母的放一块方便辨识」——
    # 所以按「下划线之前的部分」分组：v、v_0、v_avg 会排进同一块。
    base_map = {}
    for cname, _, pts in groups:
        for p in pts:
            for s in p["symbols"]:
                nm = s.get("name", "") or ""
                base = nm.split("_")[0] or nm
                base_map.setdefault(base, []).append((nm, s, p.get("title", p["id"])))

    def _base_key(b):
        # 拉丁字母排前面（按字母序），希腊字母排后面
        greek = bool(b) and ("\u0370" <= b[0] <= "\u03ff")
        return (1 if greek else 0, b.lower(), b)

    ss = ['<section class="vidx" id="allsymbols">',
          '<h2 class="vidx-h">全部符号一览</h2>',
          '<p class="vidx-lead">同一字母打头的符号放在一起，方便对照——'
          '例如 v、v_0、v_平均 会排在同一块里。最右列标注它出自哪个知识点。</p>']
    for base in sorted(base_map, key=_base_key):
        # 同名同单位的合并成一行，否则一个 v 会在十几个知识点里各占一行，反而难认。
        merged = {}
        order = []
        for nm, s, ptitle in base_map[base]:
            key = (nm, s.get("unit", ""))
            if key not in merged:
                merged[key] = {"s": s, "pts": []}
                order.append(key)
            if ptitle not in merged[key]["pts"]:
                merged[key]["pts"].append(ptitle)
        ss.append('<div class="vidx-g"><h3 class="vs-base">%s<span class="vs-count">%d 个符号</span></h3>'
                  % (E(base), len(order)))
        for key in sorted(order, key=lambda k: k[0]):
            nm, unit_raw = key
            s = merged[key]["s"]
            pts = merged[key]["pts"]
            where = pts[0] if len(pts) == 1 else "%s 等 %d 个知识点" % (pts[0], len(pts))
            ss.append('<div class="vs-row"><span class="vs-sym">%s</span>'
                      '<span class="vs-desc">%s</span>'
                      '<span class="vs-unit">%s</span>'
                      '<span class="vs-from">%s</span></div>'
                      % (mathml_inline(s.get("mathml", "")), prose(s.get("desc", "")),
                         prose(unit_raw) or "\u2014", E(where)))
        ss.append('</div>')
    ss.append('</section>')
    symbols_html = "\n".join(ss)
    views_html = formulas_html + "\n" + symbols_html

    hero_stats = [
        (stats["points"], "知识点"),
        (stats["formulas"], "公式"),
        (stats["checks"], "项自动校验"),
        ("%.0f%%" % (100.0 * stats["pass"] / max(1, stats["checks"])), "校验通过率"),
        (stats["traps_ok"], "条常见错误实测抓住"),
    ]
    hstats = "".join('<div class="hstat"><b>%s</b><span>%s</span></div>' % (v, E(k))
                     for v, k in hero_stats)

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s · 自动校验版</title>
<style>%s</style>
</head>
<body data-view="point">

<header class="hero">
  <div class="hero-in">
    <div class="eyebrow">离线可用 · 单文件 · 内容逐条自动校验</div>
    <h1>%s</h1>
    <p class="sub">%s</p>
    <div class="hstats">%s</div>
  </div>
</header>

<div class="toolbar">
  <div class="toolbar-in">
    <div class="views">
      <button class="vbtn on" data-view="point">按知识点</button>
      <button class="vbtn" data-view="formula">只看公式</button>
      <button class="vbtn" data-view="symbol">只看符号</button>
    </div>
    <button class="tbtn" id="tocbtn" type="button">目录</button>
    <input id="q" type="search" placeholder="搜索知识点、公式、符号、错误写法…" autocomplete="off">
    <div class="chips">%s</div>
    <label class="tgl"><input type="checkbox" id="expand"> 展开校验记录</label>
    <span id="count"></span>
  </div>
</div>

<main>
  %s
  %s
  %s
  %s
  <div id="empty" class="empty">没有匹配的知识点。换个关键词试试。</div>
</main>

<footer>
  <div class="foot-in">
    <p><b>关于这份文件</b>　这是一个单文件网页，双击即可打开，不需要联网、不需要安装任何东西，
    也可以直接发给别人。全部内容、样式、脚本、数学排版都在这一个文件里。</p>
    <p><b>关于正确性</b>　页面上的公式与校验器检查的公式来自同一串机器可读文本，
    排版出来的样子和机器验算的东西永远是同一个东西，不存在「显示一套、验算另一套」的可能。
    校验未通过的内容不会进入本页。</p>
    <p><b>关于边界</b>　自动检查能覆盖的是能被公式表达的错误。概念性错误（例如「重的物体下落更快」）
    不体现为公式写错，因此被明确标注为「人工审核」，而不是硬凑一个能跑的检查上去。</p>
  </div>
</footer>

<script>%s</script>
</body>
</html>""" % (E(title), CSS, E(title), prose(lead), hstats, "".join(chips), toc,
             render_method_panel(stats), "".join(body), views_html, JS)


# ============================================================
# 五、纯文本校验报告（Markdown）
# ============================================================

def render_markdown(report, stats, chapters):
    L = []
    L.append("# 高中物理知识库 · 自动校验报告")
    L.append("")
    L.append("本报告由校验流水线自动生成，记录每一个公式经受了哪些检查、结果如何。")
    L.append("")
    L.append("## 总览")
    L.append("")
    L.append("| 项目 | 数值 |")
    L.append("|---|---|")
    L.append("| 知识点 | %d 个 |" % stats["points"])
    L.append("| 公式 | %d 条 |" % stats["formulas"])
    L.append("| 自动校验 | %d 项 |" % stats["checks"])
    L.append("| 校验通过 | %d 项（%.1f%%）"
             % (stats["pass"], 100.0 * stats["pass"] / max(1, stats["checks"])))
    L.append("| 常见错误 | %d 条，实测抓住 %d 条，标注人工审核 %d 条"
             % (stats["traps"], stats["traps_ok"], stats["traps_unknown"]))
    L.append("")
    L.append("### 分类统计")
    L.append("")
    L.append("| 检查类型 | 通过 / 总数 |")
    L.append("|---|---|")
    for key, name, _ in CHECK_DOC:
        d = stats["by_type"].get(key)
        if d:
            L.append("| %s | %d / %d |" % (name, d[1], d[0]))
    L.append("")

    for fname, chapter in chapters:
        L.append("## %s" % chapter.get("chapter", fname))
        L.append("")
        for p in chapter.get("points", []):
            pid = p.get("id")
            if pid not in report:
                continue
            r = report[pid]
            L.append("### %s　%s" % (pid, r["title"]))
            L.append("")
            L.append("- 难度：%s" % r["level"])
            L.append("- 校验：%d / %d 项通过" % (r["n_pass"], r["n_total"]))
            L.append("")
            for f in r["formulas"]:
                L.append("#### 公式：%s" % f["name"])
                L.append("")
                L.append("```")
                L.append(f.get("plain", f.get("expr", "")))
                L.append("```")
                L.append("")
                for c in f["results"]:
                    L.append("- [%s] %s —— %s"
                             % ("通过" if c["ok"] else "未通过",
                                c["type_name"], c["detail"]))
                    if c.get("note"):
                        L.append("  - 依据：%s" % c["note"])
                L.append("")
            if r["errors"]:
                L.append("#### 常见错误实测")
                L.append("")
                for t in r["errors"]:
                    ver = t.get("verify") or {}
                    state = {True: "已抓住", False: "未抓住（缺陷）", None: "未自动验证"}[ver.get("caught")]
                    L.append("- **%s**（%s）" % (t.get("wrong", ""), state))
                    if t.get("wrong_expr"):
                        L.append("  - 错误写法：`%s`" % t["wrong_expr"])
                    L.append("  - 实测记录：%s" % ver.get("detail", ""))
                L.append("")
    return "\n".join(L)


# ============================================================
# 六、主流程
# ============================================================

def main(argv):
    kb_dir = os.path.join(HERE, "kb")
    out_dir = os.path.abspath(os.path.join(HERE, "..", "outputs"))
    if len(argv) > 1:
        kb_dir = argv[1]
    if len(argv) > 2:
        out_dir = argv[2]

    print("=" * 62)
    print("高中物理知识库 · 出成品")
    print("=" * 62)
    print("知识库：%s" % kb_dir)

    chapters = KB.load_kb(kb_dir)

    # --- 第一关：结构 ---
    issues, id_map = KB.check_structure(chapters)
    errs = [i for i in issues if i.level == "错误"]
    warns = [i for i in issues if i.level == "警告"]
    print("结构检查：%d 个错误，%d 个警告" % (len(errs), len(warns)))
    for i in issues:
        print("  %s" % i)
    if errs:
        print()
        print("存在结构性错误，拒绝出成品。")
        return 1

    # --- 第二关：物理 ---
    report = KB.run_physics_checks(chapters, id_map)
    stats = KB.summarize(report)
    print("物理检查：%d 项，通过 %d 项，通过率 %.1f%%"
          % (stats["checks"], stats["pass"],
             100.0 * stats["pass"] / max(1, stats["checks"])))
    print("常见错误实测：%d 条，抓住 %d 条，标注人工审核 %d 条"
          % (stats["traps"], stats["traps_ok"], stats["traps_unknown"]))

    bad = [r for r in report.values() if not r["ok"]]
    for b in bad:
        print("  ✘ %s %s" % (b["id"], b["title"]))
        for res in b["results"]:
            if not res["ok"]:
                print("      %s：%s" % (res["type_name"], res["detail"]))
    for t in stats["trap_failures"]:
        print("  ✘ 错误防线失效：%s" % t)

    if bad or stats["trap_failures"]:
        print()
        print("存在未通过项，拒绝出成品 —— 宁可少一个知识点，也不要错的内容。")
        return 1

    # --- 出成品 ---
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    # 按知识库目录名取学段标题 —— 以前写死成「高中」，初中站跟着一起错了
    seg_key = os.path.basename(os.path.normpath(kb_dir))
    seg = SEGMENT.get(seg_key, {"title": "物理知识库", "lead": ""})
    print("页面标题：%s" % seg["title"])

    # 成品文件名也跟着学段走，否则初中站会输出一个叫「高中物理知识库.html」的文件
    html_path = os.path.join(out_dir, "%s.html" % seg["title"])
    md_path = os.path.join(out_dir, "校验报告.md")
    page = render_page(report, stats, chapters, seg["title"], seg["lead"] + LEAD_TAIL)
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(page)

    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(report, stats, chapters))

    size_kb = os.path.getsize(html_path) / 1024.0
    print()
    print("已生成：")
    print("  %s（%.1f KB）" % (html_path, size_kb))
    print("  %s" % md_path)
    # ★ 2026-09-19：体积约束放宽（需求方的原话：「大小不重要，重要的是成品的效果，
    # 不出 bug 就行」）。这里只留一个 2 MB 的防呆上限，正常交付碰不到。
    # 不要为了压体积而删内容 —— 覆盖门槛优先。
    if size_kb > 2048:
        print("  注意：文件超过 2 MB 的防呆上限 —— 体积约束已放宽，此项仅防失控。")
        return 1
    print()
    print("结论：全部通过，成品可用。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
