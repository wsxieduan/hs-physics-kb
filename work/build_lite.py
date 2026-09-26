# -*- coding: utf-8 -*-
"""
build_lite.py —— 知识的「学生版」出口
=====================================

【它是什么】
    同一份内容源（work/kb/*.json），换一个渲染模板，产出一个**给学生用的速查版**。
    和 build_site.py（工程版）并列，互不影响。

【为什么要做成「一个源、多个出口」】
    主人的原话：「可以做老师版、学生版、简洁版等」。
    如果每做一版就复制一份内容，以后改一个公式要改四遍 —— 迟早不一致。
    正确做法是**内容源只有一份**，各个版本只是"给不同的人看不同的部分"。
    这和这个项目一贯的原则是同一个：**显示与数据解耦**。

【学生版砍掉了什么（相对于工程版）】
    · 全部校验记录（1535 条）—— 那是给人和审查看的证据，学生不需要
    · 物理意义 / 推导要点 —— 备课才用得上，收进「老师版」的考虑范围
    · 校验徽章、检查明细、抽样单 —— 一律不出现

【学生版保留了什么】
    · 一句话说清是什么          ← 定义的首句
    · 核心公式 + 适用条件        ← 公式框
    · 最容易错的那一条          ← 常见错误（优先取"量纲抓不住、只能靠代入"的那类）
    · 例题（默认收起，点开看）
    · 符号表（默认收起）
    · 符号速查视图、只看公式视图
"""

import os
import sys
import html
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from physkit import kb as KB
import build_site as BS                 # 已有 if __name__ 保护，导入无副作用
from prose_math import render as prose  # 字段文本的排版还原（机器写法 → 教材写法）

E = BS.E


def first_sentence(text, limit=120):
    """取定义的第一句，作为卡片上那句「一句话说清是什么」。
    太长的截断，避免一行占满屏。"""
    s = str(text or "").strip()
    for sep in ("。", "；", ";"):
        i = s.find(sep)
        if 0 < i <= limit:
            return s[:i + 1]
    return s[:limit] + ("…" if len(s) > limit else "")


def pick_error(p):
    """挑出「最容易错的那一条」。
    优先取「量纲检查抓不到、只能靠数值代入暴露」的那类 ——
    那才是学生真正会踩、而且自己不容易发现的坑。"""
    errs = p.get("errors") or []
    for e in errs:
        if e.get("caught_by") == "数值代入":
            return e
    return errs[0] if errs else None


