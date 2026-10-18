# -*- coding: utf-8 -*-
"""检查内容来源、冻结指纹、单文件链接和三处交付的一致性，不修改校验器。"""
import hashlib
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'work'))
from physkit import kb as KB
from 发布版本 import PAGE_NAMES, DELIVERY_DIRS, versions

DEST = ROOT / 'outputs/产品审查_2026-10-18'


class Page(HTMLParser):
    """只取真实链接及内嵌数据，不执行网页脚本。"""
    def __init__(self, content):
        super().__init__()
        self.links = []
        self.data = ''
        self.in_data = False
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('a', 'script', 'link', 'img', 'iframe'):
            self.links.extend(attrs[k] for k in ('href', 'src') if k in attrs)
        if tag == 'script' and attrs.get('id') == 'kb-data':
            self.in_data = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_data = False

    def handle_data(self, content):
        if self.in_data:
            self.data += content


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    rows = []

    def check(name, ok, detail=''):
        rows.append({'name': name, 'pass': bool(ok), 'detail': detail})
        print(('通过 ' if ok else '失败 ') + name, flush=True)

    before = json.loads((DEST / '原始内容与冻结指纹.json').read_text(encoding='utf-8'))
    changed = [name for name, sha in before.items() if digest(ROOT / name) != sha]
    check('原有知识内容、题目、冻结引擎和基线没有被界面改造改写', not changed, changed)
    for name in PAGE_NAMES:
        check(name + ' 三处交付字节相同', len({digest(ROOT / directory / name) for directory in DELIVERY_DIRS}) == 1)
        page = Page((ROOT / 'site' / name).read_text(encoding='utf-8'))
        external = [x for x in page.links if urlsplit(x).scheme in ('http', 'https')]
        check(name + ' 没有外部脚本、样式或资源依赖', not external, external)
        missing = [x for x in page.links if not urlsplit(x).scheme and urlsplit(x).path and not
                   (ROOT / 'site' / unquote(urlsplit(x).path)).exists()]
        check(name + ' 本地页面跳转目标存在', not missing, missing)
    from build_quick_site import make_payload
    for segment, folder, filename in [('高中', 'kb', 'quick.html'), ('初中', 'kb_junior', 'junior-quick.html')]:
        chapters = KB.load_kb(str(ROOT / 'work' / folder))
        report = KB.run_physics_checks(chapters)
        payload = make_payload(chapters, report, segment)
        page = Page((ROOT / 'site' / filename).read_text(encoding='utf-8'))
        actual = json.loads(page.data)
        check(segment + ' 速查界面全部字段与已校验源逐项对应', payload == actual,
              {'chapters': len(chapters), 'points': len(payload['p']), 'formulas': sum(len(p[3]) for p in payload['p'])})
    result = {'version': versions()['version'], 'checks': rows, 'pass': all(r['pass'] for r in rows)}
    (DEST / '内容与交付一致性.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
