# -*- coding: utf-8 -*-
"""完整发布门禁：正式检查、精度、独立复算、版本及成品分别核对。"""
from pathlib import Path
import sys,json,hashlib,re,datetime,subprocess,math,copy
if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='backslashreplace')
if hasattr(sys.stderr,'reconfigure'):sys.stderr.reconfigure(encoding='utf-8',errors='backslashreplace')
from physkit import kb as K,expr as E,checks as C
import qc
from 发布版本 import ROOT,versions,input_fingerprints
PAGES=['index.html','junior.html','student.html','quick.html','junior-quick.html','quiz-hs.html','quiz-junior.html']
# ★ 与执行方候选「有意不采纳」的政策差异名单（2026-10-05 质检方登记）
#   只登记**我们明知道**并 deliberately 选择不同做法的那几条；每条都要写清为什么不采纳。
#   只要这份名单和探针实际结果对不上（探针改名、新增、或消失了），门禁立刻报错要求重新审查 ——
#   防止名单变成「以后什么都往里塞」的垃圾桶。
ACCEPTED_POLICY_DIFFS={
 "numeric有效精度：{'rtol': 0.001, 'atol': 0}":
   "不采纳「源写 rtol=1e-3 就无条件放行」。允许内容自己把容差放大 1000 倍等于自开后门；"
   "本工程改为：可放宽，但必须写明 precision_basis，且不得超过默认精度的 100 倍（osc-03 手算参考这类实际需求已被覆盖）。",
 "一致性有效精度：{'rtol': 0.001, 'atol': 0}":
   "同上，一致性检查采用同一套判定，不放宽到千分之一。",
}
ALIASES={'高中物理知识库.html':'index.html','初中物理知识库.html':'junior.html','高中物理速查.html':'quick.html','高中物理速查_优化版.html':'quick.html','高中物理知识库-学生版.html':'student.html','初中物理速查.html':'junior-quick.html'}
def now():return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def precision(a,b,c):
    """独立求值使用已声明的精度；不使用固定的大绝对底线。
    ★ 2026-10-05 质检方统一定调：这里改调用正式 checks.comparison，
      免得「发布门禁」和「正式引擎」各有一套容差算法、结论打架。
      注意独立性体现在**求值**是另一套代码（qc.safe_eval），不是容差规则另立门户。"""
    return C.comparison(a,b,c)[0]
def independent(chapters):
    rows=[];index=K.build_formula_index(chapters)
    for _,ch in chapters:
        for p in ch['points']:
            for f in p['formulas']:
                for c in f['checks']:
                    if c['type'] not in ['numeric','consistency']:continue
                    env=p['scenarios'][c['scenario']];a_expr=(c.get('expr') or f['expr'].split('=',1)[1]).strip()
                    b_expr=None if c['type']=='numeric' else (c.get('with_expr') or index[c['with']]['expr'].split('=',1)[1]).strip()
                    try:
                        a=qc.safe_eval(a_expr,env);b=c['ref'] if c['type']=='numeric' else qc.safe_eval(b_expr,env)
                        rows.append({'point':p['id'],'formula':f['name'],'type':c['type'],'a_expression':a_expr,'b_expression':b_expr,'a':a,'b':b,'pass':precision(a,b,c)})
                    except Exception as exc:rows.append({'point':p['id'],'formula':f['name'],'pass':False,'error_type':type(exc).__name__,'error':str(exc)})
    return rows
