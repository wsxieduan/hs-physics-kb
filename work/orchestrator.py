# -*- coding: utf-8 -*-
"""
协作编排器 —— Codex 干活、质检把关，交替推进项目（纯标准库，无依赖）
================================================================

【为什么要多一个脚本出来】

    Codex 是一个命令行程序（codex exec），可以被无人值守地叫起来干活。
    但它自己不会判断「这批干完了没有」「干得对不对」。

    所以这个脚本负责中间那一段：
        开跑前先体检（网络通不通）
          → 给 Codex 派活（让它干一批）
          → 干完立刻质检（跑 work/qc.py）
          → 合格就进入下一批；不合格就把质检报告塞回去让它重修
          → 连续失败就停机；网络断了就等，等回来再继续

    质检用的是 work/qc.py —— 质检方写的，已冻结，Codex 不许改。
    这样一来「生产」和「质检」是两个作者、两套代码，才拦得住互相印证式的错误。

【两种跑法】

    1) 手动：双击 D:\\codex\\run_loop.cmd，或者
             python work/orchestrator.py --rounds 2

    2) 定时：Windows 任务计划程序每 30 分钟跑 D:\\codex\\run_loop_hidden.vbs
             每次跑到「等质检方放行」「网络不通」「连续失败」就自己退出，
             下一次定时再接着跑。这样天然自愈，不需要一直守着。

【常用参数】

    --rounds N     本次最多跑几轮（默认取下面的配置）
    --minutes M    本次最多跑多少分钟（默认取配置）
    --dry-run      只打印「将要派什么活」：不叫 Codex、不落日志、不改状态与批次计划
                   （质检报告会照常重算一遍，因为派什么活得看质检结果）
    --no-codex     只跑质检，不叫 Codex（想单看质检结果时用）
    --plan         只打印《批次计划》（进度真相）和下一批目标
    --force        无视「等质检方放行」这道闸门，直接开跑（慎用）
    --status       只打印当前状态，什么都不干

【两方的交互契约（这是本脚本存在的理由）】

    一个文件只允许一个作者 —— 之前没说清，所以两边都可能往同一个文件上写字。

        work/批次计划.md      编排器写（Codex 只读）—— 进度真相、每批交付什么
        work/协作状态.json    编排器写（质检方只开/关「放行」这一个字段）
        work/交接单.json      Codex 写  —— 声明它这一轮实际改了哪些文件
        work/本批报告.md      Codex 写  —— 给人看的说明
        outputs/质检意见.md   质检方写   —— Codex 下一轮必须逐条回应
        work/质检回执.json    质检方写   —— 结论 + 逐条回应 + 是否放行

    一轮的往返：
        编排器照《批次计划》抄派活指令 → Codex 只做那一批 → 交《交接单》
        → 编排器「轮后质检」+「★ 对账」（拿交接单跟文件时间/git 比）
        → 质检方核抽样单、写《质检意见》《质检回执》→ 编排器标完成、开下一批

    ★ 为什么必须有「对账」这一步：
      实测教训 —— 2026-09-18 第一轮的交接报告写「本批修改了 kb 两个文件」，
      而那两个文件的修改时间比那轮启动还早，那一轮其实零产出。
      光看报告永远发现不了。**不靠信，靠核。**
"""

import atexit
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import net_guard  # noqa: E402  网络体检（同目录）


def _guard_stream(stream):
    """把「不存在的输出通道」换成黑洞。

    为什么需要这个：定时任务用 pythonw.exe 跑（为了不弹黑框），
    而 pythonw 没有控制台 —— 这种情况下 sys.stdout 可能是 None，
    于是任何一句 print() 都会抛 AttributeError，脚本静默崩掉。
    「我手动跑得好好的、挂上定时就没了」这类鬼故事多半就是这么来的。
    """
    if stream is not None:
        return stream

    class _Null:
        def write(self, *a):
            return len(a[0]) if a and isinstance(a[0], str) else 0

        def flush(self):
            pass

        def isatty(self):
            return False

        def close(self):
            pass

    return _Null()


sys.stdout = _guard_stream(sys.stdout)
sys.stderr = _guard_stream(sys.stderr)

# ============================================================
# 配置 —— 想改什么直接改这里
# ============================================================
CONFIG = {
    # Codex 的命令行程序。
    #
    # ★ 留空 = 运行时自动去找（见 codex_exe()）。**不要在这里写死路径** ——
    #   它的目录名里带一串安装哈希（`bin\cdef5aaf…\codex.exe`），
    #   **每次升级 Codex 都会变**。写死的结果就是升级之后怎么都叫不起它，
    #   而且症状看起来像"Codex 坏了"，其实只是路径过期。
    "codex程序": "",

    # 万一上面几条自动查找的路都走不通，用这个兜底（允许它过期，只当最后保险）。
    "codex程序兜底": r"C:\Users\Lenovo\AppData\Local\OpenAI\Codex\bin\cdef5aaf3e41ab53\codex.exe",

    # ★ 推理档。Codex 的 config.toml 里写死的是 high —— 每一步都深度思考，
    #   写一整章能磨蹭一两个小时。而我们写的是**结构化知识库**
    #   （定义、公式、情境参数），不需要解奥数题，medium 完全够用。
    #   2026-09-19 实测：high 档跑 7 分钟连内容都没开始写。
    #   留一个开关在这儿，哪天发现它在物理推导上出错率变高，改回 high 就行。
    "推理档": "medium",

    # 本地代理。Codex 靠它才能连上 chatgpt.com。
    #
    # ★★ 2026-09-19 改：**这一项已经退化成「兜底默认值」，不再是真理。**
    #
    #   真正生效的端口由**网络体检实测**决定：每轮开跑前 `net_guard.evaluate()`
    #   按 CANDIDATE_PORTS 顺序真握手试探，谁通就用谁；主循环拿到结果后会
    #   **改写这个键**（见「体检通道 = 干活通道」那一段）。
    #   只有体检没能给出端口时，这里才会被用到。
    #
    #   为什么要这么改 —— 因为「哪条通道好」**一天之内就翻过两次**：
    #     · 上午实测：v2rayN(10808) 失败率 15%（含两次 20 秒 TLS 死等），
    #                 SakuraCat(7897) 20/20、0.43s 极稳 → 当时把 7897 定为首选；
    #     · 下午实测：**完全反过来** —— 7897 掉到 25% 失败率、平均 2.9s、最慢 11.6s，
    #                 而 10808 是 20/20、平均 1.1s。
    #   把"好的那条"写死在配置里，等于每次节点一变就要人来改一次 ——
    #   而无人值守的循环恰恰没人在。所以改成"实测 + 采用实测结果"。
    #
    #   ⚠ 两条通道都要求客户端在跑：v2rayN 与 SakuraCat 任一开着即可，
    #     两个都关掉就会报「网络不通」并等网络（这是设计好的自愈路径，不是故障）。
    "代理": "http://127.0.0.1:10808",

    # 一轮里 Codex 最多干多久（分钟）。超时会被掐掉，算这一轮失败。
    # 60 分钟：写一整章内容 + 三次自检 + 写交接单，实测不短。
    "单轮超时分钟": 60,

    # 本次运行最多几轮 / 最多多少分钟。
    # 6 轮：一次尽量把剩下的批次都跑掉（任务计划程序那边限 3 小时，留余量到 170）。
    "默认轮数": 6,
    "默认分钟": 170,

    # ★ 每批做完后，在闸门上等多久（分钟）看质检方放行是否翻牌。
    # 翻牌了就立刻接着干下一批，不必等下一次定时任务。
    # 这一条专治「等我放行」的空转：原来平均白等一小时，现在最多等这么久。
    #
    # 为什么是 45 而不是更短：质检方的质检任务本身是**每小时**跑一次，
    # 窗口太短（原来 25 分钟）命中率不到一半，白等一场还得再等下一个周期。
    # 45 分钟能覆盖大多数情况，而且等待期间只是 sleep，不烧 CPU 也不烧额度。
    "等放行分钟": 45,

    # 连续失败到这个次数就停机，别一直烧额度。
    "连续失败上限": 4,

    # 网络不通时最多等多久（分钟）。期间每分钟重测一次。
    # 等不到就退出、记「网络中断」；下一次定时任务会自动重试。
    "等网络分钟": 15,

    # ★ 判死的依据：**网络类错误刷了多少条**，而不是「安静了多久」。
    #
    #   两版教训叠在一起，值得写清楚：
    #   ① 最早那版数「输出文件大小」→ 卡死时每 4~5 分钟刷一条报错把文件撑大，
    #      于是以为它还在干活，白白空转 20 多分钟。
    #   ② 改成「4 分钟没事件就判死」→ **又矫枉过正**：模型生成一整章内容
    #      （一大块 JSON）是一次性输出，**中间根本不产生新事件**，
    #      静默几分钟是正常写作，不是死机。2026-09-19 凌晨两轮都因此在
    #      「正要动笔」的那一刻被掐掉，两轮零产出。
    #
    #   所以判据换成本质的那一条：真卡死时它会**反复**刷
    #   `Reconnecting…` / `stream disconnected`；正常写作时它是安静的。
    #
    #   而且只看**最近 5 分钟窗口内**的条数 —— 网络偶发抖一下、Codex 自己重试成功了，
    #   不该被掐掉（那会白丢已经做完的部分）；连着刷才算真卡死。
    "网络错误判死条数": 3,

    # 兜底：这么久一点事件都没有（又没在刷网络错误）才判死。
    # 放得很宽 —— 宁可多等，也不要再掐掉正在写作的它。
    "静默判死分钟": 25,

    # 网络明确断掉时，多久没进展就判死（秒）。比上面更急。
    "网络断判死秒": 90,

    # 开跑前要不要清掉上一轮留下的孤儿 Codex 进程（它们白占内存）。
    "清理残留进程": True,

    # 质检随机抽几条公式留给人看。
    "抽样条数": 8,

    # ★ 闸门：每批做完是否必须等质检方放行才继续。
    #   2026-09-19 改成 False —— 需求方的要求是「全都要、效率要高」，不等人工点头。
    #   ⚠ 关掉的只是「等人工签字」，**不等于放弃质检**：
    #     每轮照样跑 qc.py（指纹 / 覆盖门槛 / 独立复算 / 成品一致），
    #     抽样单也照样留在 outputs/质检报告.md 里，事后随时能看。
    "每批需质检方放行": False,

    # 每轮结束后是否自动 git 提交（留回退点，不会 push）。
    "自动提交": True,
}

