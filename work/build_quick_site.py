# -*- coding: utf-8 -*-
"""
build_quick_site.py —— 生成面向学生与老师的“高中物理速查版”
================================================================

【为什么另做一版】

原来的“高中物理知识库”重点展示自动校验过程，适合作为项目技术证明；
速查版保留同一套已经校验过的内容，但把公式、符号、适用条件和常见错误放到最前面，
把推导与例题折叠起来。两个版本互不覆盖，使用者可以按需要选择。

【怎么运行】

    python build_quick_site.py

脚本只使用 Python 标准库和项目现有模块，不安装任何依赖。生成结果是：

    outputs/高中物理速查.html

它仍然是一个可以双击打开、断网使用的单 HTML 文件。
"""

import html
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from physkit import kb as KB
from prose_math import render as prose
from prose_math import display_mathml


def E(value):
    """转义普通文本，避免内容被浏览器误当成 HTML。"""
    return html.escape(str(value or ""), quote=True)


def math_block(mathml):
    """用浏览器原生 MathML 显示块级公式，保证断网也能排版。"""
    return ('<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">'
            + display_mathml(mathml or "") + '</math>')


def math_inline(mathml):
    """用于符号表中的行内公式。"""
    return ('<math xmlns="http://www.w3.org/1998/Math/MathML">'
            + display_mathml(mathml or "") + '</math>')


def plain(value):
    """把用于搜索的内容压成一行纯文本。"""
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(text.split())


# 章节按学习领域重新分组。这里只改变导航，不改变原知识库的章节和内容。
DOMAINS = [
    ("力学", 1, 6, "#2563eb"),
    ("电磁学", 7, 15, "#7c3aed"),
    ("光学", 16, 17, "#0891b2"),
    ("热学", 18, 19, "#d97706"),
    ("近代物理", 20, 21, "#db2777"),
]


def domain_for(order):
    for name, lo, hi, color in DOMAINS:
        if lo <= order <= hi:
            return name, color
    return "其他", "#475569"


def short_chapter(name):
    """导航里去掉“第几章”，只保留真正的章节名称。"""
    return name.split("·", 1)[-1].strip()


def build_groups(chapters, report):
    """把原始章节与通过校验的知识点合并成渲染需要的数据。"""
    groups = []
    point_map = {}
    for index, (_, chapter) in enumerate(chapters, start=1):
        order = int(chapter.get("order") or index)
        chapter_name = chapter.get("chapter") or ("第%d章" % order)
        domain, color = domain_for(order)
        points = [report[p["id"]] for p in chapter.get("points", [])
                  if p.get("id") in report]
        group = {
            "order": order,
            "chapter": chapter_name,
            "short": short_chapter(chapter_name),
            "intro": chapter.get("intro", ""),
            "domain": domain,
            "color": color,
            "points": points,
        }
        groups.append(group)
        for pos, point in enumerate(points):
            point_map[point["id"]] = (group, pos, point)
    return groups, point_map


def relation_links(point, group, pos, point_map):
    """显示前置与后续关系；源数据没填写时，用章节顺序给出温和的学习建议。"""
    before = list(point.get("prereq") or [])
    after = list(point.get("next") or [])
    inferred_before = False
    inferred_after = False
    if not before and pos > 0:
        before = [group["points"][pos - 1]["id"]]
        inferred_before = True
    if not after and pos + 1 < len(group["points"]):
        after = [group["points"][pos + 1]["id"]]
        inferred_after = True

    def render(ids, label, inferred):
        links = []
        for pid in ids:
            target = point_map.get(pid)
            if target:
                links.append('<button class="relation-link" data-go="%s">%s</button>'
                             % (E(pid), E(target[2]["title"])))
        if not links:
            return ""
        hint = '<span class="relation-hint">建议顺序</span>' if inferred else ""
        return '<div class="relation-row"><span>%s</span>%s%s</div>' % (
            E(label), hint, "".join(links))

    return render(before, "先理解", inferred_before) + render(after, "接着看", inferred_after)


def render_formula(formula):
    when = formula.get("when") or ""
    return '''
      <div class="formula-card">
        <div class="formula-name">%s</div>
        <div class="formula-math">%s</div>
        %s
      </div>''' % (
        prose(formula.get("name", "")),
        math_block(formula.get("mathml", "")),
        ('<div class="formula-when"><b>适用：</b>%s</div>' % prose(when)) if when else "",
    )