def engine_contract(contract_rows=None):
    """直接测试实际正式引擎，不能只看独立复算是否通过。"""
    issues=[]
    for module in [K,E,C,qc]:
        if not Path(module.__file__).resolve().is_relative_to(ROOT/'work/physkit') and Path(module.__file__).resolve()!=ROOT/'work/qc.py':issues.append('发布必须使用正式路径校验器，不能使用隔离候选：'+str(module.__file__))
    symbols={'q':{'unit':'C'},'I':{'unit':'A'},'t':{'unit':'s'}};_,rhs=E.parse_equation('q=I*t');idx={'探针':{'rhs':rhs,'lhs_var':'q'}}
    for a,b in [('0*I*t','I*t'),('I*t','0*I*t'),('10*I*t','I*t'),('I*t','10*I*t')]:
        r=C.check_consistency('微小量发布探针',E.parse(a),symbols,{'_scenario_values':{'I':8e-19,'t':1},'with':'探针','with_expr':b},idx)
        if r.ok:issues.append('正式引擎误放过微小量错误：'+a+' 对 '+b)
        if r.as_dict().get('evaluation_status')!='numeric_mismatch':issues.append('一致性未提供明确数值不符状态')
    r=C.check_consistency('量纲发布探针',E.parse('I'),symbols,{'_scenario_values':{'I':1,'q':1,'t':1},'with':'探针','with_expr':'q'},idx)
    if r.ok or r.as_dict().get('evaluation_status')!='dimension_conflict':issues.append('一致性缺少两端量纲门禁或结构化状态')
    symbols={'x':{'unit':'m'},'v':{'unit':'m/s'},'t':{'unit':'s'}};lhs,rhs=E.parse_equation('x=v*t')
    r=C.check_numeric('目标量纲发布探针',lhs,rhs,'x',symbols,{'_scenario_values':{'v':1,'t':1},'ref':1,'expr':'v'})
    if r.ok or r.as_dict().get('evaluation_status')!='dimension_conflict':issues.append('numeric缺少实际目标量纲门禁或结构化状态')
    scan_chapters=[('发布探针.json',{'points':[{'id':'symbol-probe','symbols':{'delta_E_total':{'unit':'J'}},'meaning':'总能量差记为delta_E_total。','formulas':[],'errors':[],'derivation':[],'examples':[]} ]})]
    if qc.check_prose_symbols(scan_chapters)[1]:issues.append('delta_E_total完整下标仍被符号扫描误报')
    if not hasattr(qc,'check_release_all'):issues.append('正式质检尚未具备初高中完整正式检查与版本验收，待质检方应用修复')
    # 不能因函数已存在就认定入口执行了它，也不能用已有样例通过代替机制回归。
    from 机制续修_契约检查 import inspect_contract,inspect_qc_entry
    contract=inspect_contract()+[inspect_qc_entry()]
    # ★ 2026-10-05 质检方补：区分「还没修完」和「有意不采纳的政策分歧」。
    #   上面两条探针要求：源里只要写一句 rtol=1e-3，就得允许把误差放大到千分之一。
    #   质检方不采纳 —— 那等于内容可以自己给自己的检查开后门。
    #   我们改成：要放宽必须写清楚依据（precision_basis），且最多放宽到默认精度的 100 倍。
    #   这两条因此不计为缺陷，但必须在下面名单里点名、写明理由，写进验收记录公开展示；
    #   名单之外的任何一条失败，一律照旧拦发布。
    failures=[r for r in contract if not r['pass']]
    unknown=[]
    for r in contract:
        if r['pass']:r['policy']='已通过';continue
        reason=ACCEPTED_POLICY_DIFFS.get(r['name'])
        if reason:r['policy']='有意不采纳的政策差异：'+reason
        else:r['policy']='必须先修好的机制缺陷';unknown.append(r)
    if contract_rows is not None:contract_rows.extend(contract)
    issues.extend('正式机制未完成：'+r['name'] for r in unknown)
    if len(ACCEPTED_POLICY_DIFFS)!=len([r for r in contract if r['policy'].startswith('有意不采纳')]):
        issues.append('登记的政策差异与实际不符，说明执行方探针或本名单已变化，必须重新审查')
    print('   机制契约探针：通过 %d 条，拦发布 %d 条，登记的政策差异 %d 条'
          % (sum(1 for r in contract if r['pass']), len(unknown),
             sum(1 for r in contract if r['policy'].startswith('有意不采纳'))))
    for r in contract:
        if r['policy'].startswith('有意不采纳'):print('     · 政策差异：%s —— %s' % (r['name'], r['policy'][10:]))
    baseline=ROOT/'质检基线.json'
    if not baseline.exists():issues.append('缺少质检基线，需质检方处理')
    else:
        fingerprint_issues,_=qc.check_fingerprint(json.loads(baseline.read_text(encoding='utf-8')))
        issues.extend('正式校验器指纹：'+str(i) for i in fingerprint_issues)
    return issues
def validate_inputs():
    data={'started_at':now(),'before':versions(),'input_fingerprints_before':input_fingerprints(),'segments':{},'mechanism_contract':[]}
    data['issues']=engine_contract(data['mechanism_contract'])
    for label,d in [('高中','kb'),('初中','kb_junior')]:
        chs=K.load_kb(str(ROOT/'work'/d));structure,_=K.check_structure(chs);report=K.run_physics_checks(chs);stats=K.summarize(report);rec=independent(chs)
        errors=[str(i) for i in structure if i.level=='错误'];bad=[{'point':pid,**r} for pid,p in report.items() for r in p['results'] if not r['ok']]
        data['segments'][label]={'summary':stats,'structure_errors':errors,'failed_count':stats['checks']-stats['pass'],'failed_checks':bad,'independent':rec}
        data['issues'] += [label+'结构：'+s for s in errors]+[label+'完整物理：'+r['point']+'/'+r['formula'] for r in bad]+[label+'独立复算：'+r['point']+'/'+r['formula'] for r in rec if not r['pass']]+stats['trap_failures']
    data['after']=versions();data['input_fingerprints_after']=input_fingerprints();data['finished_at']=now();data['version_stable']=data['before']==data['after'] and data['input_fingerprints_before']==data['input_fingerprints_after']
    if not data['version_stable']:data['issues'].append('验收期间版本变化，必须重验')
    return data