PROJECT = os.path.abspath(os.path.join(HERE, ".."))          # D:\codex
WORK = HERE                                                  # D:\codex\work
OUTPUTS = os.path.join(PROJECT, "outputs")
LOGS = os.path.join(PROJECT, "logs")
STATE_FILE = os.path.join(WORK, "协作状态.json")
BATCH_PLAN = os.path.join(WORK, "批次计划.md")               # 进度真相，编排器写、Codex 只读
HANDOFF = os.path.join(WORK, "交接单.json")                  # Codex 写：声明它实际改了什么
CODEX_NOTE = os.path.join(WORK, "本批报告.md")               # Codex 写：给人看的说明
QC_REPORT = os.path.join(OUTPUTS, "质检报告.md")             # qc.py 生成
AYOU_NOTE = os.path.join(OUTPUTS, "质检意见.md")             # 质检方写，本脚本只读
AYOU_RECEIPT = os.path.join(WORK, "质检回执.json")           # 质检方写，本脚本只读

# 防撞锁：同一时间只允许一个循环在跑。
# 手动双击和定时任务撞上时，两个 Codex 会互相覆盖对方的文件 —— 所以必须有这把锁。
LOCK = os.path.join(WORK, ".loop.lock")

# 我们自己叫起来的那个 Codex 的进程号，记在案。
# 用途只有一个：下次开跑前，如果发现它还赖着没走（上一轮被强杀留下的孤儿），把它清掉。
# ★ 为什么要有这个文件、而不是"按进程名把 codex.exe 全杀"：
#   需求方的 ChatGPT/Codex 桌面应用**自己也常驻着一个 codex.exe**
#   （命令行 `codex.exe … app-server …`）。按名字扫会连它一起杀 —— 拆的是需求方的东西。
OUR_PID = os.path.join(WORK, ".ours_codex.pid")

# 演练模式开关。--dry-run 时置 True：只打印，不往日志/状态/指标里落任何东西。
_DRY = False


# ============================================================
# 小工具
# ============================================================
def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs():
    for d in (LOGS, OUTPUTS):
        if not os.path.isdir(d):
            os.makedirs(d)


def write_json(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def log(text):
    """往协作日志里追加一行（带时间戳），并同时打到屏幕上。

    演练模式只打印不落文件 —— 否则演练记录会混进审计流水，把"到底跑过几轮"搅浑。
    """
    line = "- [%s] %s" % (now(), text)
    if not _DRY:
        with open(os.path.join(LOGS, "协作日志.md"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    print(text)


def codex_exe():
    """找出 Codex 的命令行程序在哪。

    ★ 为什么不能把路径写死：它的目录名里带一串安装哈希
    （`bin\\cdef5aaf3e41ab53\\codex.exe`），**每次升级 Codex 都会变**。
    写死的结果就是升级之后怎么都叫不起它，而症状看起来像"Codex 坏了"，
    其实只是路径过期 —— 这个坑踩过一次，所以改成自动查找。

    查找顺序（先找到先用）：
      1) CONFIG["codex程序"] 里显式写的（默认留空，会跳过）
      2) 环境变量 CODEX_CLI_PATH
      3) ~/.codex/config.toml 里的 CODEX_CLI_PATH = '…'
      4) 扫 bin\\*\\codex.exe，取修改时间最新的那个
      5) 兜底常量
    """
    cands = []
    if CONFIG["codex程序"]:
        cands.append(CONFIG["codex程序"])
    if os.environ.get("CODEX_CLI_PATH"):
        cands.append(os.environ["CODEX_CLI_PATH"])
    try:
        cfg = os.path.join(os.path.expanduser("~"), ".codex", "config.toml")
        with open(cfg, encoding="utf-8", errors="replace") as fh:
            m = re.search(r"CODEX_CLI_PATH\s*=\s*['\"]([^'\"]+)['\"]", fh.read())
        if m:
            cands.append(m.group(1))
    except Exception:
        pass
    try:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                            "OpenAI", "Codex", "bin")
        found = glob.glob(os.path.join(base, "*", "codex.exe"))
        if found:
            # ★ 优先挑「一整套齐全」的目录：同目录里必须有 codex-code-mode-host.exe。
            #
            #   为什么（2026-09-19 踩过，整整空转两轮）：
            #   需求方更新 Codex 后，新版装进一个新目录
            #   （bin\247581e4…\，里面 codex.exe + codex-code-mode-host.exe
            #   + codex-command-runner.exe + codex-windows-sandbox-setup.exe 四件齐全），
            #   而**旧目录 bin\cdef5aaf…\ 里只剩一个孤零零的旧 codex.exe**。
            #   旧程序启动后去**自己同目录**找 code-mode-host，当然找不到 →
            #   报「本地命令执行宿主缺失，找不到指定的文件」→ 整轮空转、零产出。
            #
            #   ⚠ 只按 mtime 取最新是**不够**的：那条时间线恰好很阴 ——
            #   编排器 01:43:02 启动时，新版目录 01:43:40 才建好，
            #   差 38 秒，于是「最新」选中了旧目录。
            #   → 改用「配套文件是否齐全」判它是不是一套完整安装，再在其中取最新。
            full = [p for p in found
                    if os.path.isfile(os.path.join(os.path.dirname(p),
                                                   "codex-code-mode-host.exe"))]
            cands.append(max(full or found, key=os.path.getmtime))
    except Exception:
        pass
    cands.append(CONFIG["codex程序兜底"])
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return cands[-1]


def child_env():
    """给子进程准备环境变量。

    关键点：必须**显式指定代理**，不能靠继承。
    因为如果这个脚本是在别的 Agent 会话里被调起来的，环境里可能带着
    别的代理设置（指向一个只放行少数域名的网关），那样 Codex 会连不上
    chatgpt.com，看起来像「Codex 坏了」，其实是代理串了。
    """
    env = dict(os.environ)
    env.pop("CODEBUDDY_SERVICE_PROXY_URL", None)
    env.pop("ALL_PROXY", None)
    env.pop("all_proxy", None)
    env["HTTP_PROXY"] = CONFIG["代理"]
    env["HTTPS_PROXY"] = CONFIG["代理"]
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # 压低 Codex 自己的 tracelog 噪音（那些 ERROR: Reconnecting… 就是它打的）。
    # 不是靠这个做判断，只是让日志文件干净点、好读。
    env.setdefault("RUST_LOG", "error")
    return env


def _pid_alive_and_is(pid, exe_name="codex.exe"):
    """这个进程号现在还活着、并且映像名正好是 exe_name 吗？

    为什么要连「名字」一起核：进程号会被系统回收再利用。
    过了一段时间后，同一个号码可能已经属于别人的程序了，不能照着旧号码乱杀。
    """
    try:
        p = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        text = p.stdout.decode("gbk", "replace")
    except Exception:
        return False
    for line in text.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) == pid:
            return parts[0].lower() == exe_name.lower()
    return False


def remember_pid(pid):
    """把「我们自己叫起来的 Codex 进程号」记在案。"""
    try:
        with open(OUR_PID, "w", encoding="utf-8") as fh:
            fh.write(str(pid))
    except Exception:
        pass


def forget_pid():
    """案卷销毁（进程已经正常收工、或者刚被我们杀掉）。"""
    try:
        os.remove(OUR_PID)
    except Exception:
        pass


def kill_tree(pid, why=""):
    """杀一棵进程树（连带子进程）。

    为什么必须「连子进程一起」：Codex 干活的命令是它自己 fork 出来的，
    只杀 codex.exe 本身，那些命令进程会变成没人管的孤儿继续占内存。
    /T 就是「连子孙一起」的意思。
    """
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        return True
    except Exception:
        return False


def kill_stale_codex(tag="开跑前"):
    """清掉**上一轮我们自己留下的**那个 Codex 孤儿进程。

    为什么需要：这个脚本被强杀（或人工中止）时，它叫起来的 codex.exe
    不会跟着死，就成了孤儿 —— 既不干活，又占着几百兆内存。
    2026-09-18 实测就留了一个 PID 22908，CPU 时间纹丝不动地挂了 40 分钟。

    ★ 这里有个必须写清楚的坑（2026-09-19 踩过，而且踩得不轻）：
      早先这个函数是「按进程名把 codex.exe 全杀一遍」。但需求方的
      **ChatGPT/Codex 桌面应用自己也常驻一个 codex.exe**
      （命令行 `codex.exe … app-server …`，父进程 `ChatGPT.exe`）。
      于是每次开跑前，它都会顺手把需求方的应用进程杀掉 ——
      应用随后自动重启，所以表面上「没出事」，实际是在反复拆需求方的东西。
      → 现在**只认我们自己记录过的那个进程号**（OUR_PID 文件），绝不按名字扫。
    """
    if not CONFIG["清理残留进程"]:
        return None
    try:
        pid = int(open(OUR_PID, encoding="utf-8").read().strip())
    except Exception:
        return None
    if _pid_alive_and_is(pid, "codex.exe"):
        kill_tree(pid)
        if not _DRY:
            log("%s：清掉上一轮留下的 Codex 孤儿进程（PID %d）" % (tag, pid))
        forget_pid()
        return pid
    forget_pid()
    return None


def acquire_lock():
    """拿锁。拿到返回 True。

    用「文件存在」当锁，够用且跨平台。进程被强杀会留下锁文件，
    所以按修改时间判过期 —— 超过「单轮超时 + 15 分钟」没人刷新，就当作旧锁清掉。
    """
    stale_after = (CONFIG["单轮超时分钟"] + 15) * 60
    if os.path.exists(LOCK):
        age = time.time() - os.path.getmtime(LOCK)
        if age < stale_after:
            return False
        try:
            os.remove(LOCK)
        except Exception:
            return False
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, ("%s 启动\n" % now()).encode("utf-8"))
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except Exception:
        return False


