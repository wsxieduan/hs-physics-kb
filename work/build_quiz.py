# -*- coding: utf-8 -*-
"""把知识库里的典型例题制作成离线可打开的自测页面。

题目、解析和答案直接写进 HTML，不请求网络，也不自动判断自由文本答案。
学生自己对照解析选择“会做 / 卡住了 / 做错了”，记录只留在当前浏览器。
"""

import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from physkit import kb as KB
from prose_math import render as prose


DIAGNOSTIC_TYPES = {
    "数值代入": "数值代入",
    "量纲一致性": "量纲一致性",
    "不等式关系": "不等式关系",
    "变化方向": "变化方向",
    "人工审核": "概念混淆",
}
TYPE_ORDER = ["数值代入", "量纲一致性", "不等式关系", "变化方向", "概念混淆"]


def diagnostic_explanation(error):
    """把原知识库的解释与可用的实测情境整理成诊断题解析。"""
    parts = [error.get("why", "")]
    test = error.get("trap_test")
    if test:
        evidence = []
        if test.get("scenario"):
            evidence.append("情境：" + str(test["scenario"]))
        if test.get("expr"):
            evidence.append("代入错误式：" + str(test["expr"]))
        if "ref" in test:
            evidence.append("校验参照值：" + str(test["ref"]))
        if test.get("expect"):
            evidence.append("变化方向要求：" + str(test["expect"]))
        if test.get("op") and test.get("vs"):
            evidence.append("边界检查：结果须满足“%s %s”" % (test["op"], test["vs"]))
        if test.get("note"):
            evidence.append("检验说明：" + str(test["note"]))
        if evidence:
            parts.append("知识库校验证据：" + "；".join(evidence) + "。")
    elif error.get("caught_by") != "人工审核":
        # 少数量纲类错误由知识库主校验流程验证，源数据未单列 trap_test。
        parts.append("这条来源记录没有单独填写 trap_test；题目分类沿用知识库已通过校验记录中的“%s”结果。" % error.get("caught_by", ""))
    return "\n".join(part for part in parts if part)