def render_symbols(symbols):
    rows = []
    for symbol in symbols:
        rows.append('''
          <div class="symbol-row">
            <span class="symbol-name">%s</span>
            <span class="symbol-desc">%s</span>
            <span class="symbol-unit">%s</span>
          </div>''' % (
            math_inline(symbol.get("mathml", "")),
            prose(symbol.get("desc", "")),
            prose(symbol.get("unit", "")) or "—",
        ))
    return "".join(rows)


def render_errors(errors):
    cards = []
    for error in errors:
        cards.append('''
          <div class="error-card">
            <div class="error-title">%s</div>
            <div class="error-why">%s</div>
          </div>''' % (prose(error.get("wrong", "")), prose(error.get("why", ""))))
    return "".join(cards)


def render_point(point, group, pos, point_map):
    """一个速查卡：核心信息直接展示，长内容放进折叠区。"""
    formulas = "".join(render_formula(f) for f in point.get("formulas", []))
    symbols = render_symbols(point.get("symbols", []))
    errors = render_errors(point.get("errors", []))
    derivation = "".join("<li>%s</li>" % prose(x) for x in point.get("derivation", []))
    example = point.get("example") or {}
    example_steps = "".join("<li>%s</li>" % prose(x) for x in example.get("solution", []))
    relations = relation_links(point, group, pos, point_map)

    search_parts = [
        point.get("id"), point.get("title"), group["chapter"], group["domain"],
        point.get("definition"), point.get("meaning"), " ".join(point.get("tags", [])),
    ]
    for f in point.get("formulas", []):
        search_parts.extend([f.get("name"), f.get("expr"), f.get("when")])
    for s in point.get("symbols", []):
        search_parts.extend([s.get("name"), s.get("desc"), s.get("unit")])
    for err in point.get("errors", []):
        search_parts.extend([err.get("wrong"), err.get("why")])

    return '''
    <article class="point-card searchable" id="%s" data-view-item="points"
      data-domain="%s" data-chapter="%s" data-search="%s">
      <div class="point-head">
        <div>
          <div class="point-path">%s · %s</div>
          <h2>%s</h2>
        </div>
        <span class="verified" title="本知识点已通过原知识库自动校验">✓ 已校验</span>
      </div>

      <p class="definition">%s</p>

      <section class="quick-section">
        <h3>核心公式</h3>
        <div class="formula-grid">%s</div>
      </section>

      <section class="quick-section">
        <h3>符号与单位</h3>
        <div class="symbol-table">
          <div class="symbol-row symbol-head"><span>符号</span><span>含义</span><span>单位</span></div>
          %s
        </div>
      </section>

      <section class="quick-section errors-section">
        <h3>容易出错</h3>
        <div class="error-list">%s</div>
      </section>

      %s

      <details class="deep-read">
        <summary>深入理解：物理意义、推导和例题</summary>
        <div class="deep-body">
          <section><h3>物理意义</h3><p>%s</p></section>
          %s
          %s
        </div>
      </details>
    </article>''' % (
        E(point["id"]), E(group["domain"]), E(group["chapter"]),
        E(plain(" ".join(str(x or "") for x in search_parts))),
        E(group["domain"]), E(group["short"]), E(point["title"]),
        prose(point.get("definition", "")), formulas, symbols, errors,
        ('<section class="relations"><h3>知识关系</h3>%s</section>' % relations) if relations else "",
        prose(point.get("meaning", "")),
        ('<section><h3>推导要点</h3><ol>%s</ol></section>' % derivation) if derivation else "",
        ('''<section class="example"><h3>典型例题</h3><p class="example-stem">%s</p>
             <ol>%s</ol><p class="example-answer"><b>答案：</b>%s</p></section>''' %
         (prose(example.get("stem", "")), example_steps, prose(example.get("answer", ""))))
        if example else "",
    )