def products(version):
    rows=[];issues=[];bases=[ROOT/'outputs',ROOT/'site',ROOT/'outputs/物理知识库整合版']
    for f in PAGES:
        hashes=[]
        for base in bases:
            p=base/f
            if not p.exists():issues.append('缺少成品：'+str(p));continue
            page=p.read_text(encoding='utf-8');match=re.search(r'<meta name="物理知识库版本" content="([a-f0-9]+)">',page);pv=match.group(1) if match else None
            rows.append({'path':str(p.relative_to(ROOT)).replace('\\','/'),'sha256':sha(p),'bytes':p.stat().st_size,'version':pv,'same_version':pv==version});hashes.append(sha(p))
            if pv!=version:issues.append(str(p.relative_to(ROOT))+'是历史成品或版本不同，不能作为本轮验收结果')
            if p.stat().st_size>8*1048576:issues.append('成品超过8 MiB：'+str(p))
        if len(hashes)!=3 or len(set(hashes))!=1:issues.append('三个交付位置不同版：'+f)
    for base in bases:
        for alias,f in ALIASES.items():
            p=base/alias
            # 速查及学生版别名维护在outputs；两学段完整页三个目录均维护。
            required=base==ROOT/'outputs' or f in ['index.html','junior.html']
            if not p.exists():
                if required:issues.append('缺少维护别名：'+str(p))
                continue
            if not (base/f).exists() or sha(p)!=sha(base/f):issues.append('维护别名不同版：'+str(p))
    return rows,issues
def finish(data):
    rows,issues=products(data['after']['version']);data.update(products=rows,product_issues=issues);data['issues']+=issues
    # 质检报告由质检方写；这里只核对，不能用内存质检替代正式落盘报告。
    qp=ROOT/'outputs/质检报告.md';report=qp.read_text(encoding='utf-8') if qp.exists() else '';qhash=sha(qp) if qp.exists() else None
    data['quality_report']={'path':'outputs/质检报告.md','sha256':qhash,'same_version':('源/校验器/生成器版本：`'+data['after']['version']+'`') in report,'declares_full_machine_pass':'**自动环节全部通过。**' in report}
    if not data['quality_report']['same_version']:data['issues'].append('质检方正式报告仍是历史版本；需在新成品生成后由质检方运行完整质检并更新报告')
    elif not data['quality_report']['declares_full_machine_pass']:data['issues'].append('同版质检报告未声明完整机器环节通过，不能发布')
    if versions()!=data['after']:data['issues'].append('成品验收期间源或校验器变化，必须重验')
    if input_fingerprints()!=data['input_fingerprints_after']:data['issues'].append('成品验收期间诊断题等输入改变，必须重验')
    if (sha(qp) if qp.exists() else None)!=qhash:data['issues'].append('验收期间正式质检报告改变，必须重验')
    data['issues']=list(dict.fromkeys(data['issues']))
    data['pass']=not data['issues'];data['finished_at']=now()
    p=ROOT/'outputs/当前发布验收.json';p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 当前发布验收','', '检查时间：'+data['finished_at'],'版本：`'+data['after']['version']+'`。','', '**'+('通过完整机器验收；教学仍待教师签署。' if data['pass'] else '未通过，不能发布；同版成品与质检报告尚未完成验收。逐页版本见验收JSON。')+'**','', '| 学段 | 正式通过 | 正式失败 | 独立复算 |','|---|---:|---:|---:|']
    for label,r in data['segments'].items():
        s=r['summary'];lines.append('| %s | %d/%d | %d | %d项，%d处不符 |'%(label,s['pass'],s['checks'],r['failed_count'],len(r['independent']),sum(not x['pass'] for x in r['independent'])))
        print('%s：正式通过%d/%d；失败%d项；独立复算%d项、%d处不符'%(label,s['pass'],s['checks'],r['failed_count'],len(r['independent']),sum(not x['pass'] for x in r['independent'])))
    lines += ['', '## 未解决项目','']+['- '+s for s in data['issues']]
    lines += ['', '742道诊断题教师审核状态不得由机器验收自动更改。7项实验继续部分覆盖。质检方原报告单独保存；未更新前只能作为历史结果。']
    (ROOT/'outputs/当前发布验收.md').write_text('\n'.join(lines)+'\n',encoding='utf-8');print('结论：'+('✔ 完整机器验收通过，教师待审' if data['pass'] else '✘ 未通过；正式机制与最终同版成品尚未完成'))
    return 0 if data['pass'] else 1
_READY=None
def ensure_ready():
    global _READY
    if _READY and _READY['after']==versions():return _READY
    data=validate_inputs()
    if data['issues']:
        finish(data)
        raise RuntimeError('发布门禁未通过：\n'+'\n'.join(data['issues']))
    _READY=data
    return data
def main():
    data=validate_inputs()
    if '--build' in sys.argv and not data['issues']:
        r=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'work/build_all.py')],cwd=ROOT)
        if r.returncode:data['issues'].append('统一构建失败，不发布')
        # 构建可能同步历史题库；必须对实际最终源与正式机制重验。
        post=validate_inputs()
        if post['after']!=data['after']:post['issues'].append('构建前后版本改变，需重新启动完整验收')
        data=post if not r.returncode else {**post,'issues':post['issues']+['统一构建失败，不发布']}
    return finish(data)
if __name__=='__main__':sys.exit(main())