def make_diagnostic_bank(kb_dir, output_path):
    """只从高中第一章 errors 生成样板题，并保留来源索引供回链和复核。"""
    chapters = KB.load_kb(kb_dir)
    source = next(((filename, chapter) for filename, chapter in chapters
                   if os.path.basename(filename).startswith("01_")), None)
    if source is None:
        raise ValueError("没有找到第一章 JSON，无法生成诊断题样板")
    filename, chapter = source
    errors = []
    for point in chapter.get("points", []):
        for index, error in enumerate(point.get("errors", [])):
            caught_by = error.get("caught_by", "人工审核")
            if caught_by not in DIAGNOSTIC_TYPES:
                raise ValueError("第一章出现未登记的错误类型：%s" % caught_by)
            errors.append({
                "point_id": point["id"], "point_title": point["title"],
                "chapter": chapter.get("chapter", "第一章"),
                "source_error_index": index, "source": error,
                "error_type": DIAGNOSTIC_TYPES[caught_by],
                "judging": "teacher" if caught_by == "人工审核" else "auto",
            })

    # 干扰项只从本章实际出现过的类型中抽取，不引入新分类或随机内容。
    available_types = [name for name in TYPE_ORDER
                       if any(item["error_type"] == name for item in errors)]
    questions = []
    for number, item in enumerate(errors):
        error = item["source"]
        correct_type = item["error_type"]
        others = [name for name in available_types if name != correct_type]
        # 四个选项由正确类型和本章其他三类组成，轮换顺序但结果可重复构建。
        chosen_others = [others[(number + i) % len(others)] for i in range(min(3, len(others)))]
        options_text = [correct_type] + chosen_others
        offset = number % len(options_text)
        options_text = options_text[offset:] + options_text[:offset]
        options = [{"label": chr(ord("A") + i), "text": label}
                   for i, label in enumerate(options_text)]
        correct_answer = next(option["label"] for option in options
                              if option["text"] == correct_type)
        wrong = error.get("wrong", "")
        wrong_expr = error.get("wrong_expr")
        stem = "学生的错误做法：%s" % wrong
        if wrong_expr:
            stem += "（错误表达式：%s）" % wrong_expr
        stem += "。这条错误属于哪一类？"
        questions.append({
            "id": "diagnostic-%s-%d" % (item["point_id"], item["source_error_index"] + 1),
            "point_id": item["point_id"], "point_title": item["point_title"],
            "chapter": item["chapter"], "stem": stem, "options": options,
            "correct_answer": correct_answer, "explanation": diagnostic_explanation(error),
            "error_type": correct_type, "judging": item["judging"],
            "source_error_index": item["source_error_index"],
            "source_caught_by": error.get("caught_by", "人工审核"),
            "wrong_expr": wrong_expr, "trap_test": error.get("trap_test"),
        })
    bank = {"segment": "高中", "chapter": chapter.get("chapter", "第一章"),
            "source_file": os.path.basename(filename), "questions": questions}
    parent = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(bank, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    auto_count = sum(1 for q in questions if q["judging"] == "auto")
    teacher_count = len(questions) - auto_count
    print("已生成高中第一章诊断样板：%d 题（自动判定 %d，教师点评 %d）" %
          (len(questions), auto_count, teacher_count))
    return bank


STYLE = r"""
:root{--ink:#202a3a;--muted:#69778a;--line:#e2e8f0;--blue:#3569d4;--blue-soft:#eef3ff;--green:#18794e;--red:#b64040;--ink2:var(--ink);--ink3:var(--muted);--brand:var(--blue)}
*{box-sizing:border-box}body{margin:0;background:#f5f7fb;color:var(--ink);font:15px/1.7 "Microsoft YaHei","Source Han Sans SC",sans-serif}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
header{background:linear-gradient(145deg,#1c2536,#354d78);color:#fff;padding:26px 20px}
.head-inner,main{max-width:900px;margin:0 auto}.eyebrow{font-size:12px;color:#bac9e3;letter-spacing:.08em}
h1{font-size:26px;line-height:1.3;margin:6px 0}.lead{color:#d4deed;margin:5px 0 0;font-size:13px}
.toplinks{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.toplinks a{border:1px solid #ffffff50;border-radius:999px;padding:4px 11px;color:#fff;font-size:12px}
.segment-switchbar{background:#fff;border-bottom:1px solid var(--line);padding:9px 24px}
.segment-switch-inner{max-width:1080px;margin:0 auto;display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.segment-switch-label{font-size:12px;color:var(--ink3);margin-right:2px}
.segment-switchbar a{display:inline-flex;align-items:center;padding:5px 12px;border:1px solid var(--line);border-radius:999px;color:var(--ink2);font-size:13px}
.segment-switchbar a:hover{text-decoration:none;border-color:var(--brand);color:var(--brand)}
.segment-switchbar a[aria-current="page"]{background:var(--brand);border-color:var(--brand);color:#fff}
.segment-switchbar a.segment-quiz{margin-left:auto;border-color:#b8c9ff;background:#f4f7ff;color:#315fc4}
main{padding:18px 16px 48px}.panel{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:14px;box-shadow:0 3px 14px #15243b08}
h2{font-size:18px;margin:0 0 10px}.controls{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;align-items:end}
label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}select,input,button{font:inherit}
select{width:100%;padding:9px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink)}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:13px}.button{border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink);padding:8px 13px;cursor:pointer}
.button.primary{background:var(--blue);border-color:var(--blue);color:#fff}.button:hover{filter:brightness(.97)}
.hint{font-size:12px;color:var(--muted);margin:8px 0 0}.question-meta{font-size:12px;color:var(--muted)}
.stem{font-size:17px;font-weight:600;white-space:pre-wrap;margin:13px 0}.solution,.answer{white-space:pre-wrap;border-radius:9px;padding:12px;margin:10px 0}
.solution{background:#f7f9fc;border:1px solid var(--line)}.answer{background:var(--blue-soft);border-left:3px solid var(--blue)}
.mark-row{display:flex;gap:8px;flex-wrap:wrap}.mark-row .button{flex:1;min-width:110px}.master{border-color:#b8e0ca;background:#effaf3;color:var(--green)}.stuck{border-color:#f0d28f;background:#fff8e8;color:#8a650d}.wrong{border-color:#efc1c1;background:#fff2f2;color:var(--red)}
.meter{height:8px;background:#edf0f5;border-radius:99px;overflow:hidden;margin:6px 0}.meter span{display:block;height:100%;background:var(--blue)}
.result-row{padding:9px 0;border-bottom:1px solid var(--line)}.result-row:last-child{border:0}.weak-list{padding-left:18px}.weak-list li{margin:7px 0}
.small{font-size:12px;color:var(--muted)}.empty{color:var(--muted);font-size:13px;padding:5px 0}.hidden{display:none!important}
.mode-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.mode-tab{border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--ink);padding:7px 13px;cursor:pointer}
.mode-tab[aria-pressed="true"]{background:var(--blue);border-color:var(--blue);color:#fff}
.mode-tab:disabled{opacity:.55;cursor:not-allowed}
.diag-options{display:grid;gap:8px;margin:14px 0}
.diag-option{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border:1px solid var(--line);border-radius:9px;cursor:pointer;background:#fff}
.diag-option:hover{border-color:#a9bff1;background:#f8faff}
.diag-option input{margin-top:5px;accent-color:var(--blue)}
.diag-feedback{padding:12px;border-radius:9px;margin:12px 0;white-space:pre-wrap}
.diag-feedback.correct{background:#effaf3;border:1px solid #b8e0ca;color:#176c47}
.diag-feedback.incorrect{background:#fff2f2;border:1px solid #efc1c1;color:#9c3030}
.diag-feedback.teacher{background:#fff8e8;border:1px solid #f0d28f;color:#76580d}
.diag-stem{font-size:16px;font-weight:600;margin:12px 0;white-space:pre-wrap}
.source-banner{background:#f4f7ff;border-color:#dce5fb}
.source-banner a{font-weight:600}
.inline-links{display:flex;flex-wrap:wrap;gap:12px;margin:9px 0}
.inline-links a{font-size:13px}
footer{max-width:900px;margin:0 auto;padding:0 16px 30px;color:var(--muted);font-size:12px}
@media(max-width:650px){header{padding:20px 16px}h1{font-size:22px}.controls{grid-template-columns:1fr}.panel{padding:14px}.stem{font-size:16px}.mark-row{display:grid;grid-template-columns:1fr}.segment-switchbar{padding:9px 14px}.segment-switchbar a.segment-quiz{margin-left:0}}
"""


SCRIPT = r"""
(function(){
  var bank=JSON.parse(document.getElementById('quiz-bank').textContent);
  var chapterSelect=document.getElementById('chapter-select');
  var pointSelect=document.getElementById('point-select');
  var amountSelect=document.getElementById('amount-select');
  var questionPanel=document.getElementById('question-panel');
  var resultPanel=document.getElementById('result-panel');
  var progressPanel=document.getElementById('progress-panel');
  var solutionBox=document.getElementById('solution-box');
  var answerBox=document.getElementById('answer-box');
  var outcomeButtons=document.querySelectorAll('[data-outcome]');
  var deck=[], position=0, session=[], current=null;
  var storageKey='physics-kb-self-check-v1-'+bank.segment;
  var saved={version:1,attempts:[]};
  var storageReady=false;

  function setStorageNotice(available){
    var notice=document.getElementById('storage-notice');
    if(!notice)return;
    notice.textContent=available
      ?'本机记录已启用；数据不上传。更换设备或清理浏览器数据后不会同步。'
      :'当前浏览器没有开放本地存储；本页仍可练习，但刷新后记录可能无法保留。';
  }

  function loadSaved(){
    try{var value=JSON.parse(localStorage.getItem(storageKey)||'null');
      storageReady=true;
      if(value&&value.version===1&&Array.isArray(value.attempts))saved=value;
    }catch(_e){storageReady=false;saved={version:1,attempts:[]};}
  }
  function saveSaved(){
    try{localStorage.setItem(storageKey,JSON.stringify(saved));storageReady=true;return true;}
    catch(_e){storageReady=false;return false;}
  }
  function option(label,value){var o=document.createElement('option');o.value=value;o.textContent=label;return o;}
  function fillPoints(){
    var chapter=chapterSelect.value, old=pointSelect.value;
    pointSelect.innerHTML='';pointSelect.appendChild(option('全部知识点','all'));
    bank.questions.forEach(function(q){
      if(chapter!=='all'&&q.chapter!==chapter)return;
      if(!Array.prototype.some.call(pointSelect.options,function(o){return o.value===q.id;}))
        pointSelect.appendChild(option(q.title+'（'+q.id+'）',q.id));
    });
    if(Array.prototype.some.call(pointSelect.options,function(o){return o.value===old;}))pointSelect.value=old;
  }
  bank.chapters.forEach(function(name){chapterSelect.appendChild(option(name,name));});
  fillPoints();
  chapterSelect.addEventListener('change',fillPoints);
  pointSelect.addEventListener('change',function(){
    var id=pointSelect.value;if(id==='all')return;
    var q=bank.questions.find(function(item){return item.id===id;});
    if(q&&chapterSelect.value!==q.chapter){chapterSelect.value=q.chapter;fillPoints();pointSelect.value=id;}
  });
  function shuffled(items){
    var copy=items.slice();
    for(var i=copy.length-1;i>0;i--){var j=Math.floor(Math.random()*(i+1)),tmp=copy[i];copy[i]=copy[j];copy[j]=tmp;}
    return copy;
  }
  function start(){
    var chapter=chapterSelect.value, point=pointSelect.value;
    var choices=bank.questions.filter(function(q){return (chapter==='all'||q.chapter===chapter)&&(point==='all'||q.id===point);});
    if(!choices.length){window.alert('这个范围暂时没有可练习的例题。');return;}
    deck=shuffled(choices);var amount=amountSelect.value;
    if(amount!=='all')deck=deck.slice(0,Math.max(1,Math.min(deck.length,Number(amount)||10)));
    position=0;session=[];resultPanel.classList.add('hidden');progressPanel.classList.add('hidden');
    questionPanel.classList.remove('hidden');showQuestion();
  }
  function showQuestion(){
    if(position>=deck.length){showResults();return;}
    current=deck[position];
    document.getElementById('question-number').textContent='第 '+(position+1)+' / '+deck.length+' 题';
    document.getElementById('question-chapter').textContent=current.chapter+' · '+current.title;
    var pointLink=document.getElementById('question-kp-link');pointLink.href=bank.mainPage+'#'+encodeURIComponent(current.id);pointLink.textContent='回看知识点：'+current.title;
    document.getElementById('question-stem').innerHTML=current.stem;
    var solution=document.getElementById('question-solution');solution.innerHTML='';
    (current.solution||[]).forEach(function(line){var p=document.createElement('p');p.innerHTML=line;solution.appendChild(p);});
    document.getElementById('question-answer').innerHTML=current.answer||'知识点中未提供单独答案，请结合解析核对。';
    solutionBox.classList.add('hidden');answerBox.classList.add('hidden');
    outcomeButtons.forEach(function(b){b.disabled=true;});
    document.getElementById('show-answer').disabled=false;
  }
  document.getElementById('start').addEventListener('click',start);
  document.getElementById('show-answer').addEventListener('click',function(){
    solutionBox.classList.remove('hidden');answerBox.classList.remove('hidden');
    outcomeButtons.forEach(function(b){b.disabled=false;});this.disabled=true;
  });
  outcomeButtons.forEach(function(button){button.addEventListener('click',function(){
    if(!current||this.disabled)return;
    var result={id:current.id,title:current.title,chapter:current.chapter,outcome:this.getAttribute('data-outcome'),at:new Date().toISOString()};
    session.push(result);saved.attempts.push(result);if(saved.attempts.length>1500)saved.attempts=saved.attempts.slice(-1500);
    setStorageNotice(saveSaved());position++;showQuestion();
  });});
  document.getElementById('skip').addEventListener('click',function(){position++;showQuestion();});
  function addText(parent,tag,text,className){var e=document.createElement(tag);e.textContent=text;if(className)e.className=className;parent.appendChild(e);return e;}
  function percent(ok,total){return total?Math.round(ok*100/total):0;}
  function group(records,key){var result={};records.forEach(function(r){var id=r[key];if(!result[id])result[id]={total:0,mastered:0,stuck:0,wrong:0,meta:r};result[id].total++;result[id][r.outcome]++;});return result;}
  function showResults(){
    questionPanel.classList.add('hidden');resultPanel.classList.remove('hidden');resultPanel.innerHTML='';
    var mastered=session.filter(function(r){return r.outcome==='mastered';}).length;
    var stuck=session.filter(function(r){return r.outcome==='stuck';}).length;
    var wrong=session.filter(function(r){return r.outcome==='wrong';}).length;
    addText(resultPanel,'h2','本次自测完成');
    addText(resultPanel,'p','共记录 '+session.length+' 题：会做 '+mastered+' 题，卡住 '+stuck+' 题，做错 '+wrong+' 题。自评掌握率 '+percent(mastered,session.length)+'%。','result-row');
    addText(resultPanel,'p','这是自我评估结果，不是系统自动判分；建议对照解析后再选择。','small');
    var chapters=group(session,'chapter');addText(resultPanel,'h3','按章节回顾');
    Object.keys(chapters).forEach(function(name){var g=chapters[name];addText(resultPanel,'div',name+'：'+g.mastered+' / '+g.total+' 题自评会做（'+percent(g.mastered,g.total)+'%）','result-row');});
    var points=group(session,'id'), weak=Object.keys(points).filter(function(id){return points[id].stuck+points[id].wrong>0;});
    addText(resultPanel,'h3','建议回看的知识点');
    if(!weak.length)addText(resultPanel,'p','本次没有标记为“卡住”或“做错”的知识点。','empty');
    else{var ul=document.createElement('ul');ul.className='weak-list';weak.forEach(function(id){var g=points[id],li=document.createElement('li'),a=document.createElement('a');a.href=bank.mainPage+'#'+encodeURIComponent(id);a.textContent=g.meta.title+'（卡住 '+g.stuck+' 次，做错 '+g.wrong+' 次）';li.appendChild(a);ul.appendChild(li);});resultPanel.appendChild(ul);}
    var again=document.createElement('button');again.className='button primary';again.textContent='再练一组';again.addEventListener('click',start);resultPanel.appendChild(again);
  }
  function renderProgress(){
    progressPanel.innerHTML='';addText(progressPanel,'h2','本机学习记录');
    if(!saved.attempts.length){addText(progressPanel,'p','还没有保存记录。完成自测后，结果会保存在当前浏览器。','empty');return;}
    var byPoint=group(saved.attempts,'id'), rows=Object.keys(byPoint).map(function(id){return byPoint[id];});
    rows.sort(function(a,b){return (b.stuck+b.wrong)-(a.stuck+a.wrong)||b.total-a.total;});
    addText(progressPanel,'p','已保存 '+saved.attempts.length+' 次自评；只保存在这台设备的当前浏览器。','small');
    rows.slice(0,12).forEach(function(g){addText(progressPanel,'div',g.meta.title+'：会做 '+g.mastered+'，卡住 '+g.stuck+'，做错 '+g.wrong,'result-row');});
    addText(progressPanel,'p','清理浏览器数据或更换设备后，这些记录不会自动同步。','small');
  }
  function showProgress(){
    progressPanel.classList.toggle('hidden');if(progressPanel.classList.contains('hidden'))return;
    renderProgress();
  }
  document.getElementById('progress').addEventListener('click',showProgress);
  document.getElementById('clear-progress').addEventListener('click',function(){
    if(!window.confirm('确定清除这台浏览器中本学段的自测记录吗？'))return;
    saved={version:1,attempts:[]};setStorageNotice(saveSaved());
    if(!progressPanel.classList.contains('hidden'))renderProgress();
  });
  loadSaved();setStorageNotice(storageReady);
})();
"""


DIAGNOSTIC_SCRIPT = r"""
(function(){
  // 诊断模式只读取构建时嵌入的 JSON，保证双击离线文件也能练习。
  var bank=JSON.parse(document.getElementById('quiz-bank').textContent);
  var data=bank.diagnostics||{questions:[]};
  var params=new URLSearchParams(window.location.hash.slice(1));
  var modeButtons=document.querySelectorAll('[data-mode]');
  var exampleIds=['example-mode'];
  var diagnosticPanel=document.getElementById('diagnostic-panel');
  var diagnosticIntro=document.getElementById('diagnostic-intro');
  var diagnosticQuestion=document.getElementById('diagnostic-question');
  var diagnosticResults=document.getElementById('diagnostic-results');
  var answerButton=document.getElementById('diagnostic-submit');
  var nextButton=document.getElementById('diagnostic-next');
  var selected=null, deck=[], position=0, current=null, session=[];

  function node(id){return document.getElementById(id);}
  function setText(id,value){node(id).textContent=value||'';}
  function show(id,on){node(id).classList.toggle('hidden',!on);}
  function mode(value){
    var isDiagnostic=value==='diagnostic';
    exampleIds.forEach(function(id){show(id,!isDiagnostic);});
    show('diagnostic-panel',isDiagnostic);
    modeButtons.forEach(function(button){button.setAttribute('aria-pressed',String(button.getAttribute('data-mode')===value));});
    if(isDiagnostic&&!data.questions.length){
      show('diagnostic-intro',true);show('diagnostic-question',false);show('diagnostic-results',false);
    }
  }
  modeButtons.forEach(function(button){
    button.addEventListener('click',function(){
      if(this.disabled)return;
      mode(this.getAttribute('data-mode'));
      if(this.getAttribute('data-mode')==='diagnostic'&&data.questions.length&&!current) startDiagnostic();
    });
  });

  // 带来源进入时显示明确的返回位置；限制为同目录的两个知识库页面。
  var source=params.get('source'), back=params.get('return');
  if(source){
    var target=back||bank.mainPage;
    var file=target.split('#')[0];
    if(file!=='index.html'&&file!=='junior.html')target=bank.mainPage;
    var banner=node('source-banner');
    banner.classList.remove('hidden');
    setText('source-label','从《'+source+'》来 · ');
    node('source-return').href=target;
    node('source-return').textContent='返回';
  }

  function setPointLinks(q){
    var kp=node('question-kp-link');
    kp.href=bank.mainPage+'#'+encodeURIComponent(q.id);
    kp.textContent='回看知识点：'+q.title;
  }
  // 例题页由原有逻辑切题；监听题目标题变化后同步刷新当前知识点入口。
  var questionHeading=node('question-chapter');
  if(questionHeading){
    var observer=new MutationObserver(function(){
      var id=node('point-select').value;
      var q=bank.questions.find(function(item){return item.id===id;});
      if(q)setPointLinks(q);
    });
    observer.observe(questionHeading,{childList:true,characterData:true,subtree:true});
  }

  function startDiagnostic(pointId){
    deck=data.questions.filter(function(q){return !pointId||q.point_id===pointId;});
    if(!deck.length){
      mode('diagnostic');show('diagnostic-intro',true);show('diagnostic-question',false);show('diagnostic-results',false);
      setText('diagnostic-intro-message','这个知识点暂时没有错误诊断样题。本批只开放高中第一章样板；其他章节和初中暂不扩题。');
      return;
    }
    position=0;session=[];current=null;
    show('diagnostic-intro',false);show('diagnostic-results',false);show('diagnostic-question',true);
    mode('diagnostic');renderDiagnostic();
  }
  function renderDiagnostic(){
    current=deck[position];selected=null;
    setText('diagnostic-number','第 '+(position+1)+' / '+deck.length+' 题');
    setText('diagnostic-origin',current.chapter+' · '+current.point_title+' · '+(current.judging==='auto'?'机器判定题':'教师点评题 · 参考答案需教师确认'));
    setText('diagnostic-stem',current.stem);
    var options=node('diagnostic-options');options.innerHTML='';
    current.options.forEach(function(option){
      var label=document.createElement('label');label.className='diag-option';
      var radio=document.createElement('input');radio.type='radio';radio.name='diagnostic-choice';radio.value=option.label;
      radio.addEventListener('change',function(){selected=this.value;answerButton.disabled=false;});
      var text=document.createElement('span');text.textContent=option.label+'．'+option.text;
      label.appendChild(radio);label.appendChild(text);options.appendChild(label);
    });
    answerButton.disabled=true;nextButton.classList.add('hidden');
    var feedback=node('diagnostic-feedback');feedback.className='diag-feedback hidden';feedback.textContent='';
    node('diagnostic-explanation').textContent='';
    var pointLink=node('diagnostic-point-link');pointLink.href=bank.mainPage+'#'+encodeURIComponent(current.point_id);pointLink.textContent='回看知识点：'+current.point_title;
    var trapLink=node('diagnostic-trap-link');
    trapLink.href=bank.mainPage+'#trap-'+current.point_id+'-'+(current.source_error_index+1);
    trapLink.textContent='查看知识库中的这条常见错误';
  }
  answerButton.addEventListener('click',function(){
    if(!current||!selected)return;
    var feedback=node('diagnostic-feedback'),chosen=current.options.find(function(o){return o.label===selected;});
    if(current.judging==='teacher'){
      session.push({judging:'teacher'});
      feedback.className='diag-feedback teacher';
      feedback.textContent='已记录你的选择。这是一道教师点评题，系统不判对错。参考归类：'+current.error_type+'（待教师确认）。';
    }else{
      var correct=selected===current.correct_answer;
      session.push({judging:'auto',correct:correct});
      feedback.className='diag-feedback '+(correct?'correct':'incorrect');
      feedback.textContent=(correct?'回答正确。':'这次没有选中。')+' 系统分类：'+current.error_type+'。';
    }
    var explanation='错误做法：'+(current.wrong_expr||current.stem)+'\n\n解析：'+current.explanation;
    if(current.judging==='teacher')explanation+='\n\n教师确认前，本题不计入系统正确率。';
    node('diagnostic-explanation').textContent=explanation;
    show('diagnostic-explanation-wrap',true);show('diagnostic-feedback',true);
    Array.prototype.forEach.call(document.querySelectorAll('input[name="diagnostic-choice"]'),function(r){r.disabled=true;});
    answerButton.disabled=true;nextButton.classList.remove('hidden');
    nextButton.textContent=position+1>=deck.length?'查看本组统计':'下一题';
  });
  nextButton.addEventListener('click',function(){
    position++;
    if(position>=deck.length){finishDiagnostic();return;}
    renderDiagnostic();
  });
  function finishDiagnostic(){
    show('diagnostic-question',false);show('diagnostic-results',true);
    var auto=session.filter(function(r){return r.judging==='auto';});
    var right=auto.filter(function(r){return r.correct;}).length;
    var teacher=session.filter(function(r){return r.judging==='teacher';}).length;
    var accuracy=auto.length?Math.round(right*100/auto.length):0;
    node('diagnostic-results').innerHTML='';
    var title=document.createElement('h2');title.textContent='本组诊断完成';node('diagnostic-results').appendChild(title);
    var summary=document.createElement('p');summary.textContent='机器判定：'+right+' / '+auto.length+' 题正确（'+accuracy+'%）。教师点评题：'+teacher+' 题，未计入系统判定正确率。';node('diagnostic-results').appendChild(summary);
    var note=document.createElement('p');note.className='small';note.textContent='统计只覆盖本次作答；教师点评题需教师确认，不作为自动评分结果。';node('diagnostic-results').appendChild(note);
    var again=document.createElement('button');again.className='button primary';again.textContent='再练一遍';again.addEventListener('click',function(){startDiagnostic(params.get('point')||'');});node('diagnostic-results').appendChild(again);
  }

  // 自动入口由 URL 片段提供，不更改历史记录，因此浏览器后退仍回到来源页。
  var initialMode=params.get('mode')||'example', initialPoint=params.get('point');
  if(initialMode==='diagnostic'){
    mode('diagnostic');startDiagnostic(initialPoint||'');
  }else{
    mode('example');
    if(initialPoint){
      var q=bank.questions.find(function(item){return item.id===initialPoint;});
      if(q){
        var chapterSelect=node('chapter-select'),pointSelect=node('point-select');
        chapterSelect.value=q.chapter;chapterSelect.dispatchEvent(new Event('change'));
        pointSelect.value=initialPoint;pointSelect.dispatchEvent(new Event('change'));
        setTimeout(function(){node('start').click();},0);
      }
    }
  }
})();
"""


def build(kb_dir, segment, main_page, output_path, diagnostic_path=None):
    """把例题与已获准的诊断样板内嵌进同一张离线答题页。"""
    chapters = KB.load_kb(kb_dir)
    questions = []
    chapter_names = []
    for _filename, chapter in chapters:
        chapter_name = chapter.get("chapter", "未命名章节")
        if chapter_name not in chapter_names:
            chapter_names.append(chapter_name)
        for point in chapter.get("points", []):
            example = point.get("example") or {}
            if not example.get("stem"):
                continue
            questions.append({
                "id": point["id"],
                "title": point["title"],
                "chapter": chapter_name,
                # 正文排版器会先转义原始文本，再补充受控的数学上下标标签。
                "stem": prose(example.get("stem", "")),
                "solution": [prose(line) for line in example.get("solution", [])],
                "answer": prose(example.get("answer", "")),
            })

    diagnostics = {"chapter": "", "questions": []}
    if diagnostic_path and os.path.isfile(diagnostic_path):
        with open(diagnostic_path, "r", encoding="utf-8") as handle:
            diagnostics = json.load(handle)
    bank = {"segment": segment, "mainPage": main_page,
            "chapters": chapter_names, "questions": questions,
            "diagnostics": diagnostics}
    # JSON 会嵌在 script 标签里，因此转义尖括号以防题目文字意外闭合标签。
    bank_json = json.dumps(bank, ensure_ascii=False).replace("<", "\\u003c")
    page_title = segment + "物理练习与错误诊断"
    other_grade = "初中" if segment == "高中" else "高中"
    other_main = "junior.html" if segment == "高中" else "index.html"
    other_quiz = "quiz-junior.html" if segment == "高中" else "quiz-hs.html"
    hs_href = "index.html" if segment == "高中" else "index.html"
    junior_href = "junior.html"
    own_quiz = "quiz-hs.html" if segment == "高中" else "quiz-junior.html"
    diagnostic_count = len(diagnostics.get("questions", []))
    diagnostic_auto = sum(1 for item in diagnostics.get("questions", []) if item.get("judging") == "auto")
    diagnostic_teacher = diagnostic_count - diagnostic_auto
    if diagnostic_count:
        diagnostic_intro = "当前仅开放%s样板：共 %d 题，其中自动判定 %d 题、教师点评待确认 %d 题。" % (
            diagnostics.get("chapter", "第一章"), diagnostic_count, diagnostic_auto, diagnostic_teacher)
        diagnostic_button = "错误诊断（第一章样板）"
    else:
        diagnostic_intro = "错误诊断样板尚未在本学段开放；当前可继续使用例题自测。"
        diagnostic_button = "错误诊断（样板未开放）"
    diagnostic_disabled = "" if diagnostic_count else " disabled"
    page = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title><style>%(style)s</style></head><body>
<header><div class="head-inner"><div class="eyebrow">离线可用 · 例题练习 · 自我评估</div><h1>%(title)s</h1>
<p class="lead">从知识库现有例题中抽题。看完解析后，由你判断掌握情况；页面不会自动判分。</p>
<nav class="toplinks" aria-label="知识库导航"><a href="%(main)s">返回%(grade)s知识库</a><a href="%(other_main)s">%(other_grade)s知识库</a><a href="%(other_quiz)s">%(other_grade)s练习</a></nav>
</div></header>
<nav class="segment-switchbar" aria-label="切换物理学段"><div class="segment-switch-inner">
<span class="segment-switch-label">知识库学段</span><a href="%(hs)s">高中</a><a href="%(junior)s">初中</a>
<a class="segment-quiz" aria-current="page" href="%(own_quiz)s">例题自测 ↗</a></div></nav>
<main>
<section id="source-banner" class="panel source-banner hidden"><span id="source-label"></span><a id="source-return" href="%(main)s">返回</a><span class="small">；也可使用浏览器后退返回原位置。</span></section>
<section class="panel"><h2>选择练习模式</h2><div class="mode-tabs" role="group" aria-label="练习模式">
<button class="mode-tab" data-mode="example" aria-pressed="true">例题自测</button>
<button class="mode-tab" data-mode="diagnostic" aria-pressed="false"%(diagnostic_disabled)s>%(diagnostic_button)s</button></div>
<p class="hint">例题自测由学生对照解析后自评；错误诊断仅在明确标注“自动判定”的题目上计系统正确率。</p></section>
<div id="example-mode"><section id="example-controls" class="panel"><h2>选择练习范围</h2><div class="controls">
<div><label for="chapter-select">章节</label><select id="chapter-select"><option value="all">整册</option></select></div>
<div><label for="point-select">知识点</label><select id="point-select"><option value="all">全部知识点</option></select></div>
<div><label for="amount-select">本组题量</label><select id="amount-select"><option value="10">随机 10 题</option><option value="20">随机 20 题</option><option value="all">练完所选范围</option></select></div>
</div><div class="actions"><button id="start" class="button primary">开始练习</button><button id="progress" class="button">查看本机记录</button><button id="clear-progress" class="button">清除记录</button></div>
<p class="hint">题目来自对应知识点的典型例题；随机顺序不重复抽取。记录只尝试保存在当前浏览器，不上传。</p><p id="storage-notice" class="hint" role="status"></p></section>
<section id="question-panel" class="panel hidden"><div id="question-number" class="question-meta"></div><div id="question-chapter" class="question-meta"></div>
<div class="inline-links"><a id="question-kp-link" href="%(main)s">回看知识点</a></div><div id="question-stem" class="stem"></div><div class="actions"><button id="show-answer" class="button primary">查看解析与答案</button><button id="skip" class="button">跳过本题</button></div>
<div id="solution-box" class="solution hidden"><b>解析</b><div id="question-solution"></div></div><div id="answer-box" class="answer hidden"><b>答案</b><div id="question-answer"></div></div>
<p class="hint">对照后选择最符合自己的状态：</p><div class="mark-row"><button class="button master" data-outcome="mastered" disabled>会做</button><button class="button stuck" data-outcome="stuck" disabled>卡住了</button><button class="button wrong" data-outcome="wrong" disabled>做错了</button></div>
</section>
<section id="result-panel" class="panel hidden"></section><section id="progress-panel" class="panel hidden"></section>
</div>
<section id="diagnostic-panel" class="panel hidden">
<div id="diagnostic-intro"><h2>错误诊断样板</h2><p id="diagnostic-intro-message">%(diagnostic_intro)s</p><p class="small">机器判定题来自知识库中实测可抓住的错误；教师点评题不由系统判对错，也不计入系统正确率。</p></div>
<div id="diagnostic-question" class="hidden"><div id="diagnostic-number" class="question-meta"></div><div id="diagnostic-origin" class="question-meta"></div>
<div class="inline-links"><a id="diagnostic-point-link" href="%(main)s">回看知识点</a><a id="diagnostic-trap-link" href="%(main)s">查看知识库中的这条常见错误</a></div>
<div id="diagnostic-stem" class="diag-stem"></div><div id="diagnostic-options" class="diag-options"></div>
<div class="actions"><button id="diagnostic-submit" class="button primary" disabled>提交选择</button><button id="diagnostic-next" class="button hidden">下一题</button></div>
<div id="diagnostic-feedback" class="diag-feedback hidden" role="status"></div><div id="diagnostic-explanation-wrap" class="solution hidden"><b>解析与校验依据</b><div id="diagnostic-explanation"></div></div>
</div><div id="diagnostic-results" class="hidden"></div></section>
</main><footer>自评结果用于个人复习参考，不代表客观考试成绩。不要在公共设备上保存个人学习记录。</footer>
<script type="application/json" id="quiz-bank">%(bank)s</script><script>%(script)s</script><script>%(diagnostic_script)s</script></body></html>""" % {
        "title": html.escape(page_title), "style": STYLE, "main": html.escape(main_page, quote=True),
        "grade": segment, "other_main": html.escape(other_main, quote=True),
        "other_grade": other_grade, "other_quiz": other_quiz, "hs": hs_href,
        "junior": junior_href, "own_quiz": own_quiz, "diagnostic_intro": html.escape(diagnostic_intro),
        "diagnostic_button": diagnostic_button, "diagnostic_disabled": diagnostic_disabled,
        "bank": bank_json, "script": SCRIPT, "diagnostic_script": DIAGNOSTIC_SCRIPT,
    }
    parent = os.path.dirname(os.path.abspath(output_path))
    if not os.path.isdir(parent):
        os.makedirs(parent)
    with open(output_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(page)
    print("已生成：%s（%d 道例题，%.1f KB）" %
          (output_path, len(questions), os.path.getsize(output_path) / 1024.0))
    return len(questions)


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("用法：python build_quiz.py 知识库目录 学段 返回知识库页面 输出文件")
        sys.exit(2)
    build(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