def render_formula_index(groups):
    cards = []
    for group in groups:
        for point in group["points"]:
            for formula in point.get("formulas", []):
                search = plain(" ".join([
                    point["title"], formula.get("name", ""), formula.get("expr", ""),
                    formula.get("when", ""), group["chapter"], group["domain"],
                ]))
                cards.append('''
                <article class="index-card searchable" data-view-item="formulas"
                  data-domain="%s" data-chapter="%s" data-search="%s">
                  <button class="index-source" data-go="%s">%s · %s</button>
                  <h2>%s</h2>
                  <div class="index-math">%s</div>
                  <p><b>适用：</b>%s</p>
                </article>''' % (
                    E(group["domain"]), E(group["chapter"]), E(search), E(point["id"]),
                    E(group["short"]), E(point["title"]), prose(formula.get("name", "")),
                    math_block(formula.get("mathml", "")), prose(formula.get("when", "")),
                ))
    return "".join(cards)


def render_symbol_index(groups):
    """同名、同解释、同单位的符号合并，减少重复但不混淆不同含义。"""
    merged = {}
    for group in groups:
        for point in group["points"]:
            for symbol in point.get("symbols", []):
                key = (symbol.get("name", ""), symbol.get("desc", ""), symbol.get("unit", ""))
                if key not in merged:
                    merged[key] = {"symbol": symbol, "uses": []}
                merged[key]["uses"].append((point["id"], point["title"], group))

    def sort_key(item):
        name = item[0][0]
        return (name.split("_")[0].lower(), name.lower())

    rows = []
    for key, item in sorted(merged.items(), key=sort_key):
        symbol = item["symbol"]
        uses = item["uses"]
        first = uses[0]
        domains = " ".join(sorted(set(x[2]["domain"] for x in uses)))
        chapters = " ".join(sorted(set(x[2]["chapter"] for x in uses)))
        search = plain(" ".join([key[0], key[1], key[2], domains, chapters,
                                  " ".join(x[1] for x in uses)]))
        where = first[1] if len(uses) == 1 else "%s 等 %d 个知识点" % (first[1], len(uses))
        rows.append('''
          <article class="symbol-index-row searchable" data-view-item="symbols"
            data-domain="%s" data-chapter="%s" data-search="%s">
            <div class="big-symbol">%s</div>
            <div><h2>%s</h2><p>%s</p></div>
            <div class="unit-pill">%s</div>
            <button class="symbol-source" data-go="%s">%s</button>
          </article>''' % (
            E(first[2]["domain"]), E(first[2]["chapter"]), E(search),
            math_inline(symbol.get("mathml", "")), E(key[0]), prose(key[1]),
            prose(key[2]) or "无量纲", E(first[0]), E(where),
        ))
    return "".join(rows)


def render_map(groups):
    """生成从领域到章节、再到知识点的学习脉络图。"""
    sections = []
    for domain, _, _, color in DOMAINS:
        domain_groups = [g for g in groups if g["domain"] == domain]
        chapters = []
        for group in domain_groups:
            points = "".join(
                '<button class="map-point" data-go="%s">%s</button>' %
                (E(p["id"]), E(p["title"])) for p in group["points"])
            chapters.append('''
              <div class="map-chapter">
                <button class="map-chapter-title" data-chapter-jump="%s">
                  <span class="chapter-number">%02d</span>
                  <span>%s</span>
                  <span class="chapter-arrow">→</span>
                </button>
                <div class="map-points">%s</div>
              </div>''' % (E(group["chapter"]), group["order"], E(group["short"]), points))
        sections.append('''
          <section class="map-domain" data-map-domain="%s" style="--domain:%s">
            <div class="map-domain-head"><span class="map-dot"></span><h2>%s</h2>
              <span>%d 章 · %d 个知识点</span></div>
            <div class="map-path">%s</div>
          </section>''' % (
            E(domain), color, E(domain), len(domain_groups),
            sum(len(g["points"]) for g in domain_groups), "".join(chapters),
        ))
    return "".join(sections)


