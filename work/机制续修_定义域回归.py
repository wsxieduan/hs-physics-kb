# -*- coding: utf-8 -*-
"""定义域 / 溢出 / 语法损坏 / 未声明变量的结构化状态回归。

为什么单独做一份：
    上一条「剩余契约」只测了 numeric 检查被单独调用的情况，没有覆盖
    **真正的完整调度入口** run_formula_checks。结果 definition-domain 这一类
    失败在正式路径上依旧是 unclassified_failure —— 报告里看不出来，
    实测统计也会把「数学上不成立」和「没测成」混为一谈。

本脚本只读源文件、只在内存里注入，不写冻结文件、不改内容、不改基线。
证据落在 outputs/机制续修验收证据/定义域与调度回归.json。
"""
from pathlib import Path
import sys, json, datetime

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'work') not in sys.path:
    sys.path.insert(0, str(ROOT / 'work'))

from physkit import checks as C, expr as E


def _status_of(exc):
    """异常的分类走正式引擎自己的分类函数，不在这里另写一套。"""
    return C.exception_status(exc)


def _eval_status(expression, env=None):
    """直接求值一个表达式，返回 (是否成功, 状态, 值)。"""
    try:
        q = E.evaluate(E.parse(expression), env or {})
        return True, 'success', q.value
    except Exception as exc:
        return False, _status_of(exc), None


def _entry_status(expression, symbols, values, check_expr=None):
    """从完整调度入口 run_formula_checks 拿结果的状态列表。

    expression 是公式本身（等号左端一个变量），check_expr 是检查项里声明的表达式。
    """
    rec = {'name': '定义域探针', 'expr': expression,
           'checks': [{'type': 'numeric', 'scenario': '探针情境',
                       'expr': check_expr or expression, 'ref': 1}]}
    results, _ctx = C.run_formula_checks(rec, symbols, {'探针情境': values}, {})
    return results


def inspect_domain():
    rows = []

    def record(name, passed, actual):
        rows.append({'name': name, 'pass': bool(passed), 'actual': actual})

    # ---------- 一、函数定义域：必须给 domain_error ----------
    for expression, want in [('ln(-1)', 'domain_error'), ('log(-1)', 'domain_error'),
                             ('ln(0)', 'domain_error'), ('asin(2)', 'domain_error'),
                             ('acos(2)', 'domain_error'), ('sqrt(-1)', 'domain_error'),
                             ('1/0', 'domain_error')]:
        ok, status, _ = _eval_status(expression)
        record('定义域：%s 记为 %s' % (expression, want), (not ok) and status == want,
               {'status': status, 'expression': expression})

    # ---------- 二、溢出：必须单独成一类，不并进定义域 ----------
    for expression, want in [('exp(10000)', 'overflow_error')]:
        ok, status, _ = _eval_status(expression)
        record('溢出：%s 记为 %s' % (expression, want), (not ok) and status == want,
               {'status': status, 'expression': expression})

    # ---------- 三、语法损坏：仍是 syntax_error，不能被定义域吞掉 ----------
    for expression in ['v***t', 'v*', '(v*t', 'ln()']:
        ok, status, _ = _eval_status(expression)
        record('语法损坏：%s 记为 syntax_error' % expression, (not ok) and status == 'syntax_error',
               {'status': status, 'expression': expression})

    # ---------- 四、未声明变量：与语法损坏必须区分 ----------
    ok, status, _ = _eval_status('v*t', {})
    record('未声明变量记为 missing_variable', (not ok) and status == 'missing_variable',
           {'status': status})

    # ---------- 五、完整调度入口：不得再出现 unclassified_failure ----------
    symbols = {'x': {'unit': 'm'}, 'v': {'unit': 'm/s'}, 't': {'unit': 's'}}
    values = {'v': 1, 't': 1}
    cases = [('x=v***t', 'syntax_error'), ('x=u*t', 'missing_variable'),
             ('x=v+t', 'dimension_conflict')]
    for expression, want in cases:
        results = _entry_status(expression, symbols, values)
        statuses = [r.evaluation_status for r in results]
        record('调度入口 %s 首条状态为 %s' % (expression, want), want in statuses,
               {'statuses': statuses, 'detail': results[0].detail if results else None})

    # 全库合法公式走一遍入口，任何一条都不许是 unclassified_failure
    from physkit import kb as K
    bad = []
    total = 0
    for label, directory in [('高中', 'kb'), ('初中', 'kb_junior')]:
        for _fn, chapter in K.load_kb(str(ROOT / 'work' / directory)):
            for point in chapter['points']:
                for formula in point['formulas']:
                    results, _info = C.run_formula_checks(
                        formula, point.get('symbols', {}), point.get('scenarios', {}), {})
                    total += len(results)
                    bad += [{'segment': label, 'point': point['id'], 'formula': formula.get('name'),
                             'status': r.evaluation_status, 'detail': r.detail}
                            for r in results if r.evaluation_status == 'unclassified_failure']
    record('正式调度入口无 unclassified_failure', not bad,
           {'checked': total, 'unclassified': bad[:10], 'unclassified_count': len(bad)})

    # ---------- 六、正确控制组：合法的写法不许被误判成失败 ----------
    for expression, want_value in [('ln(2)', 0.6931471805599453), ('asin(0.6)', 0.6435011087932844),
                                   ('sqrt(4)', 2.0), ('exp(1)', 2.718281828459045),
                                   ('1/2', 0.5), ('log(100)', 2.0)]:
        ok, status, value = _eval_status(expression)
        good = ok and status == 'success' and abs((value or 0) - want_value) < 1e-12
        record('控制组：%s 正常算出 %g' % (expression, want_value), good,
               {'ok': ok, 'status': status, 'value': value})
    results = _entry_status('x=v*t', symbols, values, check_expr='v*t')
    record('控制组：合法公式 x=v*t 调度入口全部通过',
           bool(results) and all(r.ok for r in results),
           {'statuses': [r.evaluation_status for r in results]})

    # ---------- 七、定义域失败不能被算成「抓住了物理错误」 ----------
    from physkit import kb as K
    chapters = K.load_kb(str(ROOT / 'work/kb'))
    for _fn, chapter in chapters:
        for point in chapter['points']:
            if point['id'] != 'kine-01':
                continue
            formula = point['formulas'][0]
            broken = dict(point)
            broken['formulas'] = [dict(formula, checks=[{'type': 'numeric', 'scenario': '单向直线',
                                                        'expr': 'ln(-1)', 'ref': 1}])]
            report = K.run_physics_checks([('probe.json', {'points': [broken]})])
            statuses = [r['evaluation_status'] for p in report.values() for r in p['results']]
            record('定义域失败不被记为已识别物理错误',
                   'domain_error' in statuses and 'numeric_mismatch' not in statuses,
                   {'statuses': statuses})
    return rows


if __name__ == '__main__':
    out = Path(sys.argv[sys.argv.index('--output') + 1]).resolve() if '--output' in sys.argv \
        else ROOT / 'outputs/机制续修验收证据'
    out.mkdir(parents=True, exist_ok=True)
    rows = inspect_domain()
    doc = {'time': datetime.datetime.now().astimezone().isoformat(),
           'scope': '正式路径实测，错误仅注入内存副本',
           'rows': rows, 'pass': all(r['pass'] for r in rows)}
    (out / '定义域与调度回归.json').write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n',
                                             encoding='utf-8')
    for r in rows:
        print(('✔ ' if r['pass'] else '✘ ') + r['name'])
    print('合计 %d 项，未通过 %d 项' % (len(rows), sum(not r['pass'] for r in rows)))
    sys.exit(0 if doc['pass'] else 1)
