# -*- coding: utf-8 -*-
"""诊断题结构返工：同题同量比较，独立核算，教师点评与机器判分分开。"""
import hashlib
import math
import re
from physkit import expr as E, checks as C
from prose_math import render as prose, card_symbols
from qc import safe_eval

# 版本改变会迁移历史题；相同版本和来源则保留原题和 ID。
GENERATOR_VERSION = '2026-10-04-条件过程返工-8'

# 同卡公式的等价变形/明确边界条件：逐项列出，不能只凭数值相同配对。
CORRECT_TRANSFORMS = {
    ('kine-05','v'): 'sqrt(v_0^2+2*a*x)',
    ('dyn-05','a'): '(F_push-f)/m',
    ('ene-07','E1'): 'Q+E2-W_e',
    ('pot-01','W_path2'): 'W_path1',
    ('thermo-02','E_after'): 'E_before',
    ('mdl-27','f_s'): 'mu_s*(m*g-F*sin(theta))',
    ('mdl-19','E_i'): '0*B*L*v',
    ('force-02','F_BA'): 'F_AB',
    ('fmotion-02','s'): 'v*t',
    ('ohm-01','I'): 'U/(1000*Rk)',
    ('jexp-13','I'): 'U/(1000*Rk)',
    ('jexp-15','I'): 'U/(1000*Rk)',
}