CSS = r'''
:root{--ink:#172033;--muted:#64748b;--line:#e5eaf1;--soft:#f6f8fb;--brand:#2457d6;--brand2:#173f9d;--warm:#fff7ed;--danger:#b45309;--ok:#15803d}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;overflow-x:hidden;color:var(--ink);background:#f7f8fa;font:15px/1.65 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
button,input,select{font:inherit}button{color:inherit}.appbar{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.96);backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
.appbar-in{max-width:1180px;min-width:0;margin:auto;padding:12px 22px;display:flex;align-items:center;gap:22px}.brand{display:flex;align-items:center;gap:11px;min-width:max-content}.brand-mark{display:grid;place-items:center;width:34px;height:34px;border-radius:11px;background:var(--brand);color:#fff;font-weight:800}.brand b{font-size:17px}.brand small{display:block;color:var(--muted);line-height:1.1}
.topnav{display:flex;min-width:0;gap:5px;margin-left:auto}.navbtn{border:0;background:transparent;padding:8px 12px;border-radius:9px;cursor:pointer;color:#526078}.navbtn:hover,.navbtn.on{background:#edf3ff;color:var(--brand);font-weight:700}.full-link{text-decoration:none;color:var(--muted);font-size:13px;border-left:1px solid var(--line);padding-left:16px}.full-link:hover{color:var(--brand)}
.intro{max-width:900px;margin:0 auto;padding:52px 22px 30px;text-align:center}.intro .kicker{color:var(--brand);font-weight:750;letter-spacing:.08em}.intro h1{font-size:clamp(32px,6vw,52px);letter-spacing:-.045em;margin:8px 0 6px;line-height:1.13}.intro p{color:var(--muted);font-size:17px;margin:0 auto 24px}.searchbox{display:flex;align-items:center;max-width:720px;margin:auto;background:#fff;border:1px solid #d8dee9;border-radius:16px;padding:4px 6px 4px 16px;box-shadow:0 10px 35px rgba(30,50,90,.08)}.searchbox span{font-size:19px;color:var(--muted)}.searchbox input{width:100%;border:0;outline:0;padding:13px 10px;background:transparent;font-size:16px}.searchbox kbd{background:var(--soft);color:var(--muted);border:1px solid var(--line);border-radius:7px;padding:3px 7px;font-size:12px}
.mini-stats{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin-top:17px}.mini-stats span{background:#fff;border:1px solid var(--line);border-radius:999px;padding:5px 11px;color:var(--muted);font-size:13px}.mini-stats b{color:var(--ink)}
.filters{position:sticky;top:59px;z-index:15;background:rgba(247,248,250,.96);border-bottom:1px solid var(--line)}.filters-in{max-width:1180px;margin:auto;padding:10px 22px;display:flex;align-items:center;gap:8px;overflow:auto}.domain-chip{white-space:nowrap;border:1px solid var(--line);background:#fff;border-radius:999px;padding:7px 13px;cursor:pointer}.domain-chip:hover,.domain-chip.on{border-color:#9bb5f1;background:#edf3ff;color:var(--brand);font-weight:700}.chapter-select{margin-left:auto;border:1px solid var(--line);background:#fff;border-radius:9px;padding:7px 10px;min-width:190px}.result-count{white-space:nowrap;color:var(--muted);font-size:13px}
main{max-width:1180px;margin:auto;padding:28px 22px 70px}.panel[hidden]{display:none!important}.point-list{display:grid;gap:22px;max-width:920px;margin:auto}.point-card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:25px 27px;box-shadow:0 6px 22px rgba(30,50,90,.04);scroll-margin-top:130px}.point-head{display:flex;justify-content:space-between;gap:16px}.point-path{font-size:13px;color:var(--brand);font-weight:700}.point-head h2{font-size:24px;margin:3px 0 0;line-height:1.3}.verified{align-self:start;white-space:nowrap;color:var(--ok);background:#ecfdf3;border:1px solid #c8f2d5;border-radius:999px;padding:4px 9px;font-size:12px}.definition{font-size:16px;color:#3f4b60;margin:15px 0 22px;padding-left:13px;border-left:3px solid #bfd0f8}.quick-section{margin-top:22px}.quick-section>h3,.relations>h3,.deep-body h3{font-size:14px;margin:0 0 10px;color:#506078}.formula-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}.formula-card{background:#f8faff;border:1px solid #e3eaff;border-radius:13px;padding:13px 15px}.formula-name{font-weight:700}.formula-math{font-size:21px;overflow:auto;padding:8px 0}.formula-math math{margin:0}.formula-when{color:var(--muted);font-size:13px;border-top:1px dashed #d8e1f3;padding-top:8px}.symbol-table{border:1px solid var(--line);border-radius:12px;overflow:hidden}.symbol-row{display:grid;grid-template-columns:100px 1fr 110px;align-items:center;min-height:42px;padding:7px 13px;border-top:1px solid var(--line)}.symbol-row:first-child{border-top:0}.symbol-head{background:var(--soft);font-size:12px;color:var(--muted);font-weight:700}.symbol-name{font-size:17px;font-weight:700}.symbol-unit{color:var(--muted)}.error-list{display:grid;gap:8px}.error-card{background:var(--warm);border:1px solid #fed7aa;border-left:4px solid #f59e0b;border-radius:10px;padding:11px 13px}.error-title{font-weight:750;color:#9a3412}.error-why{color:#6b4b35;margin-top:3px}.relations{margin-top:22px;border-top:1px solid var(--line);padding-top:18px}.relation-row{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:7px}.relation-row>span:first-child{color:var(--muted);font-size:13px;width:48px}.relation-hint{font-size:11px!important;width:auto!important;color:#94a3b8!important}.relation-link,.index-source,.symbol-source,.map-point{border:0;background:#edf3ff;color:var(--brand);border-radius:7px;padding:4px 8px;cursor:pointer}.relation-link:hover,.index-source:hover,.symbol-source:hover,.map-point:hover{text-decoration:underline}.deep-read{margin-top:20px;border-top:1px solid var(--line);padding-top:14px}.deep-read summary{cursor:pointer;color:var(--brand);font-weight:700}.deep-body{padding:10px 3px 0;color:#435066}.deep-body section{margin-top:18px}.deep-body ol{padding-left:22px}.example{background:var(--soft);border-radius:12px;padding:13px 16px}.example-answer{color:var(--brand2)}
.index-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}.index-card{background:#fff;border:1px solid var(--line);border-radius:15px;padding:18px}.index-card h2{font-size:16px;margin:11px 0}.index-source{font-size:12px}.index-math{font-size:21px;min-height:62px;display:grid;place-items:center;background:var(--soft);border-radius:10px;overflow:auto}.index-card p{color:var(--muted);font-size:13px;margin:11px 0 0}.symbol-index{display:grid;gap:8px;max-width:960px;margin:auto}.symbol-index-row{display:grid;grid-template-columns:90px 1fr 110px 180px;align-items:center;gap:14px;background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px 16px}.big-symbol{font-size:25px;text-align:center}.symbol-index-row h2{font-size:14px;margin:0;color:var(--muted)}.symbol-index-row p{margin:2px 0}.unit-pill{justify-self:start;background:var(--soft);border-radius:7px;padding:4px 8px;color:#506078}.symbol-source{text-align:left;background:transparent;color:var(--brand);font-size:12px}
.map-intro{text-align:center;max-width:760px;margin:0 auto 28px}.map-intro h2{font-size:27px;margin:0}.map-intro p{color:var(--muted)}.map-root{width:max-content;margin:0 auto 24px;background:var(--ink);color:#fff;border-radius:14px;padding:12px 22px;font-weight:800;position:relative}.map-root:after{content:"";position:absolute;top:100%;left:50%;height:25px;border-left:2px solid #cbd5e1}.map-domain{--domain:#2563eb;background:#fff;border:1px solid var(--line);border-left:5px solid var(--domain);border-radius:16px;padding:18px;margin:15px 0}.map-domain-head{display:flex;align-items:center;gap:10px}.map-domain-head h2{margin:0;font-size:20px}.map-domain-head>span:last-child{color:var(--muted);font-size:13px;margin-left:auto}.map-dot{width:12px;height:12px;border-radius:50%;background:var(--domain)}.map-path{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin-top:15px}.map-chapter{border:1px solid var(--line);border-radius:11px;overflow:hidden}.map-chapter-title{width:100%;border:0;background:#fafbfc;padding:10px;display:grid;grid-template-columns:34px 1fr 20px;text-align:left;align-items:center;cursor:pointer;font-weight:700}.chapter-number{color:var(--domain)}.chapter-arrow{color:#94a3b8}.map-points{padding:8px;display:flex;flex-wrap:wrap;gap:5px}.map-point{font-size:12px;background:#fff;border:1px solid var(--line);color:#526078;text-align:left}
.empty{display:none;text-align:center;padding:70px 20px;color:var(--muted)}footer{border-top:1px solid var(--line);background:#fff}.footer-in{max-width:1180px;margin:auto;padding:24px 22px;display:flex;justify-content:space-between;gap:18px;color:var(--muted);font-size:13px}.footer-in a{color:var(--brand);text-decoration:none}
@media(max-width:760px){html,body{width:100%;max-width:100%;overflow-x:hidden}.appbar,.filters,footer{width:100%;max-width:100vw}.appbar-in{width:100%;max-width:100vw;padding:9px 13px;gap:8px}.brand{flex:0 0 auto}.brand small,.full-link{display:none}.topnav{flex:1;min-width:0;max-width:100%;overflow-x:auto;overflow-y:hidden}.navbtn{flex:0 0 auto;padding:7px 9px;white-space:nowrap}.intro{width:100%;max-width:100vw;overflow:hidden;padding:34px 16px 22px}.intro h1{max-width:100%;font-size:clamp(29px,9vw,34px);white-space:normal;word-break:break-all;overflow-wrap:anywhere}.intro p{max-width:100%;word-break:break-all;overflow-wrap:anywhere}.searchbox{width:100%;max-width:100%;min-width:0}.searchbox input{min-width:0}.searchbox kbd{display:none}.mini-stats{max-width:100%;overflow:hidden}.mini-stats span{max-width:100%}.filters{top:53px}.filters-in{width:100%;max-width:100vw;padding:8px 13px}.chapter-select{flex:0 0 150px;min-width:150px;margin-left:8px}.result-count{display:none}main{width:100%;max-width:100vw;overflow:hidden;padding:18px 12px 55px}.point-list{width:100%;max-width:100%}.point-card{width:100%;max-width:100%;min-width:0;overflow:hidden;padding:19px 16px;border-radius:14px}.point-head h2{font-size:21px}.verified{display:none}.formula-grid{grid-template-columns:minmax(0,1fr)}.formula-card{min-width:0}.symbol-row{grid-template-columns:72px minmax(0,1fr) 76px;padding:7px 9px}.symbol-index-row{grid-template-columns:60px minmax(0,1fr) 76px}.symbol-source{grid-column:2/4}.map-domain{padding:14px 11px}.map-path{grid-template-columns:1fr}.footer-in{display:block}.footer-in span{display:block;margin-top:7px}}
@media print{.appbar,.filters,.intro,.deep-read,.footer-in{display:none!important}body{background:#fff}.point-card{box-shadow:none;break-inside:avoid}.point-list{max-width:none}main{padding:0}}
'''