def render_card(p, segment_nav=None):
    """一张知识点卡片：一屏看完。"""
    segment_nav = segment_nav or {}
    quiz_href = segment_nav.get("quiz_href")
    L = []
    L.append('<article class="kp" id="%s" data-chapter="%s" data-search="%s">'
             % (E(p["id"]), E(p.get("chapter", "")),
                E(" ".join([p.get("title", ""), p.get("definition", ""),
                            p.get("meaning", "")]))))

    # 标题行
    L.append('<div class="kp-head"><h3>%s</h3>'
             '<span class="kp-id">%s</span></div>'
             % (E(p.get("title", p["id"])), E(p["id"])))

    # 一句话
    L.append('<p class="one">%s</p>' % prose(first_sentence(p.get("definition", ""))))

    # 公式（带适用条件）
    for f in p.get("formulas", []):
        L.append('<div class="fbox">')
        L.append('<div class="f-name">%s</div>' % E(f.get("name", "")))
        L.append(BS.mathml_block(f.get("mathml", "")))
        if f.get("when"):
            L.append('<div class="f-when">适用：%s</div>' % prose(f["when"]))
        L.append('</div>')

    # 最容易错的那一条
    e = pick_error(p)
    if e:
        L.append('<div class="trap">')
        L.append('<div class="trap-t">最容易错</div>')
        L.append('<div class="trap-w">%s</div>' % prose(e.get("wrong", "")))
        if e.get("why"):
            L.append('<div class="trap-y">%s</div>' % prose(e["why"]))
        L.append('</div>')

    # 折叠：例题 / 符号
    ex = p.get("example") or {}
    if ex.get("stem"):
        L.append('<details><summary>看一道例题</summary>')
        L.append('<p class="stem">%s</p>' % prose(ex["stem"]))
        steps = ex.get("solution") or []
        if steps:
            L.append('<ol class="steps">%s</ol>'
                     % "".join("<li>%s</li>" % prose(s) for s in steps))
        if ex.get("answer"):
            L.append('<p class="ans">答：%s</p>' % prose(ex["answer"]))
        L.append('</details>')

    syms = p.get("symbols") or []
    if syms:
        # ★ 这里**故意不用 MathML**，改用文字形式的教材写法（prose 的输出，如 v̄）。
        #   原因：MathML 很啰嗦，一个符号要几十个字节；123 张卡片各带一份符号表，
        #   光是这一项就能占 200 KB 以上。而「符号速查」视图里已经有一套完整的
        #   数学排版了 —— 卡片里只需要「能认出这个符号叫什么」就够。
        L.append('<details><summary>看符号表（%d 个）</summary>' % len(syms))
        L.append('<table class="syms"><tbody>')
        for s in syms:
            L.append('<tr><td class="sym">%s</td><td>%s</td><td class="u">%s</td></tr>'
                     % (prose(s.get("name", "")), prose(s.get("desc", "")),
                        prose(s.get("unit", "")) or "—"))
        L.append('</tbody></table></details>')

    if quiz_href:
        # 练习链接用知识点 id 直达同一知识点的典型例题，并携带返回主知识库的位置。
        current = segment_nav.get("current", "高中")
        main_href = segment_nav.get(
            "senior_href" if current == "高中" else "junior_href",
            "index.html" if current == "高中" else "junior.html")
        query = "mode=example&point=%s&source=%s&return=%s" % (
            quote(p["id"]), quote(current + "物理学生版"), quote(main_href + "#" + p["id"]))
        target = "%s?%s" % (E(quiz_href), html.escape(query, quote=True))
        L.append('<div class="kp-practice"><a href="%s">练这道题 ↗</a></div>' % target)
    L.append('</article>')
    return "".join(L)


def render_formula_index(groups):
    """只看公式：全部公式按章节排列，方便纵向扫一遍。"""
    L = ['<section class="vidx" id="allformulas">',
         '<h2>全部公式</h2>',
         '<p class="lead">共 %d 条，按章节排列。</p>'
         % sum(len(p.get("formulas", [])) for _, _, pts in groups for p in pts)]
    for cname, _, pts in groups:
        L.append('<h3 class="ch">%s</h3>' % E(cname))
        for p in pts:
            L.append('<div class="vp">%s</div>' % E(p.get("title", p["id"])))
            for f in p.get("formulas", []):
                L.append('<div class="vf">%s%s</div>'
                         % (BS.mathml_block(f.get("mathml", "")),
                            ('<div class="vw">适用：%s</div>' % prose(f["when"])) if f.get("when") else ""))
    L.append('</section>')
    return "".join(L)


def render_symbol_index(groups):
    """符号速查：按「下划线之前的部分」分组，同名同单位合并成一行。"""
    base_map = {}
    for _, _, pts in groups:
        for p in pts:
            for s in p.get("symbols", []):
                base = (s.get("name", "") or "").split("_")[0] or s.get("name", "")
                base_map.setdefault(base, []).append((s, p.get("title", p["id"])))

    def key(b):
        greek = bool(b) and ("\u0370" <= b[0] <= "\u03ff")
        return (1 if greek else 0, b.lower(), b)

    L = ['<section class="vidx" id="allsymbols">',
         '<h2>符号速查</h2>',
         '<p class="lead">同一字母打头的排在一起，方便对照。同名同单位的合并成一行。</p>']
    for base in sorted(base_map, key=key):
        merged, order = {}, []
        for s, ptitle in base_map[base]:
            k = (s.get("name", ""), s.get("unit", ""))
            if k not in merged:
                merged[k] = {"s": s, "pts": []}
                order.append(k)
            if ptitle not in merged[k]["pts"]:
                merged[k]["pts"].append(ptitle)
        L.append('<h3 class="vsb">%s<span class="n">%d 个</span></h3>' % (E(base), len(order)))
        for k in sorted(order, key=lambda x: x[0]):
            nm, _u = k
            s = merged[k]["s"]
            pts = merged[k]["pts"]
            where = pts[0] if len(pts) == 1 else "%s 等 %d 处" % (pts[0], len(pts))
            L.append('<div class="vsrow"><span class="vss">%s</span>'
                     '<span class="vsd">%s</span><span class="vsu">%s</span>'
                     '<span class="vsf">%s</span></div>'
                     % (BS.mathml_inline(s.get("mathml", "")), prose(s.get("desc", "")),
                        prose(s.get("unit", "")) or "—", E(where)))
    L.append('</section>')
    return "".join(L)