def refresh_lock():
    """刷新锁的时间戳。干长活的时候每隔一会儿叫一次，免得被当成旧锁。"""
    try:
        os.utime(LOCK, None)
    except Exception:
        pass


def release_lock():
    try:
        os.remove(LOCK)
    except Exception:
        pass


def git(*args):
    try:
        p = subprocess.run(["git"] + list(args), cwd=PROJECT, env=child_env(),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        return p.returncode, p.stdout.decode("utf-8", "replace").strip()
    except Exception as e:
        return 1, "%s: %s" % (type(e).__name__, e)


def git_head():
    rc, out = git("rev-parse", "--short", "HEAD")
    return out if rc == 0 else "(取不到)"


# ============================================================
# 〇、批次计划 + 交接对账 —— 让两边的交互有据可查
# ============================================================
# 设计意图：以前派活只说"推进下一批"，Codex 就得自己猜"下一批是什么"，
# 于是它每轮都去翻参考文献/13 里那个**已经做完的**任务一，把补课重新"verify"一遍。
# 现在改成：编排器照《批次计划》抄，Codex 照抄的做，做完交《交接单》，
# 编排器再拿《交接单》跟文件时间、git 对账。没有任何一步靠"理解"或"信任"。
def read_plan():
    """读 work/批次计划.md 里那张表。

    返回 [{批次, 章节, 交付文件, 知识点, 公式, 检查项, 常见错误, 状态}, ...]
    只有第一列是数字的行才算数据行（表头、说明文字自动跳过）。
    """
    if not os.path.exists(BATCH_PLAN):
        return []
    rows = []
    with open(BATCH_PLAN, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
            if len(cells) < 8 or not cells[0].isdigit():
                continue

            def num(x):
                try:
                    return int(x)
                except Exception:
                    return 0

            rows.append({
                "批次": int(cells[0]), "章节": cells[1], "交付文件": cells[2],
                "知识点": num(cells[3]), "公式": num(cells[4]),
                "检查项": num(cells[5]), "常见错误": num(cells[6]),
                "状态": cells[7],
            })
    return rows


def next_batch(plan):
    """找第一个还没做完的批次。全做完了返回 None。"""
    for r in plan:
        if "已完成" not in r["状态"]:
            return r
    return None


def mark_batch_done(batch_no, note):
    """把批次计划表里某一批的状态列改成「已完成」。

    这张表归编排器写（Codex 只读），所以这里直接改文件里那一行。
    """
    if _DRY or not os.path.exists(BATCH_PLAN):
        return False
    with open(BATCH_PLAN, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    hit = False
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == str(batch_no):
            cells[-1] = "✅ 已完成（%s %s）" % (note, now())
            lines[i] = "| " + " | ".join(cells) + " |"
            hit = True
            break
    if hit:
        with open(BATCH_PLAN, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines))
    return hit


def plan_brief(plan):
    """把批次计划压成给提示词用的一小段。"""
    done = [r for r in plan if "已完成" in r["状态"]]
    todo = [r for r in plan if "已完成" not in r["状态"]]
    L = ["已完成、**不要重做**的批次："]
    if done:
        for r in done:
            L.append("- 第 %d 批：%s" % (r["批次"], r["章节"]))
    else:
        L.append("- （无）")
    L.append("")
    L.append("还没做的（只做列表里的第一个）：")
    for r in todo:
        L.append("- 第 %d 批：%s → %s" % (r["批次"], r["章节"], r["交付文件"]))
    return "\n".join(L)


def verify_handoff(batch_no, t_start):
    """★ 对账：Codex 说它干了什么 vs 实际发生了什么。

    实测教训：2026-09-18 第一轮的报告写「本批只修改了 01_kinematics.json 和
    02_newton.json」，而这两个文件的修改时间（23:16）比那一轮启动（23:44）还早 ——
    那一轮其实零产出。光看报告永远发现不了，只有对账能发现。
    """
    issues = []
    h = read_json(HANDOFF, None)
    if h is None:
        return ["没有 work/交接单.json —— 这一轮无法对账，等于不知道它到底干了什么"
                "（交接单是必交件，见 work/批次计划.md 的交接契约）"]

    claimed = h.get("声明改动") or []
    if not claimed:
        issues.append("交接单的「声明改动」是空的 —— 要么没干活，要么不敢写清楚")

    for f in claimed:
        p = os.path.join(PROJECT, str(f).replace("/", os.sep))
        if not os.path.exists(p):
            issues.append("交接单声明改了 %s，但该文件不存在" % f)
            continue
        if os.path.getmtime(p) < t_start - 120:
            issues.append("交接单声明改了 %s，但它的修改时间是 %s，早于本轮开始（%s）—— 虚报"
                          % (f,
                             time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(p))),
                             time.strftime("%H:%M:%S", time.localtime(t_start))))

    batch = {r["批次"]: r for r in read_plan()}.get(batch_no)
    if batch:
        d = os.path.join(PROJECT, batch["交付文件"].replace("/", os.sep))
        if not os.path.exists(d):
            issues.append("批次计划要求的交付文件 %s 不存在 —— 这一批没交货" % batch["交付文件"])
        elif os.path.getmtime(d) < t_start - 120:
            issues.append("交付文件 %s 本轮没被改动 —— 这一批实际上没产出" % batch["交付文件"])

    got = h.get("新增内容") or {}
    if batch and isinstance(got, dict):
        for k in ("知识点", "公式", "检查项", "常见错误"):
            v = got.get(k)
            if isinstance(v, int) and v < batch[k]:
                issues.append("交接单自报的「%s」= %d，低于批次计划的硬下限 %d"
                              % (k, v, batch[k]))

    if not (h.get("待质检方确认") or []):
        issues.append("交接单没写「待质检方确认」的问题 —— "
                      "没有具体问题的交接，等于把核验责任整个推给质检方")

    return issues


def handoff_brief():
    """把交接单压成一行，写进轮次记录，方便日后翻。"""
    h = read_json(HANDOFF, None)
    if not h:
        return "(没有交接单)"
    parts = ["批次=%s" % h.get("批次"), "目标=%s" % h.get("目标"),
             "声明改动 %d 个文件" % len(h.get("声明改动") or [])]
    g = h.get("新增内容")
    if isinstance(g, dict):
        parts.append("自报 " + "/".join("%s%d" % (k, g[k]) for k in
                                       ("知识点", "公式", "检查项", "常见错误") if k in g))
    return " ｜ ".join(str(p) for p in parts)