JS = r'''
(function(){
  var currentView='points', currentDomain='全部', currentChapter='全部';
  var q=document.getElementById('q'), count=document.getElementById('count'), empty=document.getElementById('empty');
  var panels={points:document.getElementById('panel-points'),formulas:document.getElementById('panel-formulas'),symbols:document.getElementById('panel-symbols'),map:document.getElementById('panel-map')};
  var requestedView=location.hash.replace('#','');
  if(panels[requestedView]) currentView=requestedView;
  function norm(s){return (s||'').toLowerCase().replace(/\s+/g,' ').trim();}
  function apply(){
    Object.keys(panels).forEach(function(k){panels[k].hidden=k!==currentView;});
    document.querySelectorAll('.navbtn').forEach(function(b){b.classList.toggle('on',b.dataset.view===currentView);});
    var query=norm(q.value), shown=0;
    if(currentView==='map'){
      document.querySelectorAll('.map-domain').forEach(function(el){
        var ok=currentDomain==='全部'||el.dataset.mapDomain===currentDomain;
        el.style.display=ok?'':'none'; if(ok) shown++;
      });
    }else{
      panels[currentView].querySelectorAll('[data-view-item="'+currentView+'"]').forEach(function(el){
        var okDomain=currentDomain==='全部'||el.dataset.domain===currentDomain;
        var okChapter=currentChapter==='全部'||el.dataset.chapter===currentChapter;
        var okQuery=!query||norm(el.dataset.search).indexOf(query)>=0;
        var ok=okDomain&&okChapter&&okQuery;
        el.style.display=ok?'':'none'; if(ok) shown++;
      });
    }
    count.textContent=currentView==='map'?'知识脉络':('显示 '+shown+' 项');
    empty.style.display=shown?'none':'block';
  }
  document.querySelectorAll('.navbtn').forEach(function(btn){btn.addEventListener('click',function(){currentView=btn.dataset.view;history.replaceState(null,'','#'+currentView);apply();window.scrollTo({top:0,behavior:'smooth'});});});
  document.querySelectorAll('.domain-chip').forEach(function(btn){btn.addEventListener('click',function(){currentDomain=btn.dataset.domain;document.querySelectorAll('.domain-chip').forEach(function(x){x.classList.toggle('on',x===btn);});apply();});});
  document.getElementById('chapter').addEventListener('change',function(){currentChapter=this.value;apply();});
  q.addEventListener('input',apply);
  document.addEventListener('keydown',function(e){if(e.key==='/'&&document.activeElement!==q){e.preventDefault();q.focus();}});
  document.addEventListener('click',function(e){
    var go=e.target.closest('[data-go]');
    if(go){currentView='points';currentDomain='全部';currentChapter='全部';q.value='';document.getElementById('chapter').value='全部';document.querySelectorAll('.domain-chip').forEach(function(x){x.classList.toggle('on',x.dataset.domain==='全部');});apply();setTimeout(function(){var target=document.getElementById(go.dataset.go);if(target)target.scrollIntoView({behavior:'smooth',block:'start'});},30);}
    var jump=e.target.closest('[data-chapter-jump]');
    if(jump){currentView='points';currentChapter=jump.dataset.chapterJump;document.getElementById('chapter').value=currentChapter;apply();window.scrollTo({top:0,behavior:'smooth'});}
  });
  apply();
})();
'''