CSS = """
*{box-sizing:border-box}
:root{--line:#e3e9f2;--ink:#1b2430;--ink2:#5c6b7e;--ink3:#94a2b6;--blue:#2f6df6;--red:#e5484d}
html,body{margin:0;padding:0}
body{font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  background:#f5f7fb;color:var(--ink);-webkit-text-size-adjust:100%}
header{background:#fff;border-bottom:1px solid var(--line);padding:18px 20px}
header h1{margin:0;font-size:19px;font-weight:600}
header p{margin:3px 0 0;font-size:13px;color:var(--ink2)}
.bar{position:sticky;top:0;z-index:9;background:#fff;border-bottom:1px solid var(--line);
  padding:10px 20px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
input[type=search]{flex:1;min-width:160px;font:inherit;font-size:14px;padding:7px 12px;
  border:1px solid var(--line);border-radius:999px;outline:none}
input[type=search]:focus{border-color:#b9c8e6}
.views{display:flex;gap:6px}
.views button,.chips button{font:inherit;font-size:13px;padding:6px 13px;border-radius:999px;
  border:1px solid var(--line);background:#fff;color:#3a4657;cursor:pointer}
.views button.on,.chips button.on{background:var(--blue);border-color:var(--blue);color:#fff}
.chips{display:flex;gap:6px;overflow-x:auto;padding:8px 20px 2px;background:#fff;-webkit-overflow-scrolling:touch}
.chips button{white-space:nowrap}
main{max-width:900px;margin:0 auto;padding:16px 16px 56px}
h1.ch{font-size:17px;margin:26px 0 8px;padding-bottom:6px;border-bottom:2px solid var(--blue);font-weight:600}
article.kp{background:#fff;border:1px solid var(--line);border-radius:12px;padding:15px 18px;margin:12px 0}
.kp-head{display:flex;align-items:baseline;gap:8px}
.kp-head h3{margin:0;font-size:16px;font-weight:600}
.kp-id{font-size:12px;color:var(--ink3);font-family:Consolas,monospace}
.one{margin:8px 0 12px;font-size:14.5px;color:#2b3745}
.fbox{border:1px solid var(--line);border-radius:10px;padding:9px 12px;margin:8px 0;background:#fbfcff}
.f-name{font-size:12.5px;color:var(--ink2);margin-bottom:2px}
.f-when{font-size:12.5px;color:var(--ink3);margin-top:2px}
.trap{border-left:3px solid var(--red);background:#fef6f6;border-radius:0 9px 9px 0;
  padding:9px 13px;margin:12px 0 4px}
.trap-t{font-size:12px;color:var(--red);font-weight:600;margin-bottom:3px}
.trap-w{font-size:14px;font-weight:500}
.trap-y{font-size:13px;color:var(--ink2);margin-top:4px}
details{margin-top:10px}
summary{cursor:pointer;font-size:13.5px;color:var(--blue);list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸ "}
details[open] summary::before{content:"▾ "}
.stem{margin:9px 0 6px;font-size:14px}
ol.steps{margin:0 0 6px 20px;padding:0;font-size:14px}
ol.steps li{margin:3px 0}
.ans{margin:6px 0 0;font-size:14px;font-weight:600;color:#0f7a3d}
table.syms{width:100%;border-collapse:collapse;margin-top:8px}
table.syms td{padding:5px 8px;border-bottom:1px solid var(--line);font-size:13.5px;vertical-align:middle}
td.sym{width:90px;text-align:center}
td.u{width:80px;color:var(--ink3);font-family:Consolas,monospace;font-size:12.5px;text-align:right}
.vidx{display:none}
body[data-view="formula"] #points-wrap,body[data-view="symbol"] #points-wrap{display:none}
body[data-view="point"] .vidx{display:none}
body[data-view="formula"] #allsymbols{display:none}
body[data-view="symbol"] #allformulas{display:none}
body:not([data-view="point"]) .chips{display:none}
.vidx h2{font-size:18px;margin:6px 0 4px}
.vidx .lead{color:var(--ink2);font-size:13.5px;margin:0 0 16px}
.vp{font-size:13.5px;color:var(--blue);font-weight:600;margin:14px 0 6px}
.vf{border:1px solid var(--line);border-radius:10px;padding:9px 12px;margin-bottom:8px;background:#fff}
.vw{font-size:12.5px;color:var(--ink3);margin-top:2px}
.vsb{font-size:15px;color:var(--blue);margin:18px 0 6px;font-weight:600}
.vsb .n{font-size:12px;color:var(--ink3);font-weight:400;margin-left:8px}
.vsrow{display:grid;grid-template-columns:92px 1fr 84px 150px;gap:10px;align-items:baseline;
  padding:5px 8px;border-radius:8px}
.vsrow:nth-child(odd){background:#fff}
.vss{text-align:center}
.vsd{font-size:13.5px}
.vsu{font-family:Consolas,monospace;font-size:12.5px;color:var(--ink3)}
.vsf{font-size:12px;color:var(--ink3);text-align:right}
@media(max-width:760px){ .vsrow{grid-template-columns:78px 1fr 70px} .vsf{display:none} }
.segment-switchbar{background:#fff;border-bottom:1px solid var(--line);padding:9px 16px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.segment-switchbar span{font-size:12px;color:var(--ink3)}
.segment-switchbar a{display:inline-block;padding:4px 11px;border:1px solid var(--line);border-radius:999px;color:var(--ink2);font-size:12px;text-decoration:none}
.segment-switchbar a[aria-current="page"]{background:var(--blue);border-color:var(--blue);color:#fff}
.segment-switchbar a.segment-quiz{margin-left:auto;border-color:#bdccf4;background:#f4f7ff;color:var(--blue)}
.kp-practice{margin-top:12px}.kp-practice a{display:inline-flex;padding:5px 12px;border:1px solid #bdccf4;border-radius:999px;color:var(--blue);font-size:12px;text-decoration:none}.kp-practice a:hover{background:#edf3ff}
footer{max-width:900px;margin:0 auto;padding:0 16px 40px;color:var(--ink3);font-size:12.5px;line-height:1.9}
"""


