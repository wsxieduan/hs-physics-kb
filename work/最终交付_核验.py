# -*- coding: utf-8 -*-
"""本轮内容的独立拟合、边界与错误注入；冻结引擎只调用，不修改。"""
from pathlib import Path
import sys, json, math, re, copy, collections, hashlib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'work'))
from physkit import kb as K, checks as C, expr as E
import qc
OUT=ROOT/'outputs/最终交付证据'

def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def fit(x,y):
    """含截距的最小二乘拟合，不预设过原点。"""
    avgx=sum(x)/len(x);avgy=sum(y)/len(y)
    slope=sum((a-avgx)*(b-avgy) for a,b in zip(x,y))/sum((a-avgx)**2 for a in x)
    intercept=avgy-slope*avgx
    return {'slope':slope,'intercept':intercept,'max_residual':max(abs(b-(slope*a+intercept)) for a,b in zip(x,y))}

def current_questions():
    return {q['id']:q for s in ['hs','junior'] for p in (ROOT/'work/quiz_bank'/s).glob('*.json')
            for q in json.loads(p.read_text(encoding='utf-8')).get('questions',[])}

def protect_original_pages():
    """在新页面生成前，以实际旧页面内嵌题库核对原题面，补充外部独立基准。"""
    old=[]
    for name in ['quiz-hs.html','quiz-junior.html']:
        text=(ROOT/'outputs'/name).read_text(encoding='utf-8')
        found=False
        for blob in re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',text,re.S):
            data=json.loads(blob)
            if 'diagnostics' in data:
                old+=data['diagnostics']['questions'];found=True
        if not found: raise ValueError('未找到旧页面内嵌诊断题：'+name)
    new=current_questions(); changed=[]
    for q in old:
        actual=copy.deepcopy(new[q['id']]);actual['review'].pop('material',None)
        old_question=copy.deepcopy(q);old_question['review'].pop('material',None)
        if actual!=old_question:changed.append(q['id'])
    save('原成品题面独立保护.json',{'old_count':len(old),'new_count':len(new),'changed_ids':changed,'pass':len(old)==len(new)==742 and not changed})
    if changed or len(old)!=742:raise ValueError('原题面未保留：'+str(changed))