def render_page(groups, point_map, stats):
    points_html = "".join(
        render_point(point, group, pos, point_map)
        for group in groups for pos, point in enumerate(group["points"])
    )
    formulas_html = render_formula_index(groups)
    symbols_html = render_symbol_index(groups)
    map_html = render_map(groups)

    domain_buttons = ['<button class="domain-chip on" data-domain="全部">全部</button>']
    domain_buttons.extend('<button class="domain-chip" data-domain="%s">%s</button>' % (E(d[0]), E(d[0]))
                          for d in DOMAINS)
    chapter_options = ['<option value="全部">全部章节</option>']
    chapter_options.extend('<option value="%s">%02d · %s</option>' %
                           (E(g["chapter"]), g["order"], E(g["short"])) for g in groups)

    template = r'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="高中物理公式、符号、适用条件和常见错误速查工具">
<title>高中物理速查 · 谢端</title><style>__CSS__</style></head><body>
<header class="appbar"><div class="appbar-in">
  <div class="brand"><span class="brand-mark">物</span><div><b>高中物理速查</b><small>谢端</small></div></div>
  <nav class="topnav" aria-label="主要视图">
    <button class="navbtn on" data-view="points">知识点</button><button class="navbtn" data-view="formulas">公式</button>
    <button class="navbtn" data-view="symbols">符号</button><button class="navbtn" data-view="map">知识脉络</button>
  </nav>
  <a class="full-link" href="高中物理知识库.html">完整校验版 ↗</a>