def wait_for_release(minutes):
    """每批做完后，在闸门上等一会儿看质检方放行是否翻牌。

    翻牌了就立刻接着干下一批 —— 省掉「退出 → 等下一次定时 → 再启动」那段空转。
    """
    if minutes <= 0:
        return False
    log("在这等质检方看一眼（最多 %d 分钟）；放行了就立刻接着干" % minutes)
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        time.sleep(30)
        if read_json(STATE_FILE, {}).get("质检方放行"):
            log("质检方放行了，接着干下一批")
            return True
    log("等了 %d 分钟没等到放行，先退出；下一次定时任务会接着跑" % minutes)
    return False


# ============================================================
# 一、质检 —— 调 work/qc.py
# ============================================================
def run_qc(round_no, seed):
    """跑一次质检。返回 (是否通过, 给人看的摘要)"""
    cmd = [sys.executable, os.path.join(WORK, "qc.py"),
           "--check", "--sample", str(CONFIG["抽样条数"]), "--seed", str(seed)]
    logfile = os.path.join(LOGS, "轮次_%03d_质检输出.txt" % round_no)
    try:
        p = subprocess.run(cmd, cwd=PROJECT, env=child_env(),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
        text = p.stdout.decode("utf-8", "replace")
        code = p.returncode
    except subprocess.TimeoutExpired:
        return False, "质检超时（600 秒）"
    except Exception as e:
        return False, "质检跑不起来：%s: %s" % (type(e).__name__, e)

    if not _DRY:
        with open(logfile, "w", encoding="utf-8") as fh:
            fh.write(text)

    # qc.py 的约定：退出码 0 = 自动环节全绿；1 = 有硬伤
    brief_parts = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("=") and "已生成" not in line:
            brief_parts.append(line)
    brief = " ｜ ".join(brief_parts[-7:])
    return code == 0, brief


def qc_summary_lines(round_no):
    """从刚生成的质检报告里抠出「结论」那一节，给提示词用。"""
    if not os.path.exists(QC_REPORT):
        return "(还没有质检报告)"
    with open(QC_REPORT, encoding="utf-8") as fh:
        text = fh.read()
    start = text.find("## 一、结论")
    if start < 0:
        return text[:800]
    end = text.find("## 二、", start)
    return text[start:end if end > 0 else start + 900].strip()


# ============================================================
# 二、派活 —— 组装给 Codex 的提示词
# ============================================================
def build_prompt(state, qc_ok, round_no, batch, plan, handoff_issues):
    """组装给 Codex 的派活指令。

    原则：**具体、可核对、一屏内**。
    - 明确第几批、交什么文件、指标下限多少（照抄《批次计划》，不给它猜的余地）
    - 明确列出已完成批次，免得它重做
    - 明确它只许写哪几类文件（一个文件一个作者）
    - 明确收工要交「交接单 + 本批报告」，并说清交接单会被拿去对账
    """
    L = []
    L.append("你是这个项目的执行方（Codex）。质检方是另一个模型（质检方），它独立跑 work/qc.py 查你。")
    L.append("")

    last = str(state.get("上轮codex") or "")
    if any(k in last for k in ("掐掉", "超时", "没有实质进展", "没有事件",
                               "退出码", "卡在重连", "网络重连",
                               "环境故障", "未能开工", "宿主缺失")):
        L.append("【注意：上一轮你没能正常收工，本轮算重来】")
        L.append("上一轮的结论是：%s" % last)
        L.append("这多半不是内容问题（是连接/网络把你卡住了，编排器把你掐掉了）。"
                 "**请从仓库现在的真实状态重新开始本批** —— 不要假设上轮写了一半的东西还在，"
                 "也不要凭印象补。开工第一件事先跑 `python work/validate.py` 看现状。")
        L.append("")

    # ★ 对账问题分两档，不能一刀切（2026-09-19 改）。
    #
    #   原来只要对账有话说，就一律写「这一轮不要开新章节」——
    #   结果撞上环境故障那两轮（Codex 连命令都没跑起来，自然没写交接单），
    #   下一轮又被要求「先处理对账」，**内容永远开不了工**，成了死循环。
    #   现在把「没有交接单」单独归为**软提醒**（多半是上一轮被掐死或环境故障，
    #   它根本没机会写），照常让它干活；只有「虚报 / 没交货 / 没产出」
    #   这类**实打实的对账冲突**才禁止开新章节。
    soft = [i for i in handoff_issues if "没有 work/交接单.json" in i]
    fatal = [i for i in handoff_issues if "没有 work/交接单.json" not in i]

    if soft:
        L.append("【提醒：上一轮没留下交接单】")
        for i in soft:
            L.append("- %s" % i)
        L.append("这多半是因为上一轮被掐死或环境故障 —— 你根本没机会写交接单，"
                 "不是你的错。**本轮照常干活**，但收工时务必把交接单补上。")
        L.append("")

    if fatal:
        L.append("【先处理这个：上一轮没对上账】")
        L.append("质检方拿文件时间和 git 对账时发现下面这些问题。请先解释清楚或修正，"
                 "**这一轮不要开新章节**：")
        for i in fatal:
            L.append("- %s" % i)
        L.append("")
    elif not qc_ok:
        L.append("【本轮先修错，不要开新章节】")
        L.append("质检**没有通过**。看 outputs/质检报告.md 的「一、结论」，把标 ✘ 的逐条修掉，"
                 "改完自己重跑质检确认变绿，再谈推进。")
        L.append("")
    else:
        L.append("【本轮任务】")
        L.append("")

    if batch:
        L.append("本批 = 第 %d 批：%s" % (batch["批次"], batch["章节"]))
        L.append("交付文件：%s" % batch["交付文件"])
        L.append("硬下限（低于这个数质检直接退回，不用讨论）："
                 "知识点 ≥ %d、公式 ≥ %d、检查项 ≥ %d、常见错误 ≥ %d"
                 % (batch["知识点"], batch["公式"], batch["检查项"], batch["常见错误"]))
    else:
        L.append("★ 批次计划已经全部完成了。不要自己开新章节 —— "
                 "在 work/本批报告.md 里写「全部完成 + 建议下一步」，然后停。")
    L.append("")
    L.append("**只做这一批。做完就停，不要顺手开下一章。**")
    L.append("")
    L.append("【进度】")
    L.append(plan_brief(plan))
    L.append("")
    L.append("【开工前只需要看这三样，别重读整个项目】")
    L.append("1. work/批次计划.md —— 进度真相（你**只读不写**）")
    L.append("2. outputs/质检意见.md —— 质检方上一轮给你的意见，**必须逐条回应**（没有就忽略）")
    L.append("3. work/kb/01_kinematics.json 或 02_newton.json —— 照抄格式")
    L.append("（确实需要时再查 AGENTS.md / 参考文献/13，不必每轮重读）")
    L.append("")
    L.append("★ **别一上来就通读 work/physkit/ 的源码。** 那是校验器的实现，不是给你照抄的模板。"
             "格式照着 kb/02_newton.json 抄就够了；写完先跑 `python work/validate.py`，"
             "**哪条报错再针对哪条去查原因**。"
             "（2026-09-19 实测：有一轮 7 分钟全耗在翻校验器源码上，"
             "内容一个字没写就被网络掐了 —— 那次是纯浪费，别重演。）")
    L.append("")
    L.append("【硬规矩】")
    L.append("- 冻结文件不许改：work/physkit/**、work/qc.py、work/net_guard.py、"
             "work/orchestrator.py。真觉得必须改 → 写 work/校验器变更.md 说明理由，不要偷偷改"
             "（改了质检直接报红，等于白干）。")
    L.append("- 一条公式只写一遍：源文件里存机器可读文本，MathML 和表达式树都从它生成。")
    L.append("- 参考值必须自己独立算一遍，不许拿公式算出来的数填回去（独立复算专门抓这个）。")
    L.append("- 情境参数必须自洽：同一条公式在各情境下代进去都要对得上。")
    L.append("- **你只许写这三类文件**：work/kb/*.json、work/交接单.json、work/本批报告.md"
             "（必要时加 work/校验器变更.md）。"
             "批次计划 / 协作状态 / 质检意见 / 质检回执 —— **归别人写，你不许动**。")
    L.append("- **不要执行任何 git 命令**（add / commit / checkout / stash 一律不要）。"
             "这台机器上你的沙箱写不了 .git，一跑就是 "
             "`fatal: Unable to create 'D:/codex/.git/index.lock': Permission denied`，"
             "纯浪费时间。存档和提交由编排器负责，你只管内容。")
    L.append("- 读 work/kb/*.json 时**不要用 PowerShell 的 ConvertFrom-Json**：它把键名当大小写不敏感，"
             "而 kb 里同时有 `G`（重力）和 `g`（重力加速度），它会直接报"
             "「字典包含重复的键」，你什么也读不到。要用 Python，或者干脆当文本读。")
    L.append("")
    L.append("【收工前必须做完这五件】")
    L.append("1. python work/validate.py —— 必须全过")
    L.append("2. python work/build_site.py —— 重新生成成品")
    L.append("3. python work/qc.py --check —— 必须全绿，有红就先修")
    L.append("4. 写 work/交接单.json（格式见下）")
    L.append("5. 写 work/本批报告.md（给人看的一屏说明）")
    L.append("")
    L.append("【work/交接单.json 的格式】")
    L.append('{')
    L.append('  "批次": %s,' % (batch["批次"] if batch else "?"))
    L.append('  "目标": "%s",' % (batch["章节"] if batch else ""))
    L.append('  "交付文件": ["%s"],' % (batch["交付文件"] if batch else ""))
    L.append('  "声明改动": ["把这一轮**实际改动**的文件路径一个个列出来"],')
    L.append('  "新增内容": {"知识点": 0, "公式": 0, "检查项": 0, "常见错误": 0},')
    L.append('  "自查": {"validate": "通过", "build_site": "通过", "qc": "通过"},')
    L.append('  "待质检方确认": ["1. 具体的、需要物理判断力的问题", "2. ..."],')
    L.append('  "下一批建议": "...",')
    L.append('  "完成时间": "YYYY-MM-DD HH:MM"')
    L.append('}')
    L.append("")
    L.append("★ **「声明改动」会被拿去对账**：质检方会把它跟每个文件的修改时间、git 记录比。"
             "虚报或漏报会当场被抓住，比少写一条公式更丢分。")
    L.append("★ 如果这一轮你其实没改动任何文件，就如实写 `[]`，"
             "并在「待质检方确认」里说明原因。"
             "**如实说「我这轮没有产出」是可以接受的；把「我验证过了」写成「我做完了」不可以。**")
    L.append("")
    L.append("★ 「待质检方确认」要写具体问题，例如"
             "「1. 斜面下滑情境里，用牛顿定律和用动能定理算出的末速度，是不是两条真正独立的路径」，"
             "不要写「请确认内容是否正确」—— 那种问题等于没问。")
    L.append("")
    L.append("【本轮质检结论原文】")
    L.append(qc_summary_lines(round_no))
    return "\n".join(L)


# ============================================================
# 三、叫 Codex 干活（带看门狗）
# ============================================================
def _render_event(o, fh):
    """把一条 --json 事件渲染成一行人能看懂的话。

    原则：**只读不猜**。字段名对不上就安静跳过，绝不因为格式变了就崩。
    Codex 的 JSON 结构以后可能会改，这个函数不该成为新的故障点。
    """
    t = o.get("type", "")
    # 只渲染 completed，不渲染 started —— 否则同一条命令会打两遍，读着像跑了两轮。
    if t == "item.completed":
        it = o.get("item") or {}
        k = it.get("type") or it.get("item_type") or ""
        tag = {
            "agent_message": "[说话]",
            "reasoning": "[思考]",
            "command_execution": "[跑命令]",
            "exec_command": "[跑命令]",
            "file_change": "[改文件]",
            "patch_apply": "[改文件]",
        }.get(k, "[%s]" % (k or "事件"))
        body = (it.get("text") or it.get("command") or it.get("aggregated_output")
                or it.get("path") or it.get("changes") or "")
        if isinstance(body, (list, dict)):
            body = json.dumps(body, ensure_ascii=False)
        body = str(body).replace("\n", " ").strip()
        if body:
            fh.write("%s %s\n" % (tag, body[:600]))
    elif t == "error":
        fh.write("[错误] %s\n" % str(o.get("message") or o)[:400])
    elif t == "turn.completed":
        fh.write("[一轮结束] %s\n" % json.dumps(o.get("usage") or {}, ensure_ascii=False)[:200])


def _read_tail(path, limit=240):
    """读一个文件最后两行，拼成一句话。读不到就返回空串。"""
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", "replace").strip()
        lines = [x for x in text.splitlines() if x.strip()]
        return " / ".join(lines[-2:])[:limit]
    except Exception:
        return ""


# ============================================================
# 额度用尽识别 —— 「资源故障」不是「内容失败」
# ============================================================
# 2026-09-19 加。为什么必须有这一段：
#
#   Codex 用 ChatGPT 账号的**用量窗口**（实测 5 小时一滚）。窗口用完时它不干活，
#   只回一句 `You've hit your usage limit … try again at 11:44 AM`，
#   退出码 1、耗时 0 分钟 —— 跟「服务端满载」「网络断流」的表面症状几乎一样。
#
#   而编排器原来一律把它当成「Codex 没正常收工」的生产失败，后果是连锁的：
#     ① 继续拿**上一轮残留的交接单**去对账 → 条条「虚报」→ 下一轮被写下
#        「这一轮不要开新章节」→ **新章节永远开不了工**；
#     ② 计入「连续失败」→ 到这个次数就停机，看着像"项目坏了"，其实只是要等窗口；
#     ③ 每 30 分钟醒一次撞同一堵墙，每次都要人手动去洗状态。
#
#   **额度是资源，不是内容。** 资源故障要的是「等」，不是「重来」。
#
# ★ 返回的文案里**刻意不出现**「退出码 / 掐掉 / 超时 / 重连」这些词：
#   build_prompt() 见到那些词就会写「上一轮你没能正常收工，本轮算重来」，
#   让 Codex 白跑一次 validate。额度问题不需要它重来，只需要等窗口重置。
QUOTA_KEYS = ("usage limit", "hit your usage limit")

# ★ 「网络抖动」的判定词（2026-09-19 12:50 加）。
#   与 QUOTA_KEYS 同一类用途：把**环境故障**从**内容失败**里摘出来。
#   来源是 run_codex() 自己吐的 reason 文案，只有这两条表示「连接被掐」：
#     "网络重连反复失败（最近 5 分钟刷了 N 条：…）"
#     "网络断了、Codex 也没进展"
#   ⚠ 故意**不含**「X 分钟没有任何事件输出」和「单轮超时」——
#     那两种既可能是连接卡死，也可能是对方在长时间生成，不能一口咬定是网络。
NET_DROP_KEYS = ("网络重连反复失败", "网络断了")


def detect_quota(hb, human_file):
    """认一认这一轮是不是「额度用尽」。返回 (是否, 说明文案)。

    报错正文可能出现在两处，都要看：
      - stdout 的 --json 事件流 → 已被 _reader 记进 hb["last_err"]
        （形如 {"type":"error","message":"…usage limit…"}，
          紧接着还会有一条 {"type":"turn.failed", …}）
      - stdout 的人读流水（human_file），形如 `[错误] You've hit your usage limit…`

    顺带把「几点重置」抠出来 —— 报错原文里就写着 `try again at 11:44 AM`，
    这对需求方有用（能直接告诉他等到几点）。
    """
    text = str(hb.get("last_err") or "")
    try:
        with open(human_file, encoding="utf-8", errors="replace") as fh:
            text += "\n" + fh.read()[-2000:]
    except Exception:
        pass

    if not any(k in text for k in QUOTA_KEYS):
        return False, ""

    when = ""
    m = re.search(r"try again at ([0-9]{1,2}:[0-9]{2}\s*[AP]M)", text)
    if m:
        when = "，报错原文说 %s 重置" % m.group(1).strip()
    # ★ 文案**不带 "Codex " 前缀**：调用处已经是 `log("…：Codex %s")`，
    #   带上就会印成「Codex Codex 额度用尽」，永久日志要干净。
    return True, "额度用尽（用量窗口用完%s）" % when


def run_codex(prompt, round_no):
    """叫 Codex 干一轮。返回 (是否正常收工, 备注)

    看门狗判断生死只看一件事：**有没有实质进展**。

    「实质进展」= Codex 用 --json 打出来的事件流里，能解析成结构化事件的行。
    裸的报错文本（`ERROR: Reconnecting…` 这类）不算 —— 这正是老看门狗的盲区：
    它数的是输出文件大小，而卡死时每隔几分钟吐一条报错，把文件撑大了，
    于是它以为"还在干活"。2026-09-18 那轮就是这样白空转了 20 多分钟。

    判死条件（满足任一即掐掉）：
      - 总时长超过上限                → 单轮超时
      - 静默判死分钟内一条事件都没有  → 卡住了（**不再要求网络是断的**）
      - 网络明确断了且 90 秒无事件    → 网络断了空转

    掐的时候连子进程一起杀（taskkill /T），本轮结束再扫一遍残留 ——
    免得留下像 2026-09-18 那个 CPU 纹丝不动地挂了 40 分钟的孤儿进程。
    """
    ensure_dirs()
    prompt_file = os.path.join(LOGS, "轮次_%03d_派活.md" % round_no)
    with open(prompt_file, "w", encoding="utf-8") as fh:
        fh.write(prompt)

    # 四个文件各司其职，别混着用：
    events_file = os.path.join(LOGS, "轮次_%03d_codex事件.jsonl" % round_no)  # 机器读：判心跳
    err_file = os.path.join(LOGS, "轮次_%03d_codex错误.log" % round_no)       # 人读：排障
    human_file = os.path.join(LOGS, "轮次_%03d_codex输出.txt" % round_no)     # 人读：看得懂的流水
    lastfile = os.path.join(LOGS, "轮次_%03d_最后回复.md" % round_no)         # 最后一段话

    exe = codex_exe()
    print("    Codex 程序：%s" % exe)
    cmd = [
        exe, "exec",
        "--skip-git-repo-check",
        "-C", PROJECT,
        "-s", "workspace-write",     # 允许它在项目目录里写文件
        "-c", "notify=[]",           # 关掉桌面通知，免得无人值守时弹窗
        "-c", "model_reasoning_effort=%s" % CONFIG["推理档"],   # ★ 降推理档提速（见 CONFIG 注释）
        "--json",                    # ★ 事件流：看门狗靠它判断"还活着没有"
        "-o", lastfile,              # 把它的最后一段话单独存一份
        prompt,
    ]

    print("    叫 Codex 开工（最多 %d 分钟）…" % CONFIG["单轮超时分钟"])
    t0 = time.time()
    hard_timeout = CONFIG["单轮超时分钟"] * 60
    silent_limit = CONFIG["静默判死分钟"] * 60
    net_dead_limit = CONFIG["网络断判死秒"]
    neterr_limit = CONFIG["网络错误判死条数"]

    try:
        proc = subprocess.Popen(cmd, cwd=PROJECT, env=child_env(),
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        return False, "叫不起来 Codex：%s: %s" % (type(e).__name__, e)

    remember_pid(proc.pid)      # 记在案：它万一成了孤儿，下次开跑前好认领

    # 心跳字典，两个读线程共用：
    #   t       = 最后一次收到「结构化事件」的时刻
    #   events  = 收到过多少条事件
    #   neterr  = 累计刷了多少条网络类错误
    #   err_ts  = 这些错误出现的时刻（判死只看**最近 5 分钟内**刷了几条 ——
    #             网络偶发抖一下、Codex 自己重试成功，不该被掐掉；
    #             连着刷才算真卡死）
    hb = {"t": time.time(), "events": 0, "neterr": 0, "err_ts": []}

    # 网络类错误的特征串 —— 只留**真正表示连接断了**的。
    #
    #   "Reconnecting…"     重连计数，最硬的信号
    #   "stream disconnected" 流被掐断
    #   "tls handshake"      握手失败
    #   "idle timeout"       长连接空转超时
    #
    # ★ 曾经把 "failed to refresh available models: timeout waiting for child process to exit"
    #   也算了进来 —— **这是错的**。实测（2026-09-19 01:28）一个成功跑完的短任务，
    #   stderr 里照样有这条。它是噪音，不是故障；算进来会让正常工作的轮次被误判成卡死。
    NETERR_KEYS = ("Reconnecting", "stream disconnected",
                   "tls handshake", "idle timeout")

    def _reader():
        """读 stdout 的 --json 事件流：落文件、刷心跳、顺手把网络错误数出来。

        ★★ 这里有一条必须写死的规矩：**`type == "error"` 的条目不算进展，不刷心跳。**

        为什么（2026-09-19 01:22 实测）：`Reconnecting…` 这类报错**是以 JSON 事件的形式**
        出现在 stdout 事件流里的（不是只在 stderr！），长这样：
            {"type":"error","message":"Reconnecting... 2/5 (stream disconnected …)"}
        如果把它当普通事件，每来一条报错心跳就被刷新一次，
        于是静默兜底永远等不到 —— 阈值形同虚设，它会一直卡到天荒地老。
        **报错不是进展。** 这个坑前后栽过三次（数文件大小 / 数错误条数只数 stderr /
        错误事件刷心跳），这次连判定和计数一起焊死。
        """
        try:
            with open(events_file, "wb") as ef, open(human_file, "w", encoding="utf-8") as hf:
                for raw in proc.stdout:
                    ef.write(raw)
                    ef.flush()
                    try:
                        o = json.loads(raw.decode("utf-8", "replace").strip())
                    except Exception:
                        continue        # 解析不了的（tracelog 噪音）不算数
                    if not isinstance(o, dict):
                        continue
                    if o.get("type") == "error":
                        msg = str(o.get("message") or "")
                        if any(k in msg for k in NETERR_KEYS):
                            hb["neterr"] += 1
                            hb["err_ts"].append(time.time())
                        hb["last_err"] = msg[:200]
                    else:
                        hb["t"] = time.time()      # 只有真事件才算「还活着」
                        hb["events"] += 1
                    _render_event(o, hf)
                    hf.flush()
        except Exception:
            pass

    def _err_reader():
        """Codex 的 tracelog 走 stderr。存给人排障，**顺便数网络类错误**。"""
        try:
            with open(err_file, "wb") as ef:
                for raw in proc.stderr:
                    ef.write(raw)
                    ef.flush()
                    line = raw.decode("utf-8", "replace")
                    if any(k in line for k in NETERR_KEYS):
                        hb["neterr"] += 1
                        hb["err_ts"].append(time.time())
        except Exception:
            pass

    threading.Thread(target=_reader, daemon=True).start()
    threading.Thread(target=_err_reader, daemon=True).start()

    reason = ""
    while True:
        if proc.poll() is not None:
            break
        time.sleep(5)
        refresh_lock()      # 干长活时不断刷新，免得锁被当成过期
        elapsed = time.time() - t0
        idle = time.time() - hb["t"]

        if elapsed > hard_timeout:
            reason = "单轮超时（%d 分钟）" % CONFIG["单轮超时分钟"]
            break

        # ① 主判据：网络类错误反复刷 —— 这才是「真卡在重连」的铁证。
        recent_err = [t for t in hb["err_ts"] if (time.time() - t) <= 300]
        if len(recent_err) >= neterr_limit:
            reason = "网络重连反复失败（最近 5 分钟刷了 %d 条：%s）" % (
                len(recent_err), str(hb.get("last_err", ""))[:90])
            break

        # ② 网络明确断了、它也不动 —— 不必等。
        #
        #    ★ 这里必须用 handshake=False（只判活）。原因（2026-09-19 12:50 踩到）：
        #    这一句在 idle 超过阈值后**每 5 秒**执行一次（阈值 90 秒，见 CONFIG）。
        #    如果它也去真握手，等于每分钟往代理里塞十几次真实 TLS 连接；
        #    万一握手失败还要逐个端口试过去，连看门狗自己的节奏一起拖住。
        #    判活用它、**选通道不用它**（选通道在 wait_for_net 里，那里是真握手）。
        if idle >= net_dead_limit and not net_guard.evaluate(handshake=False)["通"]:
            reason = "网络断了、Codex 也没进展"
            break

        # ③ 兜底：非常久完全没有事件、又不在刷网络错误，才判死。
        #    阈值故意放得很宽 —— 它可能在一次性生成整章内容，那期间本就没有事件。
        if idle >= silent_limit:
            reason = "%.0f 分钟没有任何事件输出" % (idle / 60)
            break

    if reason:
        print("    ⚠ %s —— 掐掉 Codex（连子进程一起）" % reason)
        kill_tree(proc.pid)
        try:
            proc.wait(timeout=20)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        forget_pid()        # 已经连树杀干净了，案卷销毁
        return False, reason

    try:
        proc.wait(timeout=20)
    except Exception:
        pass
    forget_pid()

    used = int(time.time() - t0)

    # ★ 先认「额度用尽」—— 它是资源窗口用完了，不是内容失败（见本文件 detect_quota 注释）。
    #   必须放在「退出码 != 0」之前：额度问题的现象正好是「退出码 1 + 0 分钟」，
    #   先走下面那条就会把它写成「Codex 退出码 1」，于是被当成生产失败。
    quota_hit, quota_note = detect_quota(hb, human_file)
    if quota_hit:
        return False, quota_note

    if proc.returncode != 0:
        tail = _read_tail(err_file, 240)
        return False, "Codex 退出码 %s（用了 %d 分钟）%s" % (
            proc.returncode, used // 60, ("｜" + tail) if tail else "")
    if hb["events"] == 0:
        return False, "Codex 说收工了，但事件流里一条记录都没有（疑似根本没跑起来）"

    # ★ 识别它**自己报告的**环境故障。
    #
    #   为什么必须有这一段（2026-09-19 实测，空转两轮的元凶）：
    #   需求方更新 Codex 后，新版装进新目录，而自动查找选中了旧目录里那个
    #   「孤零零的 codex.exe」（同目录没有 codex-code-mode-host.exe）。
    #   Codex 于是回报「本轮未能开工：本地命令执行宿主缺失」——
    #   退出码 0、事件流也有记录，编排器**误判成「正常收工」**；
    #   接着对账发现没有交接单 → 下一轮被判「先处理对账、不许开新章节」，
    #   于是它连"第三章"都开不了工，**内容永远产不出来**。
    #   → 它说「未能开工」就是没开工。如实记成失败，下一轮才会从现状重来。
    note = ""
    try:
        with open(lastfile, encoding="utf-8", errors="replace") as fh:
            note = fh.read()
    except Exception:
        pass
    ENV_FAIL = ("未能开工", "宿主缺失", "找不到指定的文件", "执行环境故障",
                "code-mode-host", "执行宿主缺失")
    hit = [k for k in ENV_FAIL if k in note]
    if hit:
        return False, "Codex 自报环境故障（命中「%s」）：%s" % (
            hit[0], " ".join(note.split())[:150])

    return True, "正常收工（用了 %d 分钟，%d 个事件）" % (used // 60, hb["events"])


# ============================================================
# 四、等网络
# ============================================================
def wait_for_net(minutes):
    """网络不通时等一会儿。通了返回 True。"""
    deadline = time.time() + minutes * 60
    first = True
    while True:
        net = net_guard.evaluate()
        if net["通"]:
            if not first:
                log("网络恢复了（走 %s 的 %s 通道）" % (net["端口"], net["协议"]))
            return True, net
        if first:
            log("网络不通：%s" % net["说明"])
            log("开始等网络，最多 %d 分钟（每分钟重测）" % minutes)
            first = False
        if time.time() >= deadline:
            return False, net
        time.sleep(60)


# ============================================================
# 五、主流程
# ============================================================
def main(argv):
    global _DRY
    dry_run = "--dry-run" in argv
    no_codex = "--no-codex" in argv
    force = "--force" in argv
    _DRY = dry_run        # 演练模式：不落日志、不改状态、不改批次计划
    rounds = CONFIG["默认轮数"]
    minutes = CONFIG["默认分钟"]
    for i, a in enumerate(argv):
        if a == "--rounds" and i + 1 < len(argv):
            rounds = int(argv[i + 1])
        if a == "--minutes" and i + 1 < len(argv):
            minutes = int(argv[i + 1])

    ensure_dirs()
    state = read_json(STATE_FILE, {
        "阶段": "空闲", "轮次": 0, "连续失败": 0, "质检方放行": True,
        "上次结果": "", "停机原因": "", "最后更新": "", "历史": [],
    })

    # 只报状态
    if "--status" in argv:
        print("=" * 62)
        print("协作状态")
        print("=" * 62)
        for k in ("阶段", "轮次", "连续失败", "质检方放行", "上次结果", "停机原因", "最后更新"):
            print("%-8s %s" % (k, state.get(k, "")))
        print()
        print("最近 5 轮：")
        for h in state.get("历史", [])[-5:]:
            print("  第%s轮  %s  质检=%s  %s" % (h.get("轮次"), h.get("时间"),
                                                 h.get("质检"), h.get("备注", "")))
        return 0

    # 只报批次计划
    if "--plan" in argv:
        plan = read_plan()
        print("=" * 62)
        print("批次计划（唯一进度真相）")
        print("=" * 62)
        for r in plan:
            print("  第 %d 批  %-26s %-32s %s"
                  % (r["批次"], r["章节"][:26], r["交付文件"], r["状态"]))
        b = next_batch(plan)
        print()
        print("下一批    %s" % ("第 %d 批 · %s" % (b["批次"], b["章节"]) if b else "（全部完成）"))
        out = read_json(AYOU_RECEIPT, None)
        if out:
            print("质检回执  %s" % out.get("结论", ""))
        return 0

    print("=" * 62)
    print("协作循环 · Codex 干活 + 质检把关")
    print("=" * 62)
    print("项目目录  %s" % PROJECT)
    print("本次上限  %d 轮 / %d 分钟" % (rounds, minutes))
    _plan = read_plan()
    _b = next_batch(_plan)
    print("本批目标  %s" % ("第 %d 批 · %s → %s" % (_b["批次"], _b["章节"], _b["交付文件"])
                            if _b else "（批次计划已全部完成）"))
    if dry_run:
        print("模式      演练（只打印，不改任何文件）")
    print()

    # ---- 拿防撞锁：手动双击和定时任务不许同时跑 ----
    if not dry_run:
        if not acquire_lock():
            print("已经有一个循环在跑了（锁文件 %s）。" % LOCK)
            print("等它结束再来。如果确定它是被打断留下的僵尸锁，删掉那个文件即可。")
            return 2
        atexit.register(release_lock)

    # ★ 开跑前先清干净：上一轮若被强杀，会留下不干活的 Codex 孤儿进程占内存。
    if not dry_run:
        kill_stale_codex(tag="开跑前")

    log("=== 开始一次运行：最多 %d 轮 / %d 分钟 ===" % (rounds, minutes))

    deadline = time.time() + minutes * 60
    stop_reason = "跑满了本次上限"

    for n in range(1, rounds + 1):
        round_no = int(state.get("轮次", 0)) + 1
        label = "第 %d 轮（累计第 %d 轮）" % (n, round_no)

        if time.time() >= deadline:
            stop_reason = "本次时间用完了"
            break

        # ---- 闸门 ----
        if not force and not state.get("质检方放行", True):
            stop_reason = "在等质检方放行（每批做完会停一次，这是配置里开的）"
            state["阶段"] = "待质检方放行"
            log("%s：%s" % (label, stop_reason))
            break

        # ---- 网络体检 ----
        print("[%s] 网络体检…" % label)
        ok, net = wait_for_net(CONFIG["等网络分钟"] if not dry_run else 0)
        if not ok:
            state.update({"阶段": "网络中断", "停机原因": net["说明"], "最后更新": now()})
            if not dry_run:
                write_json(STATE_FILE, state)
            log("%s：网络一直不通，先退出。%s" % (label, net["说明"]))
            log("→ 请在 v2rayN 里换一个节点（日本节点基本全挂，选美国的）。"
                "下一次定时任务会自己接着跑。")
            stop_reason = "网络中断"
            break
        print("    通（%s 端口 %s）" % (net["协议"], net["端口"]))

        # ---- ★ 体检通道 = 干活通道（2026-09-19 改）----
        #
        #   原来这两条是分开的：体检按 net_guard.CANDIDATE_PORTS 的顺序挑着试，
        #   而 Codex 干活走的是 CONFIG["代理"] 里写死的那个端口，**两者互不知情**。
        #   于是会出现「体检报通、Codex 却连不上」——2026-09-19 实测就栽了一次：
        #   写死的 7897(SakuraCat) 掉到 25% 失败率，而体检因为只发 CONNECT
        #   （代理可以在本地就答掉、根本不拨上游）照样报"通"，
        #   Codex 于是连撞三轮超时。
        #
        #   现在改成：**体检实测通哪条，本轮就用哪条**。
        #   这样「体检说通」和「Codex 能用」不再是两件事，而是一件事。
        #   CONFIG["代理"] 退化成兜底默认值（只有体检没给出端口时才用得上）。
        if net.get("协议") == "http" and net.get("端口"):
            picked = "http://127.0.0.1:%d" % net["端口"]
            if CONFIG["代理"] != picked:
                log("%s：干活通道跟着体检切到 %s（实测这条通）" % (label, picked))
            CONFIG["代理"] = picked

        # ---- 轮前质检：决定这轮是「推进」还是「修错」 ----
        seed = 20260918 + round_no
        qc_ok, brief = run_qc(round_no, seed)
        print("[%s] 轮前质检：%s" % (label, "全绿" if qc_ok else "有红"))
        log("%s：轮前质检 %s ｜ %s" % (label, "全绿" if qc_ok else "有红", brief))

        if no_codex:
            state.update({"阶段": "已质检", "上次结果": brief, "最后更新": now()})
            if not dry_run:
                write_json(STATE_FILE, state)
            stop_reason = "只做质检（--no-codex）"
            break

        # ---- 组装派活指令：照《批次计划》抄，不给它猜的余地 ----
        plan = read_plan()
        batch = next_batch(plan)

        # ★ 2026-09-19：全部批次都已完成 —— 直接收工，**不再叫 Codex**。
        #   原来这里会派一个「收尾批次」的活，让 Codex 再跑一遍校验；
        #   于是每轮都重复空跑，而计划任务每 30 分钟拉起一次 —— 白烧一次额度。
        #   实测：04:42 六章全部交付后，仍空转了 7 轮（期间 Codex 额度耗尽，
        #   每轮都报 usage limit，看着像"项目坏了"，其实只是没活可干）。
        #   现在：没有待做批次 = 本轮正常结束，不启动 Codex。
        if batch is None:
            state.update({
                "阶段": "全部完成",
                "停机原因": "六批全部已完成（质检+对账通过），无待做批次 —— 正常收工",
                "最后更新": now(),
            })
            if not dry_run:
                write_json(STATE_FILE, state)
            log("%s：批次计划已无待做批次 —— 全部完成，正常收工（不调用 Codex）" % label)
            stop_reason = "全部批次已完成"
            break

        prior_issues = state.get("上轮对账问题") or []
        prompt = build_prompt(state, qc_ok, round_no, batch, plan, prior_issues)

        if dry_run:
            print()
            print("---- 演练：将要交给 Codex 的提示词 %d 字 ----" % len(prompt))
            print(prompt)
            print("---- 演练结束 ----")
            stop_reason = "演练模式，只跑一轮就停"
            break

        # ---- 叫 Codex ----
        log("%s：派活给 Codex 了 → 第 %s 批 · %s（任务书见 logs/轮次_%03d_派活.md）"
            % (label, batch["批次"] if batch else "?", batch["章节"] if batch else "收尾",
               round_no))
        t_start = time.time()
        ok, note = run_codex(prompt, round_no)
        state["上轮codex"] = note          # 下一轮派活据此判断"上轮是不是被掐死的"
        log("%s：Codex %s" % (label, note))

        # ---- ★ 对账：它说它干了什么 vs 实际发生了什么 ----
        #
        # ★★ 只有「这一轮真的开工并且正常收工」时，对账才有意义（2026-09-19 改）。
        #
        #   否则磁盘上留着的是**上一轮的** work/交接单.json —— 拿它去跟本轮的起始时间比，
        #   它声明的每个文件时间都早于本轮开始，于是条条判「虚报」，还会附一条
        #   「交付文件本轮没被改动 —— 这一批实际上没产出」。**这是一场凭空伪造出来的
        #   对账冲突**：Codex 压根没参与，却被判了"对上账没通过"。
        #
        #   而下一轮 build_prompt() 会把这些"实打实的对账冲突"翻译成
        #   **「先处理这个：上一轮没对上账 …… 这一轮不要开新章节」** ——
        #   于是新章节永远开不了工，循环每 30 分钟原地打转。
        #   （2026-09-19 实测：额度用尽后连续 7 轮都栽在这上面。）
        #
        #   所以：没正常收工 → **不重算**，保留上一轮的原值。
        #   语义上也更干净 —— 对账的对象是「本轮那份交接单」，本轮没交，就没什么可对的。
        if ok:
            handoff_issues = verify_handoff(batch["批次"] if batch else -1, t_start)
        else:
            handoff_issues = state.get("上轮对账问题") or []
            log("%s：Codex 本轮没正常收工 → 不重算对账（保留上一轮原值，避免伪造冲突）"
                % label)
        log("%s：对账 %s ｜ %s"
            % (label, "通过" if not handoff_issues else "%d 个问题" % len(handoff_issues),
               handoff_brief()))
        for i in handoff_issues:
            log("    ⚠ %s" % i)
        state["上轮对账问题"] = handoff_issues

        # ---- 轮后质检 ----
        qc_ok2, brief2 = run_qc(round_no, 20260918 + round_no)
        log("%s：轮后质检 %s ｜ %s" % (label, "全绿" if qc_ok2 else "有红", brief2))

        # 三样都过才算这一轮通过：Codex 正常收工 + 质检全绿 + 对账没毛病
        passed = bool(ok and qc_ok2 and not handoff_issues)

        # ---- git 留个回退点 ----
        if CONFIG["自动提交"]:
            git("add", "-A")
            rc, out = git("commit", "-m",
                          "第 %s 批 第 %d 轮：Codex 产出（质检%s／对账%s）"
                          % (batch["批次"] if batch else "?", round_no,
                             "通过" if qc_ok2 else "未过",
                             "通过" if not handoff_issues else "有问题"))
            if out and "nothing to commit" not in out:
                log("已提交一个回退点：%s" % out.splitlines()[0][:70])

        # ---- 记状态 ----
        entry = {
            "轮次": round_no,
            "时间": now(),
            "批次": batch["批次"] if batch else None,
            "网络": "%s:%s" % (net["协议"], net["端口"]),
            "codex": note,
            "对账": "通过" if not handoff_issues else "%d 个问题" % len(handoff_issues),
            "交接单": handoff_brief(),
            "git": git_head(),
            "质检": "通过" if passed else "未过",
            "备注": brief2[:140],
        }
        state.setdefault("历史", []).append(entry)
        state["轮次"] = round_no
        state["最后更新"] = now()

        if passed:
            state["连续失败"] = 0
            state.pop("停机原因", None)
            state.pop("上轮对账问题", None)
            state["阶段"] = "空闲"
            state["上次结果"] = brief2
            write_json(STATE_FILE, state)

            # 把《批次计划》里那一行标成已完成（这张表归编排器写，Codex 只读）
            if batch:
                mark_batch_done(batch["批次"], "质检+对账通过")
                log("《批次计划》已把第 %d 批标为已完成" % batch["批次"])

            if CONFIG["每批需质检方放行"]:
                state["质检方放行"] = False
                state["阶段"] = "待质检方放行"
                write_json(STATE_FILE, state)
                log("本轮合格。关上闸门等质检方看抽样单（最多 %d 分钟）"
                    % CONFIG["等放行分钟"])
                if wait_for_release(CONFIG["等放行分钟"]):
                    # ★ 注意：闸门只由质检方开。这里绝不代它打开 —— 否则闸门就形同虚设。
                    state["阶段"] = "空闲"
                    write_json(STATE_FILE, state)
                    continue
                stop_reason = "等质检方放行超时（下一次定时任务会接着跑）"
                break
        elif "额度用尽" in note:
            # ★★ 资源故障 ≠ 生产失败（2026-09-19 加）。
            #
            #   额度用尽要的是「等窗口重置」，不是「让 Codex 重来」。
            #   所以这里**不做**下面那套失败处置：
            #     - 不计入「连续失败」（否则攒到上限就停机，看着像项目坏了，
            #       其实只是要等到点，白让人半夜爬起来看）
            #     - 不置「修错中」（它压根没有错要修，这个状态有误导性）
            #     - 不当成「对账没过」（对账已在上一步跳过，见上面那段注释）
            #   本轮到此为止，避免同一堵墙连撞 6 次；下一次定时任务到时自动重试，
            #   窗口一开就接着干活。
            state["阶段"] = "等额度窗口"
            state["停机原因"] = ("Codex 额度用尽（用量窗口未重置）—— "
                                "等窗口重置后由下一次定时任务自动重试")
            write_json(STATE_FILE, state)
            log("Codex 额度用尽：本轮不计入连续失败，保留原状退出；等窗口重置自动重试。")
            stop_reason = "等额度窗口"
            break
        elif any(k in note for k in NET_DROP_KEYS):
            # ★★ 网络抖动 ≠ 生产失败（2026-09-19 12:50 加，与「额度用尽」同一类）。
            #
            #   实测背景：12:05 起网络明显劣化，批次 18 连撞三轮
            #   （`Reconnecting… peer closed connection without sending TLS close_notify`），
            #   而 07:43~09:15 那 10 批是 12 次里成功 10 次。
            #   看门狗因为长连接被掐而终止一轮时，**Codex 本身并没有问题**。
            #
            #   把它计入「连续失败」的后果很具体：攒到上限就停机 →
            #   此后每次定时任务只试 1 轮就停 → 一个网络抖动期里几乎推不动。
            #   网络该走它自己的路径：**下一轮的「网络体检」会如实判定通不通**，
            #   不通就等、通了就继续（那是原本就设计好的自愈路径）。
            #   所以这里不计失败，直接进下一轮再试。
            state["阶段"] = "网络抖动"
            state["停机原因"] = "网络抖动（长连接被掐）：%s" % note[:120]
            write_json(STATE_FILE, state)
            log("本轮判定为网络抖动（不是内容失败）→ 不计入连续失败，紧接着再试一轮。")
            continue
        else:
            why = []
            if not ok:
                why.append("Codex 没正常收工")
            if not qc_ok2:
                why.append("质检有红")
            if handoff_issues:
                why.append("对账没过")
            state["连续失败"] = int(state.get("连续失败", 0)) + 1
            state["阶段"] = "修错中"
            state["上次结果"] = brief2
            write_json(STATE_FILE, state)
            log("本轮未通过（%s）。下一轮会先要求它处理这些，不会开新章节。"
                % "、".join(why))
            if state["连续失败"] >= CONFIG["连续失败上限"]:
                state["阶段"] = "已停机"
                state["停机原因"] = "连续 %d 轮没过（%s）" % (state["连续失败"], "、".join(why))
                write_json(STATE_FILE, state)
                log("连续 %d 轮不合格，停机。需要人看一眼再决定。" % state["连续失败"])
                stop_reason = "连续失败停机"
                break

    # ---- 收尾 ----
    state["最后更新"] = now()
    if not dry_run:
        write_json(STATE_FILE, state)
    print()
    print("=" * 62)
    print("本轮运行结束：%s" % stop_reason)
    print("状态文件：%s" % STATE_FILE)
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