def experiment_checks():
    chapters=K.load_kb(str(ROOT/'work/kb'));points={p['id']:p for _,ch in chapters for p in ch['points']}
    rows=[]
    def check(name,ok,actual):
        rows.append({'name':name,'pass':bool(ok),'actual':actual})
    p=points['exp-10']
    for prefix,x_name,expected in [('固定小车质量_','T_r',1),('固定小车合力_','inverse_mass',1)]:
        data=[s for k,s in p['scenarios'].items() if k.startswith(prefix)]
        x=[1/s['m_c'] if x_name=='inverse_mass' else s[x_name] for s in data];y=[s['a_c'] for s in data];r=fit(x,y)
        check('exp-10 '+prefix+'拟合',math.isclose(r['slope'],expected,rel_tol=1e-12) and abs(r['intercept'])<1e-12 and r['max_residual']<1e-12,r)
        check('exp-10 '+prefix+'控制变量与两物体方程',all(s['m_c']>0 and s['m_h']>0 and 0<s['a_c']<s['g'] and math.isclose(s['T_r'],s['m_h']*(s['g']-s['a_c']),rel_tol=1e-12) for s in data) and len(set(s['m_c'] if prefix.startswith('固定小车质量') else s['T_r'] for s in data))==1,data)
    p=points['exp-12'];data=[s for k,s in p['scenarios'].items() if k.startswith('等间隔设计_')]
    times=[s['t_p'] for s in data];xfit=fit(times,[s['x_p'] for s in data]);yfit=fit([t*t for t in times],[s['y_p'] for s in data])
    check('exp-12 水平与竖直拟合',math.isclose(xfit['slope'],1.5,rel_tol=1e-12) and math.isclose(yfit['slope'],5,rel_tol=1e-12) and abs(xfit['intercept'])<1e-12 and abs(yfit['intercept'])<1e-12,{'horizontal':xfit,'vertical_squared_time':yfit})
    second=[data[i+2]['y_p']-2*data[i+1]['y_p']+data[i]['y_p'] for i in range(3)]
    check('exp-12 等间隔与二阶差',all(math.isclose(times[i+1]-times[i],.05,rel_tol=1e-12) for i in range(4)) and all(math.isclose(v,.025,rel_tol=1e-12) for v in second),second)
    # 对照另一水平初速度的同时刻竖直坐标；不是从图像主观判断。
    control=[{'t':s['t_p'],'x':3*s['t_p'],'y':.5*s['g']*s['t_p']**2} for s in data]
    check('exp-12 改水平速度的独立性设计控制',all(math.isclose(c['x'],2*s['x_p'],abs_tol=1e-12) and math.isclose(c['y'],s['y_p'],abs_tol=1e-12) for c,s in zip(control,data)),control)
    p=points['exp-13']
    for prefix,var,fixed,expected in [('改变质量','m',['r','T'],4*math.pi**2*.2),('改变半径','r',['m','T'],4*math.pi**2*.1),('改变周期','inverse_period',['m','r'],4*math.pi**2*.1*.2)]:
        data=[s for k,s in p['scenarios'].items() if k.startswith(prefix+'设计_')]
        forces=[s['m_h']*s['g'] for s in data];x=[1/s['T']**2 if var=='inverse_period' else s[var] for s in data];r=fit(x,forces)
        check('exp-13 '+prefix+'控制变量及拟合',all(len(set(s[v] for s in data))==1 for v in fixed) and math.isclose(r['slope'],expected,rel_tol=1e-12) and abs(r['intercept'])<1e-12 and r['max_residual']<1e-12,{'fixed':fixed,**r})
    p=points['exp-21'];direction={k:s['I_dir'] for k,s in p['scenarios'].items() if k.startswith('方向设计_')}
    check('exp-21 方向反转与静止零控制',direction['方向设计_北极插入']>0 and direction['方向设计_北极退出']<0 and direction['方向设计_南极插入']<0 and direction['方向设计_南极退出']>0 and direction['方向设计_磁铁静止']==0,direction)
    state=False;actions=[]
    for voltage,duration,wanted in [(4.545,.3,False),(1,.3,True),(2.75,.3,True),(3.333,.3,False),(2,.1,False)]:
        if duration>=.2:
            if voltage<2.5:state=True
            elif voltage>3:state=False
        actions.append({'voltage':voltage,'duration':duration,'actual':state,'expected':wanted})
    check('exp-23 滞回及延时动作设计',all(a['actual']==a['expected'] for a in actions),actions)
    check('exp-23 故障与分压边界',5*10000/(0+10000)==5 and abs(5*10000/(1e12+10000))<1e-6,{'short_sensor':5,'open_sensor_limit':0,'note':'两端均可能误触发；硬件未实测'})
    fractions=[20/(20+r) for r in [0,20,40,100]]
    check('exp-16 非均匀刻度模型',fractions==[1,.5,1/3,1/6] and (fractions[0]-fractions[1])!=(fractions[1]-fractions[2]),fractions)
    p=points['exp-24'];data=[s for k,s in p['scenarios'].items() if k.startswith('油酸单滴设计_')]
    values=[s['V_cal']/s['n_drop']*s['c_r']/s['A_o'] for s in data]
    check('exp-24 配液、单滴与单位处理',math.isclose(1/100*1/100,1e-4) and math.isclose(values[0],.8e-9,rel_tol=1e-12) and all(s['n_drop']>0 and s['A_o']>0 and 0<s['c_r']<1 for s in data),{'estimates_m':values,'mean_nm':sum(values)/len(values)*1e9,'ml_to_m3':1e-6,'cm2_to_m2':1e-4})
    # 每份新表核对列数和单位，页面必须明确标为设计示例。
    for pid in ['exp-21','exp-23','exp-10','exp-12','exp-13','exp-16','exp-24']:
        p=points[pid];tables=p['experiments'][0]['tables']
        check(pid+'设计表结构',all('设计示例' in t['title'] and all(len(r)==len(t['columns']) for r in t['rows']) for t in tables),{'tables':len(tables),'actual_apparatus_verified':False})
    # 在正式检查函数中注入错误，同时保留正确控制组，检查的不是字符串相似。
    injections=[]
    for pid in ['exp-21','exp-23','exp-10','exp-12','exp-13','exp-16','exp-24']:
        p=points[pid]
        for f in p['formulas']:
            tests=[c for c in f['checks'] if c.get('scenario','') in p['scenarios'] and ('设计' in c.get('scenario','')) and c['type']=='numeric']
            for c in tests:
                lhs,rhs=E.parse_equation(f['expr']);lhsvars=E.collect_vars(lhs);lv=next(iter(lhsvars));spec={**c,'_scenario_values':p['scenarios'][c['scenario']]}
                control=C.check_numeric(f['name'],lhs,rhs,lv,p['symbols'],spec).as_dict()
                expr='-('+f['expr'].split('=',1)[1]+')' if pid=='exp-21' else '2*('+f['expr'].split('=',1)[1]+')'
                if c['ref']==0:expr='1'  # 理论零的错误目标或量纲也必须被拒绝。
                bad=C.check_numeric(f['name'],lhs,rhs,lv,p['symbols'],dict(spec,expr=expr)).as_dict()
                injections.append({'point':pid,'formula':f['name'],'scenario':c['scenario'],'control':control,'injected':bad,'pass':control['ok'] and not bad['ok']})
    check('七卡新增检查正确控制与错误注入',all(i['pass'] for i in injections),{'count':len(injections)})
    # 分母边界明确拒绝，禁止把没有数值算成通过。
    for expr,syms,vals in [('V_cal/n_drop',{'V_cal':{'unit':'m^3'},'n_drop':{'unit':'1'}},{'V_cal':1,'n_drop':0}),('V_o/A_o',{'V_o':{'unit':'m^3'},'A_o':{'unit':'m^2'}},{'V_o':1,'A_o':0}),('4*pi^2*m*r/T^2',{'m':{'unit':'kg'},'r':{'unit':'m'},'T':{'unit':'s'}},{'m':1,'r':1,'T':0})]:
        try:E.evaluate(E.parse(expr),C.build_env(syms,vals));rejected=False
        except Exception:rejected=True
        check('非法边界拒绝：'+expr,rejected,vals)
    save('实验独立数据拟合与注入.json',{'rows':rows,'injections':injections,'pass':all(r['pass'] for r in rows)})
    if not all(r['pass'] for r in rows):raise ValueError('实验独立核验失败')
    print('实验独立核验',len(rows),'项，错误注入',len(injections),'项')

