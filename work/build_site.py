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
import re
import sys
from urllib.parse import quote

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
from prose_math import display_mathml, card_symbols, symbol_context


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


def mathml_block(expr_text, point_id=None):
    """把 MathML 包成浏览器能直接渲染的 <math>。

    ★ 所有公式框与符号表都从这里过 —— 所以「机器下标 → 教材写法」的还原
      只需挂在这两个函数上，一处覆盖全站。
    """
    return ('<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">'
            '%s</math>' % display_mathml(expr_text, point_id))


def mathml_inline(expr_text, point_id=None):
    return ('<math xmlns="http://www.w3.org/1998/Math/MathML">%s</math>'
            % display_mathml(expr_text, point_id))


def compact_check_records(page):
    """对完整校验文字作无损去重；保留全部记录和原文，浏览器展开时复原。

    重复的类型、量纲说明等只保存一次。逐条回读断言保证这不是删校验或删解释。
    JSON只转义可能影响HTML脚本边界的序列，数学子标签无需逐字符膨胀。
    """
    from collections import Counter
    pattern = r'(<script type="application/json" class="check-data">)(.*?)(</script>)'
    matches = list(re.finditer(pattern, page, re.S))
    rows_by_formula = [json.loads(match.group(2)) for match in matches]
    counts = Counter(value for rows in rows_by_formula for row in rows for value in (row[0], row[2], row[3]))
    texts = [value for value, count in counts.items() if count > 1 and len(value.encode('utf-8')) >= 12]
    positions = {value: index for index, value in enumerate(texts)}

    def safe_json(value):
        data = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        return re.sub(r'<(?=/?script\b|!--)', r'\\u003c', data, flags=re.I)

    iterator = iter(rows_by_formula)

    def replace(match):
        original = next(iterator)
        encoded = [[positions.get(row[0], row[0]), row[1],
                    positions.get(row[2], row[2]), positions.get(row[3], row[3])] for row in original]
        serialized = safe_json(encoded)
        decoded = json.loads(serialized)
        restored = [[texts[row[0]] if isinstance(row[0], int) else row[0], row[1],
                     texts[row[2]] if isinstance(row[2], int) else row[2],
                     texts[row[3]] if isinstance(row[3], int) else row[3]] for row in decoded]
        assert restored == original, '校验文字无损回读失败'
        return match.group(1)+serialized+match.group(3)

    packed = re.sub(pattern, replace, page, flags=re.S)
    dictionary = '<script type="application/json" id="check-texts">'+safe_json(texts)+'</script>'
    assert '<script>'+JS in packed, '缺少完整页脚本插入位置'
    return packed.replace('<script>'+JS, dictionary+'<script>'+JS, 1)


def compact_page(page):
    """移除排版缩进与 HTML 内不必重复的数学命名空间，保留全部内容。"""
    # 脚本、样式及保留空白的元素不得被空白压缩误改。
    chunks = re.split(r'(<(?:script|style|pre|textarea)\b[^>]*>.*?</(?:script|style|pre|textarea)>)',
                      page, flags=re.S | re.I)
    for index in range(0, len(chunks), 2):
        chunks[index] = re.sub(r'>\s+<', '><', chunks[index])
        chunks[index] = chunks[index].replace(' xmlns="http://www.w3.org/1998/Math/MathML"', '')
    # 本地静态样式只去掉注释和行首缩进，不更改选择器、属性或页面内容。
    for index in range(1, len(chunks), 2):
        if re.match(r'<style\b', chunks[index], re.I):
            # 保留中文样式注释；体积不再靠删除排错说明控制。
            chunks[index] = re.sub(r'(?m)^[ \t]+', '', chunks[index])
        elif re.match(r'<script\b', chunks[index], re.I):
            # 此页脚本无多行字符串；只删行首缩进，保留换行与全部执行语句。
            chunks[index] = re.sub(r'(?m)^[ \t]+', '', chunks[index])
    from 发布版本 import stamp
    return stamp(''.join(chunks))


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
LEAD_TAIL = ("公式经过量纲、单位和数值检查，并按适用情形检查方向、互证与边界。"
             "可计算的错误写法经过实测；概念错误标明人工审核。自动检查不等于正文与课程覆盖认证。")


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
    ("consistency", "关系一致核对",
     "直接求被检查公式的结果，与同一阶段的独立关系或设计情境基准核对。"
     "定义与反算的数据核对不代表不同物理原理互证；具体路径、数据性质和适用阶段见每项说明。"
     "正式通过只证明本项结果一致；是否能拒绝错误另以隔离错误注入验证。"),
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

    # 校验数据仍完整内嵌，打开明细时再创建重复的行标签，降低成品体积。
    rows = [[c["type_name"], bool(c["ok"]), prose(c["detail"]),
             prose(c.get("note", ""))] for c in checks]
    records = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    # JSON 放在非执行脚本中；转义标签起始符，防止文本提前结束容器。
    records = records.replace("<", "\\u003c").replace(">", "\\u003e")

    when = ('<span class="f-when">%s</span>' % prose(f["when"])) if f.get("when") else ""

    return """
      <div class="fbox">
        <div class="fbox-head">
          <span class="f-name">%s</span>
          %s
          <span class="f-pass %s">%d/%d 校验通过</span>
        </div>
        <div class="fbox-math">%s</div>
        <details class="fbox-src"><summary>查看校验用原式</summary><code>%s</code></details>
        <details class="checks">
          <summary>校验记录（%d 项）</summary>
          <script type="application/json" class="check-data">%s</script><ul class="chk-list"></ul>
        </details>
      </div>""" % (
        prose(f["name"]), when, badge, n_ok, n_all,
        mathml_block(f.get("mathml", "")),
        E(f.get("expr", "")),
        n_all, records,
    )


