# -*- coding: utf-8 -*-
"""
physkit —— 高中物理知识库的构建与自动校验工具包
==============================================

【这是什么】

一套「内容自动校验」流水线。解决的具体问题：

    用 AI 批量生成物理知识点和公式时，最容易出的错是
    「公式抄错」和「物理规律说反了」—— 这两类错误看起来最像对的，
    人工一条条核不现实；但把它们放进机器里，反而能被完全自动地判死。

【四个模块的分工】

    quantity.py   物理量与量纲（7 个基本量的指数组合），外加单位表
    expr.py       公式解析 → 计算 → 数学排版（MathML）
                  一份公式文本，同时供校验和网页显示使用
    checks.py     六类自动检查：量纲 / 单位 / 数值 / 方向 / 跨公式一致 / 极端参数
    kb.py         知识库结构规范 + 结构层检查 + 调度物理层检查

【怎么用】

    from physkit import kb

    chapters = kb.load_kb("kb")
    issues, id_map = kb.check_structure(chapters)
    report = kb.run_physics_checks(chapters, id_map)
    print(kb.summarize(report))

命令行入口见本目录上级的 validate.py（只校验）和 build_site.py（校验+出网页）。
"""

__all__ = ["quantity", "expr", "checks", "kb"]
__version__ = "0.2.0"
