# -*- coding: utf-8 -*-
"""统一源、校验器和生成器版本；时间写入验收记录，页面版本保持可重复构建。

2026-10-05 质检方补两点（对应"题库、页面与质检报告的版本关联"）：

1. **派生题库也算版本。** 诊断题页面直接由 `work/quiz_bank/*.json` 生成，而构建时
   又会"逐题保留旧对象"。所以只改题干/答案/解析/审核状态、不动 kb 源，页面内容
   确实会变，可版本号以前不变 —— 旧报告于是被错误地当成仍然有效。
   现在把 quiz_bank 一并登记，内容一变页面版本就变，旧报告立刻失效。

2. **报告要绑定实际受检成品，不能靠"同版文字"。** `acceptance_binding()` 把
   「版本 + 全部实际输入指纹 + 三处交付的七个页面指纹」压成一个总指纹；
   质检报告里必须出现这个总指纹，发布门禁只认它 —— 光写一句"全部通过"不算数。
"""
from pathlib import Path
import json, hashlib, re

ROOT = Path(__file__).resolve().parents[1]

# 七个对外页面、三处交付目录（与发布验收、质检工具共用同一份清单）
PAGE_NAMES = ['index.html', 'junior.html', 'student.html', 'quick.html',
              'junior-quick.html', 'quiz-hs.html', 'quiz-junior.html']
DELIVERY_DIRS = ['outputs', 'site', 'outputs/物理知识库整合版']


def _digest(records):
    """把一个 {路径: 指纹} 字典压成一个总指纹（排序后序列化，保证可重复）。"""
    blob = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def versions(root=None):
    root = Path(root or ROOT); records = {}
    paths = [p for d in ['kb', 'kb_junior'] for p in (root / 'work' / d).glob('*.json')
             if not p.name.startswith('_')]
    # ★ 派生题库（诊断题页面就是由它生成的）也必须进版本，否则改题干/答案/解析/审核状态时版本不动。
    paths += [p for d in ['hs', 'junior'] for p in (root / 'work/quiz_bank' / d).glob('*.json')
              if not p.name.startswith('_')]
    paths += list((root / 'work/physkit').glob('*.py'))
    paths += [root / 'work' / n for n in ['qc.py', 'validate.py', 'build_all.py', 'build_site.py', 'build_lite.py',
                                          'build_quick_site.py', 'build_quiz.py', '诊断题重构.py', 'prose_math.py',
                                          '发布版本.py', '发布验收.py', '机制续修_契约检查.py',
                                          '机制续修_定义域回归.py', '最终交付_核验.py', 'crosswalk.json', 'crosswalk_review.json']]
    for p in paths:
        if p.exists():
            records[str(p.relative_to(root)).replace('\\', '/')] = hashlib.sha256(p.read_bytes()).hexdigest()
    version = _digest(records)
    return {'version': version, 'fingerprints': records}


def input_fingerprints():
    """验收期间必须稳定的**全部输入**：版本登记项 + 质检基线本身。"""
    records = dict(versions()['fingerprints'])
    baseline = ROOT / '质检基线.json'
    if baseline.exists():
        records['质检基线.json'] = hashlib.sha256(baseline.read_bytes()).hexdigest()
    return records


def product_fingerprints(root=None):
    """七个页面在三处交付目录里的**实际字节指纹**。报告必须绑定它。"""
    root = Path(root or ROOT); rows = {}
    for name in PAGE_NAMES:
        per = {}
        for directory in DELIVERY_DIRS:
            p = root / directory / name
            per[directory] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        rows[name] = per
    return rows


def acceptance_binding(root=None):
    """本次验收的绑定块：版本、实际输入总指纹、成品总指纹，以及三者合成的总指纹。

    发布门禁与质检报告用同一个函数算，所以"报告是不是对这份成品写的"可以**算出来**，
    而不是靠人去比报告里那句版本文字。
    """
    root = Path(root or ROOT)
    version = versions(str(root))['version']
    products = product_fingerprints(str(root))
    inputs = input_fingerprints()
    binding = hashlib.sha256(json.dumps({'version': version, 'inputs': inputs, 'products': products},
                                        ensure_ascii=False, sort_keys=True,
                                        separators=(',', ':')).encode('utf-8')).hexdigest()
    return {'version': version,
            'inputs_count': len(inputs),
            'inputs_digest': _digest(inputs),
            'products_digest': _digest(products),
            'binding': binding,
            'products': products}


def stamp(page):
    """由生成器输出版本元信息，不改变学生操作，也不直接手改成品。"""
    page = re.sub(r'<meta name="物理知识库版本" content="[a-f0-9]+">', '', page)
    return page.replace('<head>', '<head><meta name="物理知识库版本" content="' + versions()['version'] + '">', 1)