def build(kb_dir, out_path, segment_nav=None):
    chapters = KB.load_kb(kb_dir)
    issues, id_map = KB.check_structure(chapters)
    errs = [i for i in issues if i.level == "错误"]
    if errs:
        print("结构有错，拒绝出成品：")
        for i in errs:
            print("  ✘", i)
        return 1
    report = KB.run_physics_checks(chapters, id_map)

    groups = []
    for _fname, ch in chapters:
        pts = [report[p["id"]] for p in ch.get("points", []) if p.get("id") in report]
        if pts:
            groups.append((ch.get("chapter", ""), ch.get("intro", ""), pts))

    body, chips = [], ['<button class="on" data-chapter="all">全部</button>']
    for cname, cintro, pts in groups:
        body.append('<h1 class="ch">%s</h1>' % E(cname))
        if cintro:
            body.append('<p class="lead" style="color:#5c6b7e;font-size:13.5px;margin:0 0 6px">%s</p>'
                        % prose(cintro))
        body.extend(render_card(p, segment_nav) for p in pts)
        chips.append('<button data-chapter="%s">%s</button>'
                     % (E(cname), E(cname.split("·")[-1].strip())))

    n_pts = sum(len(p) for _, _, p in groups)
    n_fml = sum(len(p.get("formulas", [])) for _, _, p in groups for p in p)
    n_err = sum(len(p.get("errors", [])) for _, _, p in groups for p in p)

    page = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>高中物理 · 速查</title>
<style>%s</style></head>
<body data-view="point">
<header>
  <h1>高中物理 · 速查</h1>
  <p>%d 个知识点 · 一句话说清是什么 · 核心公式 · 最容易错的那条</p>
</header>
__SEGMENT_BAR__
<div class="bar">
  <div class="views">
    <button class="on" data-view="point">按知识点</button>
    <button data-view="formula">全部公式</button>
    <button data-view="symbol">符号速查</button>
  </div>
  <input id="q" type="search" placeholder="搜知识点、公式、符号…" autocomplete="off">