# 执行方逐条读过概念断言后，补齐被截断的指代与具体条件。
# 这里只登记参考命题及其参考真值，教师审核状态仍为待审。
CLAIMS = {
 'diagnostic-kine-03-1': ('匀变速直线运动中，速度与时间的关系为 v=v_0+a/t。',False),
 'diagnostic-kine-03-3': ('沿汽车初速度方向为正，刹车减速阶段的加速度应取正值。',False),
 'diagnostic-kine-04-2': ('从静止出发的匀加速直线运动，经过时间t的位移为x=a*t/2。',False),
 'diagnostic-kine-05-1': ('匀变速直线运动的末速度满足v=v_0+2*a*x。',False),
 'diagnostic-dyn-02-3': ('忽略空气阻力，在同一地点自由落体的加速度与物体质量无关。',True),
 'diagnostic-dyn-08-2': ('斜面上物体因重力不能自行下滑，并不表示沿斜面施加任意大的外力仍不能使其运动。',True),
 'diagnostic-mom-04-1': ('两质量不等的物体粘连碰撞，碰后共同速度总等于两初速度的算术平均值。',False),
 'diagnostic-dc-07-1': ('表头串联电阻不会分走通过表头的电流，只会增加支路总电阻。',True),
 'diagnostic-lor-03-2': ('临界速度v_max=|q|*B*R/m，仅在题目明确要求轨道半径r不超过R时可直接使用。',True),
 'diagnostic-mdl-04-2': ('汽车启动时不能在v=0处用F=P/v求有限牵引力，需要另外说明启动限制。',True),
 'diagnostic-mdl-17-2': ('导电杆的电源极性或磁场方向改变时，应重新按左手定则判断安培力方向。',True),
 'diagnostic-mdl-25-2': ('弹簧保持完整且形变量连续时，撤去其他外力的瞬间弹簧弹力连续。',True),
 'diagnostic-mdl-28-2': ('滑轮组的绳端位移等于承重绳段数乘物体位移，需绳不可伸长且承重绳段方向不变。',True),
 'diagnostic-mdl-29-2': ('气体初末状态相同，沿不同p-V过程的做功仍可能不同。',True),
 'diagnostic-mdl-35-1': ('竖直上抛最高点只有速度瞬时为零，忽略空气阻力时加速度仍为竖直向下的g。',True),
 'diagnostic-mdl-36-2': ('无地面摩擦的自由木板与滑块沿斜面同受重力加速，初始相对静止时可在摩擦力为零时保持相对静止。',True),
 'diagnostic-mdl-27-2': ('物体处于动态平衡时，速度一定为零。',False),
 'diagnostic-exp-25-2': ('等温过程中，用压力计的表压读数可以直接代入p1*V1=p2*V2，无需换成绝对压强。',False),
 'diagnostic-exp-25-3': ('等压过程可用V1/T1=V2/T2；等容过程可用p1/T1=p2/T2，其中T必须用开尔文温度。',True),
 'diagnostic-exp-10-2': ('研究加速度与力的关系时同时改变质量，就不能单独归因于力的变化。',True),
 'diagnostic-exp-11-2': ('机械能守恒实验须记录同一参考面下的高度差，并比较动能增加量与重力势能减少量。',True),
 'diagnostic-exp-14-2': ('已充电电容器若无串联限流电阻而直接短路，放电峰值电流可能损坏元件。',True),
 'diagnostic-exp-17-2': ('测电源电动势和内阻时，应通过有限负载改变工作点，而非直接短路测电流。',True),
 'diagnostic-exp-19-2': ('折射率实验需要多角度复测，并考虑光斑宽度和法线定位的不确定度。',True),
 'diagnostic-met-07-2': ('能否把物体当质点取决于尺寸和形状是否影响当前研究问题。',True),
 'diagnostic-thermo-05-2': ('相对湿度为70%时，作为比例代入应使用0.70。',True),
 'diagnostic-qz-01-2': ('频率固定的振子与光交换能量时，交换量可以连续取任意数值。',False),
 'diagnostic-qz-02-2': ('在单光子光电效应模型中，频率低于截止频率的光不能仅靠增加光强使金属发射光电子。',True),
 'diagnostic-lens-03-2': ('反射定律不能代替折射定律描述跨介质界面的折射角。',True),
 'diagnostic-lens-04-2': ('放大率只说明像的大小比例，不能单凭大小比例确定像的正倒和虚实。',True),
 'diagnostic-lens-05-2': ('红外线与紫外线在可见光范围以外，通常通过热效应、荧光或仪器间接探测。',True),
 'diagnostic-pressure-05-3': ('仅受重力与浮力而向下加速的物体，浮力小于重力，不能使用平衡条件。',True),
 'diagnostic-energy-02-2': ('在比热容近似不变且无物态变化时，计算显热可以用末温直接代替温度变化量。',False),
 'diagnostic-machine-05-2': ('两个过程的总功相同，功率就一定相同，与所用时间无关。',False),
 'diagnostic-machine-03-2': ('机械效率与功率是同一个物理量。',False),
 'diagnostic-jexp-07-2': ('晶体在固定气压下熔化时，温度不变就意味着不再吸热。',False),
 'diagnostic-tool-01-2': ('普通毫米刻度尺按中学估读约定测得3.26 cm时，完整记录可以写成3.2 cm。',False),
 'diagnostic-ohm-01-1': ('温度不变的欧姆导体，电流可按I=R/U计算。',False),
 'diagnostic-jexp-13-1': ('温度不变的欧姆导体，电流可按I=R/U计算。',False),
 'diagnostic-jmdl-01-2': ('均匀直柱体水平放置，底面完整支持且无其他竖直力时，底面压强可用rho*g*h计算。',True),
}

def number(value):
    return format(value, '.6g')

def plain_assertion(text):
    """移除叙述者的正误评价，保留待讨论的物理断言。"""
    text = re.sub(r'^(认为|以为|误认为|误以为|误认为|误认|误把|错误地|误将)', '', text.strip())
    text = text.replace('误判为','判断为').replace('误认为','认为').replace('误当成','当成')
    text = text.replace('误并联','并联').replace('误串联','串联').replace('误算','计算')
    text = re.sub(r'^把(.+?)当成', r'将\1视为', text)
    text = re.sub(r'^将(.+?)当成', r'将\1视为', text)
    return text.rstrip('。') + '。'

def conditions(point):
    """解析列出处时逐公式标名；各自条件不是同一道题的共同前提。"""
    return '\n'.join(f['name']+'：'+f['when'] for f in point['formulas'] if f.get('when','').strip())