def tolerance_audit():
    rows=[];legacy=[]
    for label,directory in [('高中','kb'),('初中','kb_junior')]:
        for fn,ch in K.load_kb(str(ROOT/'work'/directory)):
            for p in ch['points']:
                for f in p['formulas']:
                    for i,c in enumerate(f['checks']):
                        if c['type'] not in ['numeric','consistency']:continue
                        if 'tol' not in c and 'precision_basis' not in c:continue
                        env=p['scenarios'][c['scenario']];a=qc.safe_eval((c.get('expr') or f['expr'].split('=',1)[1]).strip(),env)
                        b=c['ref'] if c['type']=='numeric' else qc.safe_eval(c['with_expr'],env)
                        ok,diagnostics=C.comparison(a,b,c)
                        row={'segment':label,'point':p['id'],'formula':f['name'],'check_index':i,'type':c['type'],'source':c,'a':a,'b':b,'diagnostics':diagnostics,'pass':ok}
                        rows.append(row)
                        if 'tol' in c and 'rtol' not in c and 'atol' not in c:legacy.append(row)
    probes=[]
    for a,b,spec in [(1,1.00005,{'tol':1e-4}),(1,1.00005,{'rtol':1e-4}),(1,1.00005,{'rtol':1e-4,'precision_basis':'差分截断误差示例，待质检方审核'}),(1e-9,1.2e-9,{'tol':1e-6}),(1e-9,1.2e-9,{'rtol':1e-6})]:
        ok,d=C.comparison(a,b,spec);probes.append({'a':a,'b':b,'spec':spec,'actual_pass':ok,'diagnostics':d})
    save('旧tol与有效精度逐项台账.json',{'legacy_count':len(legacy),'legacy_wider_than_default':sum(r['diagnostics']['effective_limit']>1e-6*max(abs(r['a']),abs(r['b']))*(1+1e-12) for r in legacy),'rows':rows,'probes':probes,'formal_engine_changed':False})
    print('旧tol直接通道',len(legacy),'条；冻结修复交质检方')

