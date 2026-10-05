# -*- coding: utf-8 -*-
"""统一源、校验器和生成器版本；时间写入验收记录，页面版本保持可重复构建。"""
from pathlib import Path
import json,hashlib,re
ROOT=Path(__file__).resolve().parents[1]
def versions(root=None):
    root=Path(root or ROOT);records={}
    paths=[p for d in ['kb','kb_junior'] for p in (root/'work'/d).glob('*.json') if not p.name.startswith('_')]
    paths += list((root/'work/physkit').glob('*.py'))
    paths += [root/'work'/n for n in ['qc.py','validate.py','build_all.py','build_site.py','build_lite.py','build_quick_site.py','build_quiz.py','诊断题重构.py','prose_math.py','发布版本.py','发布验收.py','机制续修_契约检查.py','crosswalk.json','crosswalk_review.json']]
    for p in paths:
        if p.exists():records[str(p.relative_to(root)).replace('\\','/')]=hashlib.sha256(p.read_bytes()).hexdigest()
    version=hashlib.sha256(json.dumps(records,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
    return {'version':version,'fingerprints':records}

def input_fingerprints():
    """诊断题的派生快照也登记；构建允许同步快照，最终验收期间则必须稳定。"""
    records=dict(versions()['fingerprints'])
    for directory in ['hs','junior']:
        for p in (ROOT/'work/quiz_bank'/directory).glob('*.json'):
            records[str(p.relative_to(ROOT)).replace('\\','/')]=hashlib.sha256(p.read_bytes()).hexdigest()
    baseline=ROOT/'质检基线.json'
    if baseline.exists():records['质检基线.json']=hashlib.sha256(baseline.read_bytes()).hexdigest()
    return records
def stamp(page):
    """由生成器输出版本元信息，不改变学生操作，也不直接手改成品。"""
    page=re.sub(r'<meta name="物理知识库版本" content="[a-f0-9]+">','',page)
    return page.replace('<head>','<head><meta name="物理知识库版本" content="'+versions()['version']+'">',1)