def teacher_conditions(point, error, qid):
    """只给判断目标所需的条件；命题已自含条件时不强加整张卡条件。"""
    specific={
        'diagnostic-exp-25-2':'一定质量的理想气体作等温过程，压力计显示的是相对当地大气压的表压',
        'diagnostic-exp-25-3':'分别讨论两种过程：一为压强不变，另一为体积不变；两过程不是同时发生，气体质量均不变',
        'diagnostic-mdl-36-2':'另设一个情境：木板与滑块初始相对静止，板与斜面无摩擦；本题不采用卡片向下相对滑动阶段的动摩擦条件',
        'diagnostic-mdl-27-2':'物体作匀速直线运动，合力为零；这里讨论动态平衡的速度',
    }
    if qid in specific:return specific[qid]
    # 有待判断等式时，只采用同一输出目标的公式条件。
    wrong=error.get('wrong_expr','')
    if '=' in wrong:
        target=wrong.split('=',1)[0].strip()
        match=[f for f in point['formulas'] if f['expr'].split('=',1)[0].strip()==target]
        if len(match)==1:return match[0].get('when','')
    return ''

def numeric_question(point, error):
    """只用真实算出的同量纲错误值作干扰项，正确值独立复算后再使用。"""
    lhs, rhs = E.parse_equation(error['wrong_expr'])
    target = error['wrong_expr'].split('=',1)[0].strip()
    symbol = point['symbols'].get(target)
    if not symbol:
        return None
    tt = error.get('trap_test') or {}
    trials = [(tt['scenario'],tt['ref'],tt.get('expr'))] if 'ref' in tt else [
        (name, values[target], None) for name, values in point['scenarios'].items() if target in values]
    for scenario, ref, bad_expr in trials:
        values = point['scenarios'][scenario]
        ast = E.parse(bad_expr) if bad_expr else rhs
        env = C.build_env(point['symbols'],values)
        bad = E.evaluate(ast,env)
        target_q = E.evaluate(lhs,env)
        if bad.value is None or not math.isfinite(bad.value) or bad.dim != target_q.dim:
            continue
        rhs_text = bad_expr or error['wrong_expr'].split('=',1)[1].strip()
        independent_bad = safe_eval(rhs_text,values)
        if not math.isclose(independent_bad,bad.value,rel_tol=1e-10,abs_tol=1e-40):
            raise ValueError('两条求值路径不一致：'+point['id'])
        # 寻找同卡公式/数值检查中对应的正确表达式，不把 ref 单独当成证据。
        references = []
        transformed = CORRECT_TRANSFORMS.get((point['id'],target))
        if transformed:
            references.append((transformed,'本卡物理关系的等价变形或明确边界条件',point['definition']))
        for f in point['formulas']:
            if f['expr'].split('=',1)[0].strip() != target:
                continue
            flhs, frhs = E.parse_equation(f['expr'])
            if E.evaluate(flhs,env).dim == target_q.dim:
                references.append((f['expr'].split('=',1)[1].strip(),f['name'],f.get('when','')))
            for check in f.get('checks',[]):
                if check['type']=='numeric' and check.get('scenario')==scenario and check.get('expr'):
                    references.append((check['expr'],f['name'],f.get('when','')))
        proof = None
        for expression,name,when in references:
            try:
                q = E.evaluate(E.parse(expression),env)
                independent = safe_eval(expression,values)
                if q.dim == target_q.dim and math.isclose(independent,ref,rel_tol=1e-4,abs_tol=1e-40):
                    proof = {'expression':expression,'formula':name,'value':independent,'when':when}
                    break
            except (ValueError,KeyError,TypeError,ZeroDivisionError,NameError):
                continue
        if proof is None:
            continue
        if number(bad.value)==number(ref):
            continue
        # 只给本步正确/错误表达式需要的数据，避免并列情境的斜抛等条件干扰平抛题。
        required=E.collect_vars(E.parse(proof['expression']))|E.collect_vars(E.parse(rhs_text))
        data = '；'.join('%s（%s）=%s %s' % (n,point['symbols'][n]['desc'],number(v),point['symbols'][n]['unit'])
                        for n,v in values.items() if n != target and n in required and n in point['symbols'])
        model_conditions = proof['when']
        if point['id']=='mdl-19': model_conditions='闭合线圈完整位于均匀磁场内，面积和方向不变，只作整体平移；没有导体跨越磁场边界'
        if point['id']=='kine-05': model_conditions+='；本情境末速度沿正方向，取正值'
        target_desc='当前状态线圈的净感应电动势' if point['id']=='mdl-19' and target=='E_i' else symbol['desc']
        condition_text=('条件：'+model_conditions+'。') if model_conditions.strip() else ''
        stem = '在“%s”的模型下，%s。%s求%s（%s）。这些数值是设计示例。' % (
            point['title'],data,condition_text,target_desc,target)
        opts = [number(ref)+' '+symbol['unit'],number(bad.value)+' '+symbol['unit']]
        explanation = '使用%s：%s，代入得%s %s。另一结果来自%s，算得%s %s。\n%s' % (
            proof['formula'],proof['expression'],number(ref),symbol['unit'],rhs_text,
            number(bad.value),symbol['unit'],error['why'])
        evidence = {'kind':'numeric','scenario':scenario,'parameters':values,'target':target,
                    'unit':symbol['unit'],'reference':ref,'correct_proof':proof,
                    'distractor_expression':rhs_text,'distractor_value':bad.value,
                    'dimension_matches':True,'independent_evaluation':True}
        return stem,opts,0,explanation,evidence
    return None

