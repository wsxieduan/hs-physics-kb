# -*- coding: utf-8 -*-
"""从同一份已校验知识库生成学生、老师都能使用的离线速查版。

运行：在 work 目录执行 python build_quick_site.py。
四个界面在浏览器里按需生成，内容只在成品中保存一份；工程版由原脚本维护。
"""

import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from physkit import kb as KB
from prose_math import render as base_prose, display_mathml


DOMAINS = (
    ("力学", 1, 6),
    ("电磁学", 7, 15),
    ("光学", 16, 17),
    ("热学", 18, 19),
    ("近代物理", 20, 21),
)
JUNIOR_DOMAINS = {
    1: "声学", 2: "光学", 3: "光学", 4: "热学", 5: "力学", 6: "力学",
    7: "力学", 8: "力学", 9: "力学", 10: "物质", 11: "力学", 12: "能量",
    13: "电学", 14: "电学", 15: "电学", 16: "电学", 17: "电磁波", 18: "能源",
}


def prose(value):
    """补齐通用正文排版器遗漏的三种写法，只影响速查版的展示。"""
    rendered = base_prose(value)
    return (rendered
            .replace("k_e", "k<sub>e</sub>")
            .replace("IgR<sub>g</sub>", "I<sub>g</sub>R<sub>g</sub>")
            .replace("Δ<sub>E</sub>_total", "ΔE<sub>总</sub>"))


def first_sentence(text, limit=140):
    """卡片先给出一句定义，避免首屏被长段落占满。"""
    value = str(text or "").strip()
    for mark in ("。", "；", ";"):
        pos = value.find(mark)
        if 0 < pos <= limit:
            return value[:pos + 1]
    return value[:limit] + ("…" if len(value) > limit else "")


def domain_of(order, segment="高中"):
    if segment == "初中":
        return JUNIOR_DOMAINS.get(order, "其他")
    for name, lo, hi in DOMAINS:
        if lo <= order <= hi:
            return name
    return "其他"


def readable_mathml(mathml):
    """把解析器内部的复合下标换成学生能读懂的中文标记。"""
    rendered = display_mathml(mathml or "")
    rendered = rendered.replace(
        '<msub><mi>Δ</mi><mi mathvariant="normal">E_total</mi></msub>',
        '<msub><mi>ΔE</mi><mi mathvariant="normal">总</mi></msub>',
    )
    for internal, readable in (("mech_before", "机械前"),
                               ("mech_after", "机械后"),
                               ("int_gain", "内能增")):
        rendered = rendered.replace(
            '<mi mathvariant="normal">%s</mi>' % internal,
            '<mi mathvariant="normal">%s</mi>' % readable,
        )
    return rendered


def make_payload(chapters, report, segment="高中"):
    """仅收取给人看的字段；公式和符号各存一次，不带1535条校验明细。"""
    groups = []
    points = []
    for fallback, (_, chapter) in enumerate(chapters, 1):
        order = int(chapter.get("order") or fallback)
        chapter_name = chapter.get("chapter") or ("第%d章" % order)
        chapter_ids = []
        for source in chapter.get("points", []):
            point = report[source["id"]]
            if not point["ok"]:
                raise ValueError("知识点未通过物理校验：" + point["id"])

            formulas = [
                [prose(f.get("name", "")), readable_mathml(f.get("mathml", "")),
                 prose(f.get("when", ""))]
                for f in point.get("formulas", [])
            ]
            symbols = [
                [prose(s.get("name", "")), prose(s.get("desc", "")),
                 prose(s.get("unit", "")) or "—"]
                for s in point.get("symbols", [])
            ]
            errors_source = point.get("errors", [])
            errors_source = sorted(
                errors_source,
                key=lambda error: 0 if error.get("caught_by") == "数值代入" else 1,
            )
            errors = [
                [prose(e.get("wrong", "")), prose(e.get("why", ""))]
                for e in errors_source
            ]
            example = point.get("example") or {}
            # 数组比重复写 JSON 字段名更小；浏览器里的注释解释每个位置。
            item = [
                point["id"], prose(point["title"]), prose(first_sentence(point.get("definition"))),
                formulas, symbols, errors, prose(point.get("meaning", "")),
                [prose(x) for x in point.get("derivation", [])],
                [prose(example.get("stem", "")),
                 [prose(x) for x in example.get("solution", [])],
                 prose(example.get("answer", ""))],
                list(point.get("prereq") or []), list(point.get("next") or []),
                [prose(x) for x in point.get("tags", [])], len(groups),
            ]
            points.append(item)
            chapter_ids.append(len(points) - 1)
        groups.append([chapter_name, domain_of(order, segment), order, chapter_ids])
    return {"g": groups, "p": points}