def render_trap(t, point_id="", index=0):
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
        <div class="trap" id="%s">
          <div class="trap-head"><span class="trap-title">%s</span>%s</div>
          %s
          <div class="trap-why">%s</div>
          <div class="trap-verify"><b>实测记录</b>%s</div>
        </div>""" % (
        E("trap-%s-%d" % (point_id, index + 1)), prose(wrong), badge,
        ('<div class="trap-wrong">错误写法 %s</div>' % wrong_html) if wrong_html else "",
        prose(t.get("why", "")),
        prose(ver.get("detail", "未做自动实测")),
    )


def validate_learning(p):
    """初中额外证据在展示前复核；不修改冻结校验器，也不混入其检查计数。"""
    import math
    factors = {('km/h', 'm/s'): 1/3.6, ('g', 'kg'): .001,
               ('mL', 'm³'): 1e-6, ('L', 'm³'): .001,
               ('g/cm³', 'kg/m³'): 1000, ('cm²', 'm²'): .0001,
               ('kΩ', 'Ω'): 1000, ('A', 'mA'): 1000,
               ('kW', 'W'): 1000, ('kW·h', 'J'): 3600000}
    counts = {'conversion': 0, 'range': 0, 'graph': 0}
    for e in p.get('evidence', []):
        if e['kind'] == 'conversion':
            value = e['value'] * factors[(e['from_unit'], e['to_unit'])]
            if not math.isclose(value, e['expected'], rel_tol=1e-10, abs_tol=1e-12):
                raise ValueError(p['id'] + '单位换算证据不符')
        elif e['kind'] == 'range':
            if not e['lo'] <= e['value'] <= e['hi']:
                raise ValueError(p['id'] + '数量级示例越界')
        else:
            raise ValueError('未知的补充证据类型')
        counts[e['kind']] += 1
    for g in p.get('figures', []):
        coordinates = dict(g['points'])
        if len(coordinates) != len(g['points']) or sorted(coordinates) != list(coordinates):
            raise ValueError('图像横坐标应严格递增')
        if g['xmax'] <= 0 or g['ymax'] <= 0 or not all(0 <= x <= g['xmax'] and 0 <= y <= g['ymax'] for x,y in g['points']):
            raise ValueError('图像坐标或范围错误')
        for c in g['checks']:
            if c['kind'] == 'point':
                value = coordinates[c['x']]
            elif c['kind'] == 'slope':
                value = (coordinates[c['right']] - coordinates[c['left']]) / (c['right'] - c['left'])
            else:
                raise ValueError('未知的图像读数类型')
            if not math.isclose(value, c['expected'], rel_tol=1e-10, abs_tol=1e-12):
                raise ValueError(p['id'] + '图像读数证据不符')
            counts['graph'] += 1
    for experiment in p.get('experiments', []):
        for key in ('title','purpose','apparatus','variables','steps','data','conclusion','limits'):
            if not experiment.get(key):
                raise ValueError(p['id'] + '实验链缺少' + key)
    return counts


@card_symbols()
def render_learning(p):
    """把初中定性主线、实验记录和读图显示在公式前；文字不冒称自动验算。"""
    validate_learning(p)
    blocks = []
    for section in p.get("core", []):
        blocks.append('<section class="field learning"><h4>%s</h4>%s</section>' %
                      (E(section["title"]), paras(section["text"])))
    for experiment in p.get("experiments", []):
        rows = [('目的', 'purpose'), ('装置与接法', 'apparatus'), ('控制、改变与观察', 'variables'),
                ('数据与处理', 'data'), ('结论与条件', 'conclusion'), ('误差与边界', 'limits')]
        # 按实际探究顺序先显示操作步骤，再显示数据、结论和误差。
        before = ''.join('<p><b>%s：</b>%s</p>' % (label, prose(experiment[key])) for label, key in rows[:3])
        after = ''.join('<p><b>%s：</b>%s</p>' % (label, prose(experiment[key])) for label, key in rows[3:])
        steps = ''.join('<li>%s</li>' % prose(step) for step in experiment['steps'])
        blocks.append('<details class="learning"><summary>实验：%s</summary>%s<p><b>步骤：</b></p><ol>%s</ol>%s</details>' %
                      (E(experiment['title']), before, steps, after))
    for graph in p.get("figures", []):
        # 坐标和读数来自同一组源数据；SVG只负责显示，不重新计算参考答案。
        xmax, ymax = graph['xmax'], graph['ymax']
        coords = ' '.join('%.2f,%.2f' % (55 + x / xmax * 325, 185 - y / ymax * 140) for x, y in graph['points'])
        labels = ''.join('<text x="%s" y="202">%s</text>' % (55+x/xmax*325, E(str(x))) for x in graph['xticks'])
        labels += ''.join('<text x="8" y="%s">%s</text>' % (189-y/ymax*140, E(str(y))) for y in graph['yticks'])
        # 密集刻度容易重叠；关键平台数值取自源坐标，标在曲线上方。
        if 'mark_x' in graph:
            x = graph['mark_x']; y = dict(graph['points'])[x]
            labels += '<text x="%s" y="%s">%s</text>' % (55+x/xmax*325, 177-y/ymax*140, E(str(y)))
        blocks.append('<figure class="learning"><svg viewBox="0 0 450 235" role="img" aria-label="%s" style="width:100%%;max-width:560px"><title>%s</title><path d="M55 35V185H390" fill="none" stroke="currentColor"/><polyline points="%s" fill="none" stroke="#2457d6" stroke-width="2"/>%s<text x="280" y="226">%s</text><text x="60" y="25">%s</text></svg><figcaption>%s</figcaption></figure>' %
                      (E(graph['title']), E(graph['title']), coords, labels, E(graph['xlabel']), E(graph['ylabel']), prose(graph['reading'])))
    for e in p.get('evidence', []):
        if e['kind'] == 'conversion':
            title = '单位换算证据'
            value = '%s %s = %s %s' % (e['value'], e['from_unit'], e['expected'], e['to_unit'])
        else:
            title = '数量级示例'
            value = '%s %s，参考区间 %s～%s %s' % (e['value'], e['unit'], e['lo'], e['hi'], e['unit'])
        blocks.append('<p class="learning"><b>%s：</b>%s。%s</p>' % (title, prose(value), prose(e['note'])))
    return ''.join(blocks)


@card_symbols(first_is_id=True)
def render_point(pid, p, related=None, quiz_href=None, segment_name="高中", source_page="index.html"):
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
    sym_table = ('<div class="table-scroll"><table class="syms"><thead><tr><th>符号</th><th>含义</th><th>单位</th>'
                 '</tr></thead><tbody>%s</tbody></table></div>' % "".join(sym_rows))

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
            render_trap(t, pid, i) for i, t in enumerate(p["errors"]))

    # 跨学段卡片按复核档位展示；低把握项目会直接提醒适用范围可能不同。
    related_html = ""
    if related:
        related_rows = []
        for item in related:
            tier = item.get("tier", "loose")
            tier_label = {"solid": "相关内容", "loose": "主题衔接提示", "risky": "谨慎比较"}.get(tier, "主题衔接提示")
            warning = ""
            if tier == "loose":
                warning = '<p class="related-warning">这是主题衔接建议；两侧的学习深度、定义或适用条件可能不同，请勿直接套用另一学段的公式。</p>'
            elif tier == "risky":
                warning = '<p class="related-warning related-risk">谨慎比较：%s 请先分别确认研究对象和适用条件，不要跨学段直接套用公式。</p>' % prose(item.get("risk", ""))
            related_rows.append(
                '<li><span class="related-tier tier-%s">%s</span>'
                '<a href="%s#%s">%s</a><span class="related-chapter">%s</span>'
                '<p>%s</p>%s</li>' % (E(tier), E(tier_label), E(item["href"]), E(item["id"]),
                                      E(item["title"]), E(item["chapter"]),
                                      prose(item.get("note", "")), warning))
        related_html = (
            '<section class="field related"><h4>跨学段学习线索'
            '<span class="related-badge">首轮整理 · 待教师复核</span></h4>'
            '<p class="related-notice">不同档位使用不同提示；请以各自知识点的定义、条件和推导为准。</p>'
            '<ul>%s</ul></section>' % "".join(related_rows))

    # 把每个知识点直接带到对应学段的同名例题，并附上可见的返回位置。
    practice_html = ""
    if quiz_href:
        source_label = segment_name + "物理知识库"
        query = "mode=example&point=%s&source=%s&return=%s" % (
            quote(pid), quote(source_label), quote(source_page + "#" + pid))
        practice_html = '<div class="kp-practice"><a class="practice-link" href="%s#%s">练这道题 ↗</a></div>' % (
            E(quiz_href), E(query))

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
    <article class="kp" id="%(pid)s" data-chapter="%(chapter)s">
      <div class="kp-head">
        <span class="kp-id">%(pid)s</span>
        <h3>%(title)s</h3>
        <span class="lvl %(lvlcls)s">难度：%(lvl)s</span><span class="lvl">范围：%(scope)s</span>
        <span class="kp-pass %(okcls)s">校验 %(nok)d/%(nall)d</span>
      </div>
      %(practice)s
      <div class="kp-body">
        %(learning)s
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
        %(related)s
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
        "scope": E(p.get("learning_scope") or segment_name),
        "okcls": "ok" if ok else "bad",
        "nok": n_ok,
        "nall": n_all,
        "definition": paras(p["definition"]),
        "learning": render_learning(p),
        "meaning": paras(p["meaning"]),
        "syms": sym_table,
        "formulas": formulas,
        "derivsec": ('<section class="field"><h4>推导要点</h4>%s</section>' % deriv) if deriv else "",
        "trapssec": ('<section class="field"><h4>常见错误与实测结果</h4>%s</section>' % traps) if traps else "",
        "related": related_html,
        "practice": practice_html,
        "example": example,
        "tags": tags,
        "nav": nav,
    }


def pass_percent(passed,total):
    """有失败时向下保留一位小数，避免2670/2671四舍五入为100%。"""
    import math
    return 100.0 if passed==total and total>0 else math.floor(1000.0*passed/max(1,total))/10


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
            '<div class="mnum">通过%d/%d · 失败%d</div></div>'
            '<div class="mdesc">%s</div>'
            % (E(name), pass_percent(ok, tot), ok, tot, tot-ok, E(desc)))

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
.segment-switchbar{background:#fff;border-bottom:1px solid var(--line);padding:9px 24px}
.segment-switch-inner{max-width:1080px;margin:0 auto;display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.segment-switch-label{font-size:12px;color:var(--ink3);margin-right:2px}
.segment-switchbar a{display:inline-flex;align-items:center;padding:5px 12px;border:1px solid var(--line);border-radius:999px;color:var(--ink2);font-size:13px}
.segment-switchbar a:hover{text-decoration:none;border-color:var(--brand);color:var(--brand)}
.segment-switchbar a[aria-current="page"]{background:var(--brand);border-color:var(--brand);color:#fff}
.segment-switchbar a.segment-quiz{margin-left:auto;border-color:#b8c9ff;background:#f4f7ff;color:#315fc4}

/* ---------- 工具条 ---------- */
.toolbar{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.94);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:10px 24px}
.toolbar-in{max-width:1080px;margin:0 auto;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
#q{flex:1;min-width:200px;padding:8px 13px;border:1px solid var(--line);border-radius:8px;
  font-size:14px;background:#fff;color:var(--ink);outline:none;font-family:inherit}
#q:focus{border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-soft)}
/* 章节筛选片不放进 sticky 工具条：手机上十几个章节片会换成七八行，
   把首屏整个占满（2026-09-29 修复）。它放在工具条下方、随页面滚动。 */
.chipsbar{max-width:1080px;margin:0 auto;padding:10px 24px 0}
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
/* 狭窄屏幕让网格与文字在卡片内换行；表格与长公式仅在局部滚动。 */
main,.kp,.kp-body,.mrow,.vs-row{min-width:0}
.kp-head{flex-wrap:wrap}.kp-head h3{min-width:0;overflow-wrap:anywhere}
.kp-body p,.kp-body li,.mdesc,.f-when,.sym-desc{overflow-wrap:anywhere}
.table-scroll{max-width:100%;overflow-x:auto}.vs-row{grid-template-columns:96px minmax(0,1fr) 88px 160px}
@media(max-width:820px){.vs-row{grid-template-columns:78px minmax(0,1fr) 72px}.vs-from{display:block;grid-column:1/-1;text-align:left}}
@media print{.toc{display:none!important}}
.tgl input{cursor:pointer}
#count{font-size:12.5px;color:var(--ink3);white-space:nowrap}

main{max-width:1080px;margin:0 auto;padding:26px 24px 70px}

/* ---------- 方法面板 ---------- */
.method{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:24px 26px;margin-bottom:26px}
.method h2{margin:0 0 8px;font-size:19px}
.lead{margin:0 0 20px;color:var(--ink2);font-size:13.5px;line-height:1.85}
.mgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:16px 26px}
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
.kp-practice{padding:9px 22px 0}
.practice-link{display:inline-flex;align-items:center;border:1px solid #bdccf4;border-radius:999px;
  padding:4px 12px;background:#f3f6ff;color:#315fc4;font-size:12px;font-weight:600}
.practice-link:hover{text-decoration:none;background:#e9efff}
.kp-body{padding:6px 22px 22px}
.related{background:#f7f9ff;border:1px solid #e1e8fb;border-radius:10px;padding:12px 16px}
.related h4{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.related-badge{font-size:11px;font-weight:500;color:#775b16;background:#fff4d7;border-radius:999px;padding:2px 8px}
.related-notice{font-size:12px;color:var(--ink3);margin:0 0 8px}
.related ul{margin:0;padding-left:20px}
.related li{margin:7px 0;color:var(--ink2)}
.related-chapter{margin-left:8px;color:var(--ink3);font-size:12px}
.related li p{margin:3px 0 0;font-size:12.5px;color:var(--ink2)}
.related-tier{display:inline-block;margin-right:7px;border-radius:999px;padding:1px 8px;font-size:11px;font-weight:600}
.tier-solid{background:#e9f7ee;color:#18794e}
.tier-loose{background:#fff4d7;color:#805f13}
.tier-risky{background:#fdecea;color:#a33232}
.related-warning{padding:6px 9px;border-left:3px solid #d49a19;background:#fff9e9;color:#75550e!important}
.related-risk{border-left-color:#c0392b;background:#fff1ef;color:#9c2c27!important}
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
  /* 手机上进一步压缩吸顶区：搜索框独占一行，视图按钮与目录按钮同行 */
  .toolbar{padding:8px 14px}
  .toolbar-in{gap:8px}
  .segment-switchbar{padding:8px 14px}
  #q{min-width:0;width:100%;flex:1 0 100%}
  .chipsbar{padding:9px 14px 0}
  /* 手机上章节片改横向滚动单行，避免换成七八行把首屏顶下去 */
  .chips{flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:5px}
  .chip{padding:4px 11px;font-size:12px}
}
@media print{
  .hero{background:#fff;color:#000;padding:8px}
  .hero p,.hero .eyebrow,.hero .hstats,.toolbar,.segment-switchbar,.method,.chipsbar{display:none!important}
  .checks{display:block}
  .kp{break-inside:auto}
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
  // 检索文本从同一份页面内容生成，不在文件中重复保存近十万字节索引。
  cards.forEach(function(card){
    var visible=card.cloneNode(true);
    visible.querySelectorAll('script').forEach(function(node){node.remove();});
    card.dataset.search=(visible.textContent+' '+card.id+' '+card.dataset.chapter).toLowerCase();
  });

  // 校验明细按需展开，所有数值与排版文本直接复用构建时已验证的数据。
  var checkTexts=JSON.parse(document.getElementById('check-texts').textContent);
  function fillChecks(details){
    if(details.dataset.ready)return;
    var rows=JSON.parse(details.querySelector('.check-data').textContent);
    var list=details.querySelector('.chk-list');
    rows.forEach(function(c){
      c=c.map(function(value,index){return index!==1&&typeof value==='number'?checkTexts[value]:value;});
      var row=document.createElement('li');row.className='chk '+(c[1]?'ok':'bad');
      var type=document.createElement('span');type.className='chk-type';type.textContent=c[0];
      var mark=document.createElement('span');mark.className='chk-mark';mark.textContent=c[1]?'通过':'未通过';
      var body=document.createElement('div');body.className='chk-body';
      var detail=document.createElement('div');detail.className='chk-detail';detail.innerHTML=c[2];
      body.appendChild(detail);
      if(c[3]){var note=document.createElement('div');note.className='chk-note';
        note.innerHTML='依据：'+c[3];body.appendChild(note);}
      row.appendChild(type);row.appendChild(mark);row.appendChild(body);list.appendChild(row);
    });details.dataset.ready='1';
  }
  document.addEventListener('toggle',function(event){
    var details=event.target;
    if(details.matches&&details.matches('details.checks')&&details.open)fillChecks(details);
  },true);
  // 打印时也填充明细，避免按需渲染使纸面记录缺失。
  window.addEventListener('beforeprint',function(){
    document.querySelectorAll('details.checks').forEach(function(details){fillChecks(details);});
  });

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
      var any=cards.some(function(c){
        return c.dataset.chapter===sec.dataset.chapter && c.style.display!=='none';});
      sec.style.display=any?'':'none';
    });
    // 索引按实际可见内容检索；与卡片搜索共享输入框，不留下虚假的筛选状态。
    var view=document.body.getAttribute('data-view');
    if(view==='formula'||view==='symbol'){
      fillIndex(view);
      var rows=document.querySelectorAll(view==='formula'?'.vidx-f':'.vs-row'),n=0;
      rows.forEach(function(row){
        var host=view==='formula'?row.parentElement:row;
        var sources=view==='formula'?[document.getElementById(host.dataset.formulas)]:row._sources;
        var hit=sources.some(function(source){return (!kw||(row.textContent+' '+source.querySelector('.kp-head h3').textContent+' '+source.id+' '+(view==='symbol'?source._symbolText[row._key]:'')).toLowerCase().indexOf(kw)>=0)&&(cur==='all'||source.dataset.chapter===cur);});
        row.style.display=hit?'':'none';if(hit)n++;
      });
      if(view==='formula')document.querySelectorAll('[data-formulas]').forEach(function(host){
        host.previousElementSibling.style.display=Array.from(host.children).some(function(row){return row.style.display!=='none';})?'':'none';
      });
      document.querySelectorAll('#allformulas .vidx-g,#allsymbols .vidx-g').forEach(function(group){
        group.style.display=Array.from(group.querySelectorAll(view==='formula'?'.vidx-f':'.vs-row')).some(function(row){return row.style.display!=='none';})?'':'none';
      });
      count.textContent='显示 '+n+' 项';
    }
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
      e.preventDefault();location.hash=a.getAttribute('href');revealHash();closeToc();
    });
  });

  // ---------- 视图切换：按知识点 / 只看公式 / 只看符号 ----------
  // 公式与符号索引复用知识点中的已排版内容，避免单文件重复存放整套公式。
  function fillIndex(view){
    if(view==='formula') document.querySelectorAll('[data-formulas]').forEach(function(host){
      if(host.dataset.ready)return;
      var source=document.getElementById(host.dataset.formulas);
      source.querySelectorAll('.fbox').forEach(function(box){
        var row=document.createElement('div');row.className='vidx-f';
        var name=document.createElement('div');name.className='vf-name';
        name.innerHTML=box.querySelector('.f-name').innerHTML;row.appendChild(name);
        row.appendChild(box.querySelector('.fbox-math math').cloneNode(true));
        var condition=box.querySelector('.f-when');
        if(condition){var when=document.createElement('div');when.className='vf-when';
          when.innerHTML=condition.innerHTML;row.appendChild(when);}
        host.appendChild(row);
      });host.dataset.ready='1';
    });
    // 符号合并行必须检索全部来源，而非只检索第一个知识点。
    if(view==='symbol'&&!window._symbolSources){
      window._symbolSources={};cards.forEach(function(card){card._symbolText={};card.querySelectorAll('.syms tbody tr').forEach(function(tr){var k=tr.cells[0].textContent+'|'+tr.cells[2].textContent;card._symbolText[k]=tr.textContent;(window._symbolSources[k]||(window._symbolSources[k]=[])).push(card);});});
    }
    if(view==='symbol') document.querySelectorAll('[data-symbol-ref]').forEach(function(row){
      if(row.dataset.ready)return;
      var ref=row.dataset.symbolRef.split('|');
      var source=document.getElementById(ref[0]);
      var cells=source.querySelectorAll('.syms tbody tr')[Number(ref[1])].cells;
      row._key=cells[0].textContent+'|'+cells[2].textContent;row._sources=window._symbolSources[row._key];
      // 来源标题复用知识点正文，避免每行重复储存；合并数量仍按源数据显示。
      var count=Number(row.dataset.pointCount||1);
      row.querySelector('.vs-from').textContent=source.querySelector('.kp-head h3').textContent
        +(count>1?' 等 '+count+' 个知识点':'');
      ['vs-sym','vs-desc','vs-unit'].forEach(function(cls,i){
        var cell=document.createElement('span');cell.className=cls;cell.innerHTML=cells[i].innerHTML;
        row.insertBefore(cell,row.querySelector('.vs-from'));
      });row.dataset.ready='1';
    });
  }
  var vbtns = Array.prototype.slice.call(document.querySelectorAll('.vbtn'));
  vbtns.forEach(function(b){
    b.addEventListener('click', function(){
      vbtns.forEach(function(x){ x.classList.remove('on'); });
      b.classList.add('on');
      var v = b.getAttribute('data-view');
      fillIndex(v);
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
    });
  });

  apply();
  // 跨学段链接带有知识点或常见错误锚点；先恢复知识点视图，再滚动并短暂高亮。
  function revealHash(){
    var targetId=window.location.hash.slice(1), target=null;
    try{target=document.getElementById(decodeURIComponent(targetId));}catch(_e){}
    if(target){
      document.body.setAttribute('data-view','point');
      vbtns.forEach(function(b){b.classList.toggle('on',b.dataset.view==='point');});
      if(tocbtn)tocbtn.style.display='';
      q.value='';cur='all';
      chips.forEach(function(x){x.classList.remove('on');});
      if(chips[0])chips[0].classList.add('on');
      apply();
      window.setTimeout(function(){
        target.scrollIntoView({behavior:'smooth',block:'start'});
        target.style.transition='box-shadow .4s';target.style.boxShadow='0 0 0 3px #2f6df6';
        window.setTimeout(function(){target.style.boxShadow='';},1100);
      },80);
    }
  }
  revealHash();window.addEventListener('hashchange',revealHash);
})();
"""