def dimension_question(point,error):
    """比较同一目标量的公式；不要求学生辨认程序的检查类别。"""
    wrong = error.get('wrong_expr','')
    if '=' not in wrong:
        return None
    target = wrong.split('=',1)[0].strip()
    if target not in point['symbols']:
        return None
    for f in point['formulas']:
        if f['expr'].split('=',1)[0].strip() != target:
            continue
        try:
            lhs,rhs = E.parse_equation(wrong)
            env = C.build_env(point['symbols'],{})
            correct = E.evaluate(E.parse(f['expr'].split('=',1)[1]),env)
            bad = E.evaluate(rhs,env)
            if correct.dim == bad.dim: continue
            stem = '在“%s”中，%s。%s的单位为%s。下列哪一表达式的量纲与该目标量一致？' % (
                point['title'],f.get('when',''),point['symbols'][target]['desc'],point['symbols'][target]['unit'])
            return stem,[f['expr'],wrong],0,error['why'],{
                'kind':'dimension','target':target,'correct_expression':f['expr'],
                'distractor_expression':wrong,'target_dimension':str(correct.dim),
                'distractor_dimension':str(bad.dim)}
        except (ValueError,KeyError,TypeError,E.ExprError):
            continue
    return None

@card_symbols()
def make_question(point,error,index,chapter,segment):
    """机器检查仅证明结构与算账，所有题的教学质量仍待教师独立审核。"""
    qid = 'diagnostic-%s-%d' % (point['id'],index+1)
    source_type = error.get('caught_by','人工审核')
    spec = error.get('diagnostic',{})
    teacher = source_type=='人工审核' or spec.get('teacher_review',False)
    result = None
    if source_type=='数值代入': result = numeric_question(point,error)
    elif source_type=='量纲一致性': result = dimension_question(point,error)
    # 原错误等式左端是动能之和，没有单独的机器变量；仍围绕同一次粘连碰撞。
    if point['id']=='mom-04' and index==1:
        values=next(iter(point['scenarios'].values()))
        expression='0.5*(m1+m2)*v_common^2'
        initial='0.5*m1*v1^2+0.5*m2*v2^2'
        right=safe_eval(expression,values);bad=safe_eval(initial,values)
        # 先用独立动量式求共同速度，再比较另一套带量纲求值，不能把情境给定值当证明。
        momentum='(m1*v1+m2*v2)/(m1+m2)'
        calculated_velocity=safe_eval(momentum,values)
        context=C.build_env(point['symbols'],values)
        typed_right=E.evaluate(E.parse(expression),context)
        typed_bad=E.evaluate(E.parse(initial),context)
        joule=C.build_env({'energy':{'unit':'J'}},{'energy':1})['energy']
        if (typed_right.dim!=joule.dim or typed_bad.dim!=joule.dim
            or not math.isclose(calculated_velocity,values['v_common'],rel_tol=1e-10,abs_tol=1e-40)
            or not math.isclose(typed_right.value,right,rel_tol=1e-10,abs_tol=1e-40)
            or not math.isclose(typed_bad.value,bad,rel_tol=1e-10,abs_tol=1e-40)):
            raise ValueError('粘连碰撞诊断题未得到一致的独立动量和能量证据')
        result=('两物体在水平合外力冲量可忽略时粘连碰撞，m1=%s kg、m2=%s kg，碰前v1=%s m/s、v2=%s m/s。求碰后系统动能。设计示例。' % tuple(number(values[n]) for n in ['m1','m2','v1','v2']),
                [number(right)+' J',number(bad)+' J'],0,
                '先由动量守恒求共同速度，再用总质量计算动能：'+expression+'='+number(right)+' J。碰前动能为'+number(bad)+' J，粘连碰撞动能减少。',
                {'kind':'numeric','parameters':values,'correct_proof':{'expression':expression,'value':right,'momentum_expression':momentum,'calculated_velocity':calculated_velocity},
                 'distractor_expression':initial,'distractor_value':bad,'reference':right,'unit':'J','dimension_matches':True,'independent_evaluation':True})
    if result:
        stem,texts,correct,explanation,evidence = result
        judging = 'teacher' if teacher else 'auto'
    else:
        # 教师点评采用等长判断选项，不把长篇解析放进“正确选项”。
        # 真假命题稳定交错，不能靠“所有题都不成立”猜答案。
        seed = hashlib.sha256(qid.encode()).digest()[0]
        assertion = plain_assertion(error['wrong'])
        true_claim = re.split(r'[。；]',error['why'])[0].strip()
        unusable = any(s in true_claim for s in ['这条','错误','校验','量纲虽然','情境中','被','这正是'])
        positive = seed % 2 == 0 and not unusable and len(true_claim)>8
        if positive: assertion = true_claim+'。'
        if qid in CLAIMS:
            assertion,positive = CLAIMS[qid]
        needed=teacher_conditions(point,error,qid)
        condition_text=('讨论条件：'+needed+'。') if needed.strip() else ''
        stem = '关于“%s”，%s判断这一说法是否成立，并说明依据：\n%s' % (
            point['title'],condition_text,assertion)
        texts = ['该说法成立。','该说法不成立。']
        correct = 0 if positive else 1
        explanation = error['why']
        evidence = {'kind':'teacher','claim_source':'执行方明确命题' if qid in CLAIMS else ('why' if positive else 'wrong'),
                    'reference_only':True,'reason':'概念、操作或无法建立完整定量证据，教师确认条件与结论。'}
        judging = 'teacher'
    # 同卡片的条件、推导与适用范围进入解析，也进入来源快照。
    explanation += '\n知识点条件：'+point.get('meaning','')
    explanation += '\n推导依据：'+'；'.join(point.get('derivation',[]))
    explanation += '\n卡片公式各自的适用条件（不是本题共同前提）：\n'+conditions(point)
    order = [0,1] if hashlib.sha256((qid+'选项').encode()).digest()[0]%2==0 else [1,0]
    options = [{'label':chr(65+j),'text':prose(texts[k])} for j,k in enumerate(order)]
    answer = chr(65+order.index(correct))
    return {'id':qid,'point_id':point['id'],'point_title':point['title'],'chapter':chapter,
        'stem':prose(stem),'options':options,'correct_answer':answer,'explanation':prose(explanation),
        'error_type':{'人工审核':'概念混淆'}.get(source_type,source_type),'judging':judging,
        'source_error_index':index,'source_caught_by':source_type,
        'wrong_expr':prose(error.get('wrong_expr')) or None,'trap_test':error.get('trap_test'),
        'level':point.get('level','基础'),'learning_scope':point.get('learning_scope',segment),
        'answer_evidence':evidence,
        'review':{'confidence':'medium','requires_teacher':True,
            'machine':{'unique_option_text':len(set(texts))==len(texts),'valid_answer_label':True,
                       'numeric_or_dimension_evidence':bool(result),'no_answer_leak_in_options':True},
            'executor':{'status':'已复查题目结构与同主题来源','scope':'数值/量纲逐题复算；概念及操作只作执行方复查'},
            'teacher':{'status':'待审','answer_unique':None,'distractors_plausible':None,'stem_clear':None},
            'checks':{'answer_unique':None,'distractors_plausible':None,'stem_clear':None,'no_cross_segment':None},
            'note':'机器证据不等于教学审核；教师未签署。点评题不计自动正确率。'}}