CSS = r'''
:root{--ink:#172033;--muted:#617087;--line:#e5eaf1;--brand:#2457d6;--soft:#f7f9fc;--error:#9a3412}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f7f8fa;color:var(--ink);font:15px/1.65 system-ui,-apple-system,"Microsoft YaHei",sans-serif}button,input,select{font:inherit}button{cursor:pointer}a{color:var(--brand)}
.appbar{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid var(--line)}.appbar-inner{max-width:1120px;margin:auto;padding:11px 18px;display:flex;align-items:center;gap:24px}.brand{white-space:nowrap;font-size:17px;font-weight:750}.brand i{display:inline-grid;place-items:center;width:32px;height:32px;margin-right:8px;border-radius:10px;background:var(--brand);color:#fff;font-style:normal}.nav{display:flex;gap:5px;margin-left:auto}.nav button{border:0;background:none;border-radius:9px;padding:7px 11px;color:#4f5c72;white-space:nowrap}.nav button.active,.nav button:hover{background:#edf3ff;color:var(--brand);font-weight:700}.old-link{font-size:13px;text-decoration:none;white-space:nowrap;border-left:1px solid var(--line);padding-left:15px}
.intro{text-align:center;max-width:820px;margin:auto;padding:42px 16px 29px}.eyebrow{font-weight:700;color:var(--brand);font-size:13px;letter-spacing:.08em}.intro h1{font-size:clamp(30px,5vw,47px);line-height:1.2;margin:7px 0 6px}.intro p{color:var(--muted);font-size:16px;margin:0 0 22px}.search{display:flex;align-items:center;gap:9px;text-align:left;background:#fff;border:1px solid #d4deee;border-radius:14px;padding:0 14px;box-shadow:0 8px 28px #1e32520b}.search span{color:var(--brand);font-size:21px}.search input{border:0;outline:0;width:100%;min-width:0;padding:13px 2px;background:transparent}.intro .counts{color:var(--muted);font-size:13px;margin-top:10px}
.filters{position:sticky;top:55px;z-index:15;background:#f7f8faed;border-bottom:1px solid var(--line)}.filters-inner{max-width:1120px;margin:auto;padding:10px 18px;display:flex;gap:7px;align-items:center;overflow-x:auto}.filter{border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 12px;white-space:nowrap}.filter.active{color:var(--brand);background:#edf3ff;border-color:#b3c6f5;font-weight:700}.filters select{margin-left:auto;background:#fff;border:1px solid var(--line);border-radius:8px;padding:6px 9px;min-width:170px}.count{font-size:12px;color:var(--muted);white-space:nowrap}
main{max-width:1120px;margin:auto;padding:24px 16px 70px}.list{max-width:900px;margin:auto;display:grid;gap:16px}.card,.index-card,.symbol-card,.map-domain{background:#fff;border:1px solid var(--line);border-radius:15px;box-shadow:0 4px 14px #1e325208}.card{padding:21px 24px;scroll-margin-top:125px}.path{font-size:12px;font-weight:700;color:var(--brand)}.card h2{font-size:22px;line-height:1.35;margin:3px 0 10px}.definition{border-left:3px solid #bed0f8;padding-left:12px;margin:0 0 18px;color:#3c4a61}.block{margin-top:17px}.block h3,.relations h3{font-size:13px;color:#52617a;margin:0 0 8px}.formulas{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:8px}.formula{min-width:0;padding:10px 12px;border:1px solid #dfebff;background:#f8faff;border-radius:10px}.formula-name{font-weight:700;font-size:13px}.math{overflow-x:auto;font-size:21px;text-align:center;padding:6px 0}.math math{margin:auto}.when{border-top:1px dashed #d4e1f8;padding-top:6px;font-size:12px;color:var(--muted)}.symbols{border:1px solid var(--line);border-radius:9px;overflow:hidden}.sym-row{display:grid;grid-template-columns:80px minmax(0,1fr) 90px;gap:8px;border-top:1px solid var(--line);padding:5px 10px;font-size:13px}.sym-row:first-child{border:0}.sym-row.head{background:var(--soft);color:var(--muted);font-weight:700}.sym-name{font-size:16px}.sym-unit{color:var(--muted)}.error{border-left:3px solid #f59e0b;background:#fff8ed;border-radius:7px;padding:8px 10px;margin-top:6px}.error b{color:var(--error)}.error p{margin:3px 0 0;color:#6b4b35;font-size:13px}.relations{margin-top:17px}.relation-line{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:5px}.relation-line span{font-size:12px;color:var(--muted)}.relation-line button,.source,.map-point{border:0;border-radius:6px;padding:3px 7px;background:#edf3ff;color:var(--brand);font-size:12px}.relation-line button:hover,.source:hover,.map-point:hover{text-decoration:underline}.deep{margin-top:17px;border-top:1px solid var(--line);padding-top:11px}.deep summary{color:var(--brand);font-weight:700;cursor:pointer}.deep-content{color:#435066}.deep-content h3{font-size:14px;margin:15px 0 4px}.deep-content p{margin:0}.deep-content ol{padding-left:21px;margin:5px 0}.deep-content li{margin:4px 0}.answer{color:#1d4d9b;font-weight:700}
.index-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:12px}.index-card{padding:15px}.index-card h2{font-size:16px;margin:7px 0}.index-card .when{border-top:0;margin:4px 0 0}.symbol-grid{display:grid;gap:7px;max-width:900px;margin:auto}.symbol-card{display:grid;grid-template-columns:100px minmax(0,1fr) 100px 160px;gap:10px;align-items:center;padding:9px 13px}.symbol-card .sym-name{font-size:20px}.symbol-card p{margin:0}.symbol-card .source{text-align:left;background:none}.map-title{text-align:center;margin:0 0 20px}.map-title h2{margin:0}.map-title p{color:var(--muted);margin:5px 0}.map-root{width:max-content;margin:0 auto 20px;border-radius:10px;background:var(--ink);color:#fff;padding:9px 18px;font-weight:700}.map-domain{padding:16px;margin:12px 0;border-left:4px solid var(--brand)}.map-domain h2{font-size:19px;margin:0 0 10px}.map-domain h2 small{color:var(--muted);font-weight:400;font-size:12px;margin-left:10px}.map-chapters{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px}.map-chapter{border:1px solid var(--line);border-radius:9px;padding:10px}.map-chapter button:first-child{width:100%;text-align:left;border:0;background:none;font-weight:700;padding:0 0 6px}.map-chapter button:first-child:hover{color:var(--brand)}.map-points{display:flex;flex-wrap:wrap;gap:4px}.map-point{background:#fff;border:1px solid var(--line);color:#4d5b71}.empty{text-align:center;color:var(--muted);padding:60px 0}footer{background:#fff;border-top:1px solid var(--line);padding:20px;text-align:center;color:var(--muted);font-size:12px}
@media(max-width:650px){.appbar-inner{gap:7px;padding:8px 10px}.brand{font-size:15px}.brand i{width:27px;height:27px;margin-right:5px}.nav{min-width:0;overflow-x:auto}.nav button{padding:6px 8px}.old-link{display:none}.intro{padding:30px 14px 20px}.intro h1{font-size:32px}.filters{top:44px}.filters-inner{padding:8px 10px}.filters select{min-width:145px}.count{display:none}main{padding:13px 10px 55px}.card{padding:16px 13px}.card h2{font-size:20px}.formulas{grid-template-columns:minmax(0,1fr)}.sym-row{grid-template-columns:65px minmax(0,1fr) 68px;padding:5px 7px}.symbol-card{grid-template-columns:70px minmax(0,1fr) 75px}.symbol-card .source{grid-column:2/4}.map-chapters{grid-template-columns:1fr}}
.segment-switchbar{background:#fff;border-bottom:1px solid var(--line);padding:8px 18px}.segment-switchbar-inner{max-width:1120px;margin:auto;display:flex;align-items:center;gap:8px;flex-wrap:wrap}.segment-switchbar span{font-size:12px;color:var(--muted)}.segment-switchbar a{border:1px solid var(--line);border-radius:999px;padding:4px 11px;font-size:12px;text-decoration:none}.segment-switchbar a[aria-current="page"]{background:var(--brand);border-color:var(--brand);color:#fff}.segment-switchbar a.segment-quiz{margin-left:auto;background:#f4f7ff;border-color:#bdccf4}
.card-practice{margin-top:12px}.card-practice a{display:inline-flex;border:1px solid #bdccf4;border-radius:999px;padding:5px 12px;color:var(--brand);font-size:12px}.card-practice a:hover{background:#edf3ff;text-decoration:none}
'''


