# -*- coding: utf-8 -*-
"""实测正式机制的未完成契约；只改隔离内存，不写冻结文件或质检方报告。"""
from pathlib import Path
import sys, json, copy, hashlib, datetime, io, contextlib
ROOT = Path(__file__).resolve().parents[1]
ENGINE=Path(sys.argv[sys.argv.index('--engine')+1]).resolve() if '--engine' in sys.argv else ROOT/'work'
sys.path.insert(0, str(ENGINE));sys.path.append(str(ROOT/'work'))
from physkit import checks as C, expr as E, kb as K
import qc

def inspect_contract():
    """同一组最小复现供验收入口和证据文件使用，避免门禁与报告各算一套。"""
    rows=[]
    def record(name, passed, actual):
        rows.append({'name':name, 'pass':bool(passed), 'actual':actual})
    syms={'x':{'unit':'m'},'v':{'unit':'m/s'},'t':{'unit':'s'}}
    lhs,rhs=E.parse_equation('x=v*t')
    def numeric(expression, values=None, **precision):
        return C.check_numeric('隔离契约探针',lhs,rhs,'x',syms,
            {'expr':expression,'ref':1,'_scenario_values':values or {'v':1,'t':1},**precision}).as_dict()
    # 声明比默认规则更严或更宽，都应按声明执行，不能只看旧tol。
    for expression,precision,wanted in [
        ('1.0000001*v*t',{'rtol':1e-10,'atol':0},False),
        ('1.0001*v*t',{'rtol':1e-3,'atol':0},True),
        ('1.0000001*v*t',{'rtol':0,'atol':1e-12},False)]:
        r=numeric(expression,**precision)
        record('numeric有效精度：'+str(precision),r['ok']==wanted,r)
    idx={'参考':{'rhs':rhs,'lhs_var':'x'}}
    for precision,wanted in [({'rtol':1e-10,'atol':0},False),({'rtol':1e-3,'atol':0},True)]:
        expression='1.0000001*v*t' if not wanted else '1.0001*v*t'
        r=C.check_consistency('一致性精度',E.parse(expression),syms,
            {'with':'参考','with_expr':'v*t','_scenario_values':{'v':1,'t':1},**precision},idx).as_dict()
        record('一致性有效精度：'+str(precision),r['ok']==wanted,r)
    r=C.check_numeric('合法平方目标',*E.parse_equation('v^2=(x/t)^2'),'v',syms,
        {'expr':'(x/t)^2','target_expr':'v^2','ref':.25,'_scenario_values':{'v':.5,'x':.5,'t':1}}).as_dict()
    record('显式合法平方目标通过',r['ok'],r)
    missing=numeric('v*t',{'t':1});syntax=numeric('v***t');conflict=numeric('v+x',{'v':1,'x':1,'t':1})
    record('缺变量与语法损坏有不同结构化状态',
        bool(missing.get('evaluation_status')) and bool(syntax.get('evaluation_status')) and
        missing['evaluation_status']!=syntax['evaluation_status'],{'missing':missing,'syntax':syntax})
    record('不同量纲相加登记量纲冲突',conflict.get('evaluation_status')=='dimension_conflict',conflict)
    chs=K.load_kb(str(ROOT/'work/kb'));traps=K.verify_traps(chs)
    for pid,i in [('kine-03',0),('kine-04',1),('kine-05',0)]:
        r=traps[pid,i];record(pid+'量纲错误实测统计为已识别',r['caught'] is True,r)
    # 明确公式被覆盖时到底算了什么；两侧显式关系不随被引用公式变化。
    r=C.check_consistency('覆盖范围',E.parse('2*v*t'),syms,
        {'expr':'v*t','with':'参考','with_expr':'v*t','_scenario_values':{'v':1,'t':1}},
        {'参考':{'rhs':E.parse('10*v*t'),'lhs_var':'x'}}).as_dict()
    d=r.get('diagnostics',{})
    record('实际表达式与覆盖依赖范围可追溯',
        bool(d) and d.get('primary_formula_binding') is False and
        d.get('reference_relation_follows_formula_changes') is False,r)
    bad=copy.deepcopy(chs)
    p=next(p for _,ch in bad for p in ch['points'] if p['id']=='ele-01')
    f=next(f for f in p['formulas'] if any(c['type']=='numeric' and 0<abs(c.get('ref',0))<1e-6 for c in f['checks']))
    c=next(c for c in f['checks'] if c['type']=='numeric' and 0<abs(c.get('ref',0))<1e-6)
    c['expr']='0*('+f['expr'].split('=',1)[1]+')'
    issues,n,_=qc.check_recompute(bad,0,1)
    record('质检独立复算拒绝ele-01微小量零替换',any('ele-01' in str(i) for i in issues),{'count':n,'issues':issues,'injected_expression':c['expr'],'reference':c['ref']})
    return rows

def inspect_qc_entry():
    """内存监听完整检查是否真的被正式qc入口调用；只截取报告，不写报告所有者文件。"""
    calls=[];captured=[];original=qc.check_release_all;original_open=qc.io.open
    # 隔离候选读取同一份源与成品，但仍记录候选模块真实路径，不能变成正式证据。
    constants={n:getattr(qc,n) for n in ['HERE','KB_DIR','OUT_DIR','BASELINE']}
    if ENGINE!=ROOT/'work':
        qc.HERE=str(ROOT/'work');qc.KB_DIR=str(ROOT/'work/kb');qc.OUT_DIR=str(ROOT/'outputs');qc.BASELINE=str(ROOT/'质检基线.json')
    def observe(*args,**kwargs):
        calls.append(True);return original(*args,**kwargs)
    def capture_open(file,mode='r',*args,**kwargs):
        if Path(file).resolve()==ROOT/'outputs/质检报告.md' and ('w' in mode or 'a' in mode):
            class Report(io.StringIO):
                def close(self):captured.append(self.getvalue());super().close()
            return Report()
        return original_open(file,mode,*args,**kwargs)
    stream=io.StringIO()
    try:
        qc.check_release_all=observe;qc.io.open=capture_open
        with contextlib.redirect_stdout(stream):code=qc.main(['qc.py','--check'])
    finally:
        qc.check_release_all=original;qc.io.open=original_open
        for n,v in constants.items():setattr(qc,n,v)
    return {'name':'正式qc入口实际执行完整双学段验收','pass':bool(calls),
            'actual':{'calls':len(calls),'exit_code':code,'stdout':stream.getvalue(),
                      'report_captured_in_memory':len(captured),'report_not_written':True}}

if __name__=='__main__':
    out=Path(sys.argv[sys.argv.index('--output')+1]).resolve() if '--output' in sys.argv else ROOT/'outputs/机制续修验收证据'
    out.mkdir(parents=True,exist_ok=True)
    files=[ROOT/'质检基线.json',ROOT/'outputs/质检报告.md']+[ENGINE/p for p in ['physkit/checks.py','physkit/expr.py','physkit/kb.py','qc.py','validate.py']]
    before={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    rows=inspect_contract()+[inspect_qc_entry()]
    after={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    formal=ENGINE==ROOT/'work'
    doc={'time':datetime.datetime.now().astimezone().isoformat(),'scope':'正式路径实测，错误仅注入内存副本' if formal else '隔离候选，不能作为正式发布验收','fingerprints':before,'version_stable':before==after,'rows':rows,'pass':all(r['pass'] for r in rows) and before==after}
    (out/('正式机制剩余契约回归.json' if formal else '隔离候选剩余契约回归.json')).write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for r in rows:print(('✔ ' if r['pass'] else '✘ ')+r['name'])
    sys.exit(0 if doc['pass'] else 1)