</div></header>

<section class="intro"><div class="kicker">公式 · 符号 · 条件 · 易错点</div><h1>高中物理，快速查清楚</h1>
  <p>先找到要用的公式，再认清每个符号，最后避开最常见的错误。</p>
  <label class="searchbox"><span>⌕</span><input id="q" type="search" autocomplete="off" placeholder="搜索知识点、公式、符号或错误……"><kbd>/</kbd></label>
  <div class="mini-stats"><span><b>__POINTS__</b> 个知识点</span><span><b>__FORMULAS__</b> 条公式</span><span>内容来自已通过校验的完整知识库</span></div>
</section>

<div class="filters"><div class="filters-in">__DOMAIN_BUTTONS__
  <select class="chapter-select" id="chapter" aria-label="按章节筛选">__CHAPTER_OPTIONS__</select><span class="result-count" id="count"></span>
</div></div>

<main>
  <section class="panel point-list" id="panel-points">__POINTS_HTML__</section>
  <section class="panel index-grid" id="panel-formulas" hidden>__FORMULAS_HTML__</section>
  <section class="panel symbol-index" id="panel-symbols" hidden>__SYMBOLS_HTML__</section>
  <section class="panel" id="panel-map" hidden>
    <div class="map-intro"><h2>知识脉络</h2><p>从五个领域进入，沿章节顺序理解概念如何一层层建立。点击章节可回到对应速查卡，点击知识点可直接定位。</p></div>
    <div class="map-root">高中物理</div>__MAP_HTML__
  </section>
  <div class="empty" id="empty">没有找到匹配内容，试试更短的关键词。</div>