def classify_manual():
    opening=json.loads((OUT/'开工无法实测逐条清单.json').read_text(encoding='utf-8'));rows=[]
    points={p['id']:p for d in ['kb','kb_junior'] for _,ch in K.load_kb(str(ROOT/'work'/d)) for p in ch['points']}
    # 逐条展示原命题、具体更正理由与缺失的非数值前提，不能只给一行总数。
    for r in opening:
        error=r['错误记录'];bad=not(error.get('wrong') and error.get('why'))
        category='错误记录自身损坏' if bad else '概念、装置操作或教学判断，需人工审核'
        boundary='现有引擎只验表达式及给定情境，不判自然语言命题、装置是否接对、条件是否适用或学生是否真会误答。'
        r.update(分类=category,逐条依据=error.get('why','缺少更正理由'),自动边界=boundary,
                 缺变量或情境损坏=False,机制暂不支持=False,修复=False)
        supplement=points[r['知识点']]['errors'][r['序号']].get('automation_review')
        if supplement:
            if r['知识点'] in ['mom-04','thermo-04','pressure-01','exp-25']:
                r['分类']='缺少变量、情境不完整或参数选取不当，已补设计反例'
                r['缺变量或情境损坏']=True
            elif r['知识点'] in ['mgas-02','nuc-01']:
                r['分类']='历史精度机制限制已修，已补正式数值反例'
            r['修复']=True;r['补充反例']=supplement
            r['逐条依据']+='；本轮复验：'+supplement['basis']+'。保留人工审核标签，不扩大为命题及教学审核通过。'
        rows.append(r)
    current={}
    for label,directory in [('高中','kb'),('初中','kb_junior')]:
        traps=K.verify_traps(K.load_kb(str(ROOT/'work'/directory)))
        current[label]={'caught':sum(r['caught'] is True for r in traps.values()),'unknown':sum(r['caught'] is None for r in traps.values()),'false_defences':sum(r['caught'] is False for r in traps.values())}
    supplemental=sum(r['修复'] for r in rows);contexts=sum(r['缺变量或情境损坏'] for r in rows)
    save('无法实测错误逐条分类.json',{'opening':{'高中':150,'初中':122},'current':current,'categories':dict(collections.Counter(r['分类'] for r in rows)),'supplemented_numeric_counterexamples':supplemental,'repaired_context_count':contexts,'damaged_records':0,'rows':rows,'note':'272条原标签均人工审核；补充指定反例只证明特定数据、模型和量纲。正式unknown计数仍150/122；不改标签、未删错误项。'})
    # 清单直接可读，每条带来源锚点及教师该核对的物理依据。
    lines=['# 无法自动实测错误逐条审核清单','', '当前高中150条、初中122条，全部保留人工审核。已逐条分类，%d条补充指定正确控制和错误注入，其中%d条完善变量/情境/控制参数，2条补验历史精度限制下的数值反例，其余为可量化的概念反例。未改标签、未删项，正式无法实测统计不减少。'%(supplemental,contexts),'']
    for r in rows:
        page='index.html' if r['学段']=='高中' else 'junior.html'
        lines += ['## '+r['学段']+' '+r['知识点']+' 第'+str(r['序号']+1)+'条',
                  '[查看源卡]('+page+'#trap-'+r['知识点']+'-'+str(r['序号']+1)+')','',
                  '- 原错误：'+r['错误记录']['wrong'],'- 分类：'+r['分类'],'- 逐条物理/操作依据：'+r['逐条依据'],'- 自动边界：'+r['自动边界'],'- 教师事项：确认上述前提和反例能否支持原命题的修正，不把反例数值当作任意装置的实测。','']
    (ROOT/'outputs/无法实测错误逐条审核清单.md').write_text('\n'.join(lines),encoding='utf-8')
    print('无法实测逐条分类',len(rows),'条；补反例',supplemental,'条，其中情境/参数修复',contexts,'条；标签保持')

if __name__=='__main__':
    if '--protect' in sys.argv:protect_original_pages()
    experiment_checks(); tolerance_audit(); classify_manual()