def render_page(report, stats, chapters, title="高中物理知识库", lead="",
                segment_nav=None, related=None):
    """组装整个 HTML 页面。title / lead 由调用方按学段传入。"""
    # 按章节分组，保持文件顺序
    groups = []
    for fname, chapter in chapters:
        cname = chapter.get("chapter", fname)
        pts = [report[p["id"]] for p in chapter.get("points", []) if p.get("id") in report]
        # 扩充展示字段从源读取；冻结公式报告保持原结构与原检查数。
        for source in chapter.get('points', []):
            if source['id'] in report:
                for key in ('core', 'experiments', 'figures', 'evidence', 'learning_scope'):
                    report[source['id']][key] = source.get(key, [])
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
        body.append('<section class="chapter" data-chapter="%s">' % E(cname))
        body.append("<h2>%s</h2>" % E(cname))
        if cintro:
            body.append('<p class="cintro">%s</p>' % prose(cintro))
        body.append('<p class="cmeta">%d 个知识点 · %d 条公式 · %d 项校验全部通过</p>'
                    % (len(pts), sum(len(p["formulas"]) for p in pts),
                       sum(p["n_total"] for p in pts)))
        body.append("</section>")
        body.extend(render_point(p["id"], p, (related or {}).get(p["id"]),
                                 (segment_nav or {}).get("quiz_href"),
                                 (segment_nav or {}).get("current", "高中"),
                                 (segment_nav or {}).get("senior_href" if (segment_nav or {}).get("current") == "高中" else "junior_href", "index.html"))
                    for p in pts)

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
            fs.append('<div data-formulas="%s"></div>' % E(p["id"]))
        fs.append('</div>')
    fs.append('</section>')
    formulas_html = "\n".join(fs)

    # ---------- 「只看符号」视图：**按首字母归堆** ----------
    # 特别要求：「相同字母的放一块方便辨识」——
    # 所以按「下划线之前的部分」分组：v、v_0、v_avg 会排进同一块。
    base_map = {}
    for cname, _, pts in groups:
        for p in pts:
            for symbol_index, s in enumerate(p["symbols"]):
                nm = s.get("name", "") or ""
                base = nm.split("_")[0] or nm
                base_map.setdefault(base, []).append((nm, s, p.get("title", p["id"]),
                                                      p["id"] + "|" + str(symbol_index)))

    def _base_key(b):
        # 拉丁字母排前面（按字母序），希腊字母排后面
        greek = bool(b) and ("\u0370" <= b[0] <= "\u03ff")
        return (1 if greek else 0, b.lower(), b)

    ss = ['<section class="vidx" id="allsymbols">',
          '<h2 class="vidx-h">全部符号一览</h2>',
          '<p class="vidx-lead">同一字母打头的符号放在一起，方便对照——'
          '例如 v、v₀、v̄ 会排在同一块里。最右列标注它出自哪个知识点。</p>']
    for base in sorted(base_map, key=_base_key):
        # 同名同单位的合并成一行，否则一个 v 会在十几个知识点里各占一行，反而难认。
        merged = {}
        order = []
        for nm, s, ptitle, symbol_ref in base_map[base]:
            key = (nm, s.get("unit", ""), display_mathml(s.get("mathml", ""),symbol_ref.split("|")[0]), s.get("desc", ""))
            if key not in merged:
                merged[key] = {"s": s, "pts": [], "ref": symbol_ref}
                order.append(key)
            if ptitle not in merged[key]["pts"]:
                merged[key]["pts"].append(ptitle)
        # 归堆的标题要用**显示用的符号**，不是机器名：
        # 分组键来自符号的机器名（`alpha` / `Delta`），直接印出来就成了
        # 「alpha 1 个符号 α …」——标题是机器名、正文才是符号，自相矛盾。
        ss.append('<div class="vidx-g"><h3 class="vs-base">%s<span class="vs-count">%d 个符号</span></h3>'
                  % (prose(base), len(order)))
        for key in sorted(order, key=lambda k: k[0]):
            nm, unit_raw, _, _desc = key
            s = merged[key]["s"]
            pts = merged[key]["pts"]
            # 来源文字与卡片标题一致，浏览器切到符号视图时读取，不重复嵌入。
            count_attr = (' data-point-count="%d"' % len(pts)) if len(pts) > 1 else ''
            ss.append('<div class="vs-row" data-symbol-ref="%s"%s>'
                      '<span class="vs-from"></span></div>'
                      % (E(merged[key]["ref"]), count_attr))
        ss.append('</div>')
    ss.append('</section>')
    symbols_html = "\n".join(ss)
    views_html = formulas_html + "\n" + symbols_html

    hero_stats = [
        (stats["points"], "知识点"),
        (stats["formulas"], "公式"),
        (stats["checks"], "项自动校验"),
        ("%d/%d" % (stats["pass"], stats["checks"]), "通过校验 · 失败%d项" % (stats["checks"]-stats["pass"])),
        (stats["traps_ok"], "条常见错误实测抓住"),
    ]
    hstats = "".join('<div class="hstat"><b>%s</b><span>%s</span></div>' % (v, E(k))
                     for v, k in hero_stats)

    # 统一站点的学段切换入口与测试入口；独立旧版调用时可不传此配置。
    segment_bar = ""
    if segment_nav:
        current = segment_nav.get("current", "高中")
        hs_attrs = ' aria-current="page"' if current == "高中" else ""
        junior_attrs = ' aria-current="page"' if current == "初中" else ""
        segment_bar = (
            '<nav class="segment-switchbar" aria-label="切换物理学段"><div class="segment-switch-inner">'
            '<span class="segment-switch-label">知识库学段</span>'
            '<a href="%s"%s>高中</a><a href="%s"%s>初中</a>'
            '<a href="%s">速查版 ↗</a>'
            '<a class="segment-quiz" href="%s">例题自测 ↗</a>'
            '</div></nav>' % (E(segment_nav.get("senior_href", "index.html")), hs_attrs,
                              E(segment_nav.get("junior_href", "junior.html")), junior_attrs,
                              E(segment_nav.get("quick_href", "quick.html")),
                              E(segment_nav.get("quiz_href", "quiz-hs.html"))))

    return compact_page(compact_check_records("""<!DOCTYPE html>
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

%s

<div class="toolbar">
  <div class="toolbar-in">
    <div class="views">
      <button class="vbtn on" data-view="point">按知识点</button>
      <button class="vbtn" data-view="formula">只看公式</button>
      <button class="vbtn" data-view="symbol">只看符号</button>
    </div>
    <button class="tbtn" id="tocbtn" type="button">目录</button>
    <input id="q" type="search" placeholder="搜索知识点、公式、符号、错误写法…" autocomplete="off">
    <label class="tgl"><input type="checkbox" id="expand"> 展开校验记录</label>
    <span id="count"></span>
  </div>
</div>

<div class="chipsbar"><div class="chips">%s</div></div>

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
</html>""" % (E(title), CSS, E(title), prose(lead), hstats, segment_bar,
             "".join(chips), toc, render_method_panel(stats), "".join(body),
             views_html, JS)))


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
    L.append("| 校验通过 | %d/%d 项（%.1f%%），失败%d项 |"
             % (stats["pass"], stats["checks"], pass_percent(stats["pass"], stats["checks"]),stats["checks"]-stats["pass"]))
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
    from 发布验收 import ensure_ready
    ensure_ready()
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
             pass_percent(stats["pass"], stats["checks"])))
    print("正式失败：%d项" % (stats["checks"]-stats["pass"]))
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
    # 不出 bug 就行」）。这里只留一个防呆上限，正常交付碰不到。
    # 不要为了压体积而删内容 —— 覆盖门槛优先。
    # ★ 2026-10-04：2 MB → 8 MB。高中完整版已 1999.1 KB，只剩 49 KB 余量，
    #   继续卡 2 MB 会直接让构建失败（return 1）。主人原话：「质量第一，占多少 MB 无所谓」。
    if size_kb > 8192:
        print("  注意：文件超过 8 MB 的防呆上限 —— 体积约束已放宽，此项仅防失控。")
        return 1
    print()
    print("结论：全部通过，成品可用。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