# 数据数组的索引：知识点 [编号,标题,一句定义,公式,符号,错误,物理意义,推导,例题,前置,后续,标签,章节序号]。
# 公式 [名称,MathML,适用条件]；符号 [教材写法,含义,单位]。所有文本已经由 Python 安全转义。
JS = r'''
(()=>{'use strict';
const data=JSON.parse(document.getElementById('kb-data').textContent),groups=data.g,points=data.p;
const SEGMENT_ROOT='__SEGMENT_ROOT__',QUIZ_HREF='__QUIZ_HREF__',RETURN_PAGE='__RETURN_PAGE__';
const viewBox=document.getElementById('content'),search=document.getElementById('search'),chapter=document.getElementById('chapter'),counter=document.getElementById('count');
const strip=s=>{const e=document.createElement('div');e.innerHTML=s||'';return(e.textContent||'').toLowerCase()};
const clean=s=>(s||'').toLowerCase().replace(/\s+/g,' ').trim();
const text=s=>{const e=document.createElement('span');e.textContent=s||'';return e.innerHTML};
const groupName=i=>groups[i][0].split('·').slice(1).join('·').trim()||groups[i][0];
let view=['points','formulas','symbols','map'].includes(location.hash.slice(1))?location.hash.slice(1):'points';
let domain='全部',chapterName='全部',query='';
const pointIndex=new Map(points.map((p,i)=>[p[0],i]));

// 搜索只从给人看的字段建立索引，不把公式解析器的内部名称重新写进页面。
const pointSearch=points.map(p=>clean([p[1],p[2],p[6],p[11].join(' '),groups[p[12]][0],groups[p[12]][1],...p[3].flat(),...p[4].flat(),...p[5].flat()].map(strip).join(' ')));
const formulas=[];points.forEach((p,i)=>p[3].forEach((f,k)=>formulas.push([i,k,clean([strip(p[1]),strip(f[0]),strip(f[2]),...p[4].map(s=>strip(s[0])+' '+strip(s[1])),groups[p[12]][0]].join(' '))])));
const symbols=[],symbolMap=new Map();points.forEach((p,i)=>p[4].forEach(s=>{const key=s.join('|');if(!symbolMap.has(key)){symbolMap.set(key,symbols.length);symbols.push([s,[i]])}else symbols[symbolMap.get(key)][1].push(i)}));

function matchPoint(i){const p=points[i],g=groups[p[12]];return(domain==='全部'||g[1]===domain)&&(chapterName==='全部'||g[0]===chapterName)&&(!query||pointSearch[i].includes(query))}
function formulaHTML(f){return '<div class="formula"><div class="formula-name">'+f[0]+'</div><div class="math"><math xmlns="http://www.w3.org/1998/Math/MathML" display="block">'+f[1]+'</math></div>'+(f[2]?'<div class="when">适用：'+f[2]+'</div>':'')+'</div>'}
function symbolHTML(s){return '<div class="sym-row"><div class="sym-name">'+s[0]+'</div><div>'+s[1]+'</div><div class="sym-unit">'+s[2]+'</div></div>'}
function errorHTML(e){return '<div class="error"><b>'+e[0]+'</b><p>'+e[1]+'</p></div>'}
function relationHTML(p,i){let before=p[9].map(id=>pointIndex.get(id)).filter(x=>x!==undefined),after=p[10].map(id=>pointIndex.get(id)).filter(x=>x!==undefined);const members=groups[p[12]][3],pos=members.indexOf(i);if(!before.length&&pos>0)before=[members[pos-1]];if(!after.length&&pos<members.length-1)after=[members[pos+1]];const row=(label,list)=>list.length?'<div class="relation-line"><span>'+label+'</span>'+list.map(j=>'<button data-go="'+j+'">'+points[j][1]+'</button>').join('')+'</div>':'';return before.length||after.length?'<section class="relations"><h3>知识关系</h3>'+row('先理解',before)+row('接着看',after)+'</section>':''}
function pointHTML(i){const p=points[i],g=groups[p[12]],more=p[5].length>2?'<details class="more-errors"><summary>查看其余 '+(p[5].length-2)+' 条常见错误</summary>'+p[5].slice(2).map(errorHTML).join('')+'</details>':'';return '<article class="card" id="'+p[0]+'"><div class="path">'+g[1]+' · '+text(groupName(p[12]))+'</div><h2>'+p[1]+'</h2><p class="definition">'+p[2]+'</p><section class="block"><h3>核心公式</h3><div class="formulas">'+p[3].map(formulaHTML).join('')+'</div></section><section class="block"><h3>容易出错</h3>'+p[5].slice(0,2).map(errorHTML).join('')+more+'</section><section class="block"><h3>符号与单位</h3><div class="symbols"><div class="sym-row head"><div>符号</div><div>含义</div><div>单位</div></div>'+p[4].map(symbolHTML).join('')+'</div></section>'+relationHTML(p,i)+'<details class="deep" data-deep="'+i+'"><summary>深入理解：物理意义、推导和例题</summary><div class="deep-content"></div></details><div class="card-practice"><a href="'+QUIZ_HREF+'?mode=example&amp;point='+encodeURIComponent(p[0])+'&amp;source='+encodeURIComponent(SEGMENT_ROOT+'速查版')+'&amp;return='+encodeURIComponent(RETURN_PAGE+'#'+p[0])+'">练这道题 ↗</a></div></article>'}
function deepHTML(p){const ex=p[8];return '<h3>物理意义</h3><p>'+p[6]+'</p>'+(p[7].length?'<h3>推导要点</h3><ol>'+p[7].map(x=>'<li>'+x+'</li>').join('')+'</ol>':'')+(ex[0]?'<h3>典型例题</h3><p>'+ex[0]+'</p><ol>'+ex[1].map(x=>'<li>'+x+'</li>').join('')+'</ol><p class="answer">答案：'+ex[2]+'</p>':'')}
function renderPoints(){const ids=points.map((_,i)=>i).filter(matchPoint);viewBox.className='list';viewBox.innerHTML=ids.map(pointHTML).join('');return ids.length}
function renderFormulas(){const rows=formulas.filter(row=>matchPoint(row[0])&&(!query||row[2].includes(query)));viewBox.className='index-grid';viewBox.innerHTML=rows.map(([i,k])=>'<article class="index-card"><button class="source" data-go="'+i+'">'+points[i][1]+'</button><h2>'+points[i][3][k][0]+'</h2>'+formulaHTML(points[i][3][k])+'</article>').join('');return rows.length}
function renderSymbols(){const rows=symbols.filter(([s,ids])=>ids.some(matchPoint)&&(!query||clean([strip(s[0]),strip(s[1]),strip(s[2]),...ids.map(i=>strip(points[i][1]))].join(' ')).includes(query)));viewBox.className='symbol-grid';viewBox.innerHTML=rows.map(([s,ids])=>{const target=ids.find(matchPoint);return '<article class="symbol-card"><div class="sym-name">'+s[0]+'</div><p>'+s[1]+'</p><div class="sym-unit">'+s[2]+'</div><button class="source" data-go="'+target+'">'+points[target][1]+(ids.length>1?' 等'+ids.length+'处':'')+'</button></article>'}).join('');return rows.length}
function renderMap(){const gs=groups.map((g,i)=>[g,i]).filter(([g])=>(domain==='全部'||g[1]===domain)&&(chapterName==='全部'||g[0]===chapterName));const domains=[...new Set(gs.map(([g])=>g[1]))];viewBox.className='';viewBox.innerHTML='<div class="map-title"><h2>知识脉络</h2><p>按领域与章节串联知识点；章节顺序是学习建议，点击可打开速查卡。</p></div><div class="map-root">'+SEGMENT_ROOT+'</div>'+domains.map(d=>'<section class="map-domain"><h2>'+d+'<small>'+gs.filter(([g])=>g[1]===d).length+'章</small></h2><div class="map-chapters">'+gs.filter(([g])=>g[1]===d).map(([g,gi])=>'<div class="map-chapter"><button data-chapter-go="'+gi+'">'+text(groupName(gi))+' →</button><div class="map-points">'+g[3].map(i=>'<button class="map-point" data-go="'+i+'">'+points[i][1]+'</button>').join('')+'</div></div>').join('')+'</div></section>').join('');return gs.length}
function render(){document.querySelectorAll('.nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===view));let count=view==='points'?renderPoints():view==='formulas'?renderFormulas():view==='symbols'?renderSymbols():renderMap();counter.textContent=view==='map'?'知识脉络':'显示 '+count+' 项';document.getElementById('empty').hidden=count>0}
document.querySelectorAll('.nav button').forEach(b=>b.addEventListener('click',()=>{view=b.dataset.view;history.replaceState(null,'','#'+view);render();window.scrollTo(0,0)}));
document.querySelectorAll('.filter').forEach(b=>b.addEventListener('click',()=>{domain=b.dataset.domain;document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('active',x===b));render()}));
chapter.addEventListener('change',()=>{chapterName=chapter.value;render()});search.addEventListener('input',()=>{query=clean(search.value);if(query&&view==='map'){view='points';history.replaceState(null,'','#points')}render()});
document.addEventListener('keydown',e=>{if(e.key==='/'&&document.activeElement!==search){e.preventDefault();search.focus()}});
document.addEventListener('toggle',e=>{const d=e.target;if(d.matches&&d.matches('details[data-deep]')&&d.open){const slot=d.querySelector('.deep-content');if(!slot.dataset.loaded){slot.innerHTML=deepHTML(points[Number(d.dataset.deep)]);slot.dataset.loaded='1'}}},true);
document.addEventListener('click',e=>{const go=e.target.closest('[data-go]');if(go){const i=Number(go.dataset.go);view='points';domain='全部';chapterName='全部';query='';search.value='';chapter.value='全部';document.querySelectorAll('.filter').forEach(b=>b.classList.toggle('active',b.dataset.domain==='全部'));render();document.getElementById(points[i][0]).scrollIntoView({behavior:'smooth',block:'start'});return}const ch=e.target.closest('[data-chapter-go]');if(ch){view='points';chapterName=groups[Number(ch.dataset.chapterGo)][0];chapter.value=chapterName;render();window.scrollTo(0,0)}});
render();
})();
'''