</div>
<div class="chips">%s</div>
<main>
  <div id="points-wrap">%s</div>
  %s
  %s
  <div id="empty" style="display:none;padding:30px;text-align:center;color:#94a2b6">没有匹配的知识点。</div>
</main>
<footer>
  <p><b>这是什么</b>　高中物理的速查版：%d 个知识点、%d 条公式、%d 条学生最容易踩的错误。
  每一条都配「一句话说清是什么 + 核心公式 + 最容易错的那条」，扫一眼就有用。</p>
  <p><b>怎么用</b>　单文件，双击就开，不用联网、不用装东西，手机上也能看。</p>
</footer>
<script>
(function(){
  var q=document.getElementById('q'), chips=document.querySelectorAll('.chips button'),
      cards=document.querySelectorAll('.kp'), cur='all';
  function apply(){
    var kw=(q.value||'').trim().toLowerCase(), n=0;
    Array.prototype.forEach.call(cards,function(c){
      var okKw=!kw||(c.getAttribute('data-search')||'').toLowerCase().indexOf(kw)>=0;
      var okCh=cur==='all'||c.getAttribute('data-chapter')===cur;
      var vis=okKw&&okCh; c.style.display=vis?'':'none'; if(vis) n++;
    });
    document.getElementById('empty').style.display = n?'none':'block';
    Array.prototype.forEach.call(document.querySelectorAll('h1.ch'),function(h){
      var any=false, el=h.nextElementSibling;
      while(el && el.tagName!=='H1'){ if(el.classList.contains('kp')&&el.style.display!=='none') any=true; el=el.nextElementSibling; }
      h.style.display=any?'':'none';
    });
  }
  Array.prototype.forEach.call(chips,function(b){
    b.addEventListener('click',function(){
      Array.prototype.forEach.call(chips,function(x){x.classList.remove('on');});
      b.classList.add('on'); cur=b.getAttribute('data-chapter'); apply();
    });
  });
  q.addEventListener('input',apply);
  Array.prototype.forEach.call(document.querySelectorAll('.views button'),function(b){
    b.addEventListener('click',function(){
      Array.prototype.forEach.call(document.querySelectorAll('.views button'),function(x){x.classList.remove('on');});
      b.classList.add('on');
      var v=b.getAttribute('data-view');
      document.body.setAttribute('data-view',v);
      if(v!=='point'){ cur='all';
        Array.prototype.forEach.call(chips,function(x){x.classList.remove('on');});
        if(chips[0]) chips[0].classList.add('on'); }
      apply();
    });
  });
  apply();
})();
</script>
</body></html>""" % (CSS, n_pts, "".join(chips), "".join(body),
                       render_formula_index(groups), render_symbol_index(groups),
                       n_pts, n_fml, n_err)

    if segment_nav:
        current = segment_nav.get("current", "高中")
        hs_attrs = ' aria-current="page"' if current == "高中" else ""
        junior_attrs = ' aria-current="page"' if current == "初中" else ""
        segment_bar = (
            '<nav class="segment-switchbar" aria-label="切换物理学段">'
            '<span>知识库学段</span><a href="%s"%s>高中</a><a href="%s"%s>初中</a>'
            '<a class="segment-quiz" href="%s">例题自测与错误诊断 ↗</a></nav>' % (
                E(segment_nav.get("senior_href", "index.html")), hs_attrs,
                E(segment_nav.get("junior_href", "junior.html")), junior_attrs,
                E(segment_nav.get("quiz_href", "quiz-hs.html"))))
    else:
        segment_bar = ""
    page = page.replace("__SEGMENT_BAR__", segment_bar)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    kb_size = os.path.getsize(out_path) / 1024
    print("已生成学生版：%s（%.1f KB）" % (out_path, kb_size))
    print("  知识点 %d · 公式 %d · 常见错误 %d · 校验记录 0（刻意不带）"
          % (n_pts, n_fml, n_err))
    return 0


def main(argv):
    kb_dir = os.path.join(HERE, "kb")
    out_path = os.path.abspath(os.path.join(HERE, "..", "outputs", "高中物理知识库-学生版.html"))
    if len(argv) > 1:
        kb_dir = argv[1]
    if len(argv) > 2:
        out_path = argv[2]
    print("=" * 62)
    print("高中物理知识库 · 出「学生版」（速查）")
    print("=" * 62)
    return build(kb_dir, out_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