</main>

<footer><div class="footer-in"><span>高中物理速查 · 谢端　单文件、零依赖、断网可用</span>
  <span>需要推导与校验细节？<a href="高中物理知识库.html">打开完整校验版</a></span></div></footer>
<script>__JS__</script></body></html>'''
    replacements = {
        "__CSS__": CSS, "__JS__": JS,
        "__POINTS__": str(stats["points"]), "__FORMULAS__": str(stats["formulas"]),
        "__DOMAIN_BUTTONS__": "".join(domain_buttons),
        "__CHAPTER_OPTIONS__": "".join(chapter_options),
        "__POINTS_HTML__": points_html, "__FORMULAS_HTML__": formulas_html,
        "__SYMBOLS_HTML__": symbols_html, "__MAP_HTML__": map_html,
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def main():
    kb_dir = os.path.join(HERE, "kb")
    out_dir = os.path.abspath(os.path.join(HERE, "..", "outputs"))
    print("=" * 62)
    print("高中物理速查版 · 出成品")
    print("=" * 62)

    chapters = KB.load_kb(kb_dir)
    issues, id_map = KB.check_structure(chapters)
    errors = [x for x in issues if x.level == "错误"]
    if errors:
        for issue in errors:
            print("  %s" % issue)
        print("结构检查未通过，拒绝生成速查版。")
        return 1

    report = KB.run_physics_checks(chapters, id_map)
    stats = KB.summarize(report)
    bad = [p for p in report.values() if not p["ok"]]
    if bad or stats["trap_failures"]:
        print("物理校验未全部通过，拒绝生成速查版。")
        return 1

    groups, point_map = build_groups(chapters, report)
    page = render_page(groups, point_map, stats)

    # --- 速查版自己的完整性检查 ---
    # 物理内容已经由原流水线验证；这里再检查“展示层”有没有漏卡片、漏公式或断链。
    point_count = page.count('data-view-item="points"')
    formula_count = page.count('data-view-item="formulas"')
    symbol_count = page.count('data-view-item="symbols"')
    targets = set(re.findall(r'data-go="([^"]+)"', page))
    missing_targets = sorted(pid for pid in targets if pid not in point_map)
    leftovers = re.findall(r'__[A-Z_]+__', page)
    external_assets = re.findall(r'(?:src|href)="https?://', page, flags=re.I)
    if point_count != stats["points"] or formula_count != stats["formulas"]:
        print("展示完整性失败：知识点或公式数量与源数据不一致。")
        return 1
    if symbol_count == 0 or missing_targets or leftovers or external_assets:
        print("展示完整性失败：存在空符号表、断开的内部链接、未替换模板或外部资源。")
        if missing_targets:
            print("断开的知识点链接：%s" % ", ".join(missing_targets))
        return 1

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "高中物理速查.html")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(page)

    size_kb = os.path.getsize(path) / 1024
    print("校验：%d/%d 项通过" % (stats["pass"], stats["checks"]))
    print("内容：%d 章 / %d 个知识点 / %d 条公式" %
          (len(groups), stats["points"], stats["formulas"]))
    print("展示：%d 张知识卡 / %d 张公式卡 / %d 条合并符号，内部跳转全部有效" %
          (point_count, formula_count, symbol_count))
    print("已生成：%s（%.1f KB）" % (path, size_kb))
    if size_kb > 2048:
        print("文件超过 2 MB 防呆上限，拒绝交付。")
        return 1
    print("结论：速查版可用，原完整校验版未被修改。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