HTML = r'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="__SEGMENT_LABEL__物理知识点、公式、符号和常见错误速查"><title>__SEGMENT_LABEL__物理速查 · 谢端</title><style>__CSS__</style></head><body>
<header class="appbar"><div class="appbar-inner"><div class="brand"><i>物</i>__SEGMENT_LABEL__物理速查</div><nav class="nav" aria-label="查看内容"><button data-view="points">知识点</button><button data-view="formulas">公式</button><button data-view="symbols">符号</button><button data-view="map">知识脉络</button></nav><a class="old-link" href="__FULL_PAGE__">完整校验版 ↗</a></div></header>
__SEGMENT_NAV__
<section class="intro"><div class="eyebrow">公式 · 符号 · 条件 · 易错点</div><h1>__SEGMENT_LABEL__物理，快速查清楚</h1><p>先找到公式，再认清符号，最后避开常见错误。</p><label class="search"><span>⌕</span><input id="search" type="search" autocomplete="off" placeholder="搜索知识点、公式、符号或错误……" aria-label="搜索知识库"></label><div class="counts">__POINTS__ 个知识点 · __FORMULAS__ 条公式 · 内容已校验</div></section>
<div class="filters"><div class="filters-inner">__DOMAIN_FILTERS__<select id="chapter" aria-label="选择章节">__CHAPTERS__</select><span class="count" id="count"></span></div></div>
<main><div id="content"></div><div id="empty" class="empty" hidden>没有找到匹配内容，请换个关键词。</div></main>
<footer>__SEGMENT_LABEL__物理速查 · 谢端　单文件、断网可用　<a href="__FULL_PAGE__">查看完整校验版</a></footer>
<script type="application/json" id="kb-data">__DATA__</script><script>__JS__</script></body></html>'''


def build_page(kb_dir, segment, output_path):
    """根据传入学段复用同一套速查模板，并在生成前重新跑知识库校验。"""
    chapters = KB.load_kb(kb_dir)
    issues, id_map = KB.check_structure(chapters)
    if any(x.level == "错误" for x in issues):
        raise RuntimeError("结构检查失败，拒绝生成%s速查版。" % segment)
    report = KB.run_physics_checks(chapters, id_map)
    stats = KB.summarize(report)
    if stats["pass"] != stats["checks"] or stats["trap_failures"]:
        raise RuntimeError("物理检查失败，拒绝生成%s速查版。" % segment)
    payload = make_payload(chapters, report, segment)
    groups, points = payload["g"], payload["p"]
    options = '<option value="全部">全部章节</option>' + ''.join(
        '<option value="%s">%02d · %s</option>' %
        (html.escape(g[0], quote=True), g[2], html.escape(g[0].split("·", 1)[-1].strip()))
        for g in groups)
    domains = list(dict.fromkeys(g[1] for g in groups))
    domain_filters = '<button class="filter active" data-domain="全部">全部</button>' + ''.join(
        '<button class="filter" data-domain="%s">%s</button>' %
        (html.escape(domain, quote=True), html.escape(domain)) for domain in domains)
    senior_current = ' aria-current="page"' if segment == "高中" else ""
    junior_current = ' aria-current="page"' if segment == "初中" else ""
    full_page = "高中物理知识库.html" if segment == "高中" else "初中物理知识库.html"
    quiz_page = "quiz-hs.html" if segment == "高中" else "quiz-junior.html"
    segment_nav = (
        '<nav class="segment-switchbar" aria-label="切换物理学段"><div class="segment-switchbar-inner">'
        '<span>知识库学段</span><a href="index.html"%s>高中</a><a href="junior.html"%s>初中</a>'
        '<a class="segment-quiz" href="%s">例题自测与错误诊断 ↗</a></div></nav>' % (
            senior_current, junior_current, quiz_page))
    # JSON 中的尖括号改成转义码，防止正文文本中意外出现关闭 script 的字符。
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    quiz_href = "quiz-hs.html" if segment == "高中" else "quiz-junior.html"
    main_href = "index.html" if segment == "高中" else "junior.html"
    script = (JS.replace("__SEGMENT_ROOT__", segment + "物理")
              .replace("__QUIZ_HREF__", quiz_href)
              .replace("__RETURN_PAGE__", main_href))
    page = HTML.replace("__CSS__", CSS).replace("__JS__", script)
    page = page.replace("__DATA__", data_json).replace("__CHAPTERS__", options)
    page = page.replace("__DOMAIN_FILTERS__", domain_filters).replace("__SEGMENT_NAV__", segment_nav)
    page = page.replace("__SEGMENT_LABEL__", html.escape(segment))
    page = page.replace("__FULL_PAGE__", full_page)
    page = page.replace("__POINTS__", str(stats["points"])).replace("__FORMULAS__", str(stats["formulas"]))
    if len(points) != stats["points"] or sum(len(p[3]) for p in points) != stats["formulas"] or len(groups) != len(chapters):
        raise RuntimeError("知识点、公式或章节数量与校验统计不匹配，拒绝生成。")
    # 数据会在浏览器里动态插入，因此必须检查解码前的原始内容，而非只扫 HTML 外壳。
    visible_data = json.dumps(payload, ensure_ascii=False)
    if any(not p[5] for p in points) or re.search(
        r'\b[A-Za-z][A-Za-z0-9]*_[A-Za-z][A-Za-z0-9]*\b', visible_data
    ):
        raise RuntimeError("常见错误缺失或机器写法漏入页面，拒绝生成。")
    if 'class="chk ' in page or re.search(r'(?:src|href)="https?://', page, re.I):
        raise RuntimeError("速查版含校验明细或外部资源，拒绝生成。")
    encoded = page.encode("utf-8")
    if len(encoded) > 620 * 1024:
        raise RuntimeError("%s速查版 %.1f KB，超过 620 KB 验收上限。" % (segment, len(encoded) / 1024))
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(page)
    print("%s速查版：%s" % (segment, output_path))
    print("%.1f KB；%d 章、%d 个知识点、%d 条公式；%d/%d 项内容校验通过。" %
          (len(encoded) / 1024, len(groups), len(points), stats["formulas"], stats["pass"], stats["checks"]))
    return {"path": output_path, "stats": stats, "groups": len(groups), "bytes": len(encoded)}


def main():
    """刷新站点旧速查页，并保留原来的独立优化版输出。"""
    root = os.path.abspath(os.path.join(HERE, ".."))
    site_dir = os.path.join(root, "site")
    outputs_dir = os.path.join(root, "outputs")
    package_dir = os.path.join(outputs_dir, "物理知识库整合版")
    hs_result = build_page(os.path.join(HERE, "kb"), "高中",
                           os.path.join(site_dir, "quick.html"))
    build_page(os.path.join(HERE, "kb_junior"), "初中",
               os.path.join(site_dir, "junior-quick.html"))
    build_page(os.path.join(HERE, "kb"), "高中",
               os.path.join(outputs_dir, "高中物理速查_优化版.html"))
    for filename in ("quick.html", "junior-quick.html"):
        source = os.path.join(site_dir, filename)
        for directory in (outputs_dir, package_dir):
            os.makedirs(directory, exist_ok=True)
            with open(source, "rb") as reader, open(os.path.join(directory, filename), "wb") as writer:
                writer.write(reader.read())
    return 0


if __name__ == "__main__":
    sys.exit(main())
