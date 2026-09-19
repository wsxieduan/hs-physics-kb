# -*- coding: utf-8 -*-
"""
网络体检 —— Codex 干活前的前置检查（纯标准库，无依赖）。

为什么需要它：
    Codex 干活要连 chatgpt.com。本机靠 v2rayN 代理出去，而 v2rayN 的节点
    会掉。节点一掉，Codex 不会立刻报错，而是「静默卡死」——它会一直重连、
    重试 5 次、再退回 HTTPS，最后停在那里什么都不干。
    所以每一轮开跑前必须先体检：通就开跑，不通就等，等回来再继续。
    这样不会浪费轮次，也不会把「网络问题」误判成「Codex 干不了」。

用法：
    python work/net_guard.py                  # 体检，自动识别端口与协议
    python work/net_guard.py --port 10808     # 只看某个端口
    python work/net_guard.py --target chatgpt.com

退出码：0 = 通；1 = 不通。
"""

import socket
import ssl
import sys
import time

# 体检目标：Codex 真正要连的域名。
TARGET_HOST = "chatgpt.com"
TARGET_PORT = 443

# 常见的本地代理端口。v2rayN 默认 SOCKS=10808、HTTP=10809，
# 但实际装出来的配置可能只开一个混合入站，所以两个都试。
#
# ★ 顺序 = 「先试哪条」的倾向。但**顺序不是保证，实测才是** —— 2026-09-19 同一天里
#   这个排序被实测打翻过两次：
#     · 上午：7897(SakuraCat) 20/20、10808(v2rayN) 15% 失败 → 把 7897 提到首位；
#     · 下午：**完全反过来** —— 7897 掉到 **15/20（25% 失败）**、平均 2.9s、
#       最慢 11.6s（清一色 TLS 握手超时），而 10808 是 **20/20、平均 1.1s**。
#   → 教训：**哪条通道好是会变的**（节点被限速、客户端被关掉……）。
#     所以「好的那条端口」不能写死在配置里当真理。
#     真正的机制是：**本文件负责实测，orchestrator 负责采用实测结果**
#     （见 orchestrator.py 里「体检通道 = 干活通道」那一段）。
#     这里只按最近一次实测把相对好的排前面。
CANDIDATE_PORTS = [10808, 7897, 10809, 1080, 7890]


def port_open(host, port, timeout=0.5):
    """本机某个端口有没有在监听。"""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def http_connect_test(proxy_port, host=TARGET_HOST, port=TARGET_PORT, timeout=8,
                      handshake=True):
    """按 HTTP 代理协议发一次 CONNECT；handshake=True 时**还要跟目标真的握手**。

    ⚠ **handshake=False 是给「高频判活」用的，不能用来选通道**
    （2026-09-19 12:50 补这个参数的原因）：
    看门狗在对方沉默时会**每 5 秒**问一次「网络还通吗」。那里如果也真握手，
    等于每分钟往代理里塞十几次真实 TLS 连接；而且万一握手失败，一次要逐个端口
    试过去，把看门狗自己的节奏也拖住（实测阈值：沉默 90 秒起判、每 5 秒一轮）。
    **高频判活只判活；只有「开跑前选通道」才真握手。**

    ★★ 为什么非要做握手不可（2026-09-19 被实测打醒，这是本文件最重要的一处改动）：

    老判据是「代理回了 `200 Connection established` 就算通」。**它会骗人。**
    因为代理完全可以在**本地**就把 CONNECT 答掉、根本不去拨上游，
    真正拨号推迟到有数据要转发的时候。

    同一个出口、同一天，两种判据给出的结论天差地别：

        只发 CONNECT          → 20/20 全通，平均 0.01s        （"完美"）
        改成真握手 + 真读响应  → 15/20（**25% 失败**），平均 2.9s，最慢 11.6s
                                 （失败全是 TLS 握手超时）

    也就是说：**旧判据会给一条烂隧道盖上"通"的章**。
    而 Codex 靠 WebSocket 长连接，25% 的建连失败率足以让它一轮轮超时 ——
    2026-09-19 中午就是这么连撞三轮的（日志里 `Reconnecting… (request timed out)`）。

    所以现在的判据是：CONNECT 之后**必须真的握手成功**（隧道能跑数据）才算通。
    宁可多花几百毫秒，也不给"检查通过"这个假安心。

    返回 (是否成功, 说明文字)
    """
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", proxy_port))
        req = ("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n"
               "Proxy-Connection: keep-alive\r\n\r\n" % (host, port, host, port))
        s.sendall(req.encode("ascii"))
        buf = s.recv(256)
        if not buf:
            return False, "代理没有任何回应就断开了"
        text = buf.decode("latin-1", "replace").split("\r\n")[0]
        if not (" 200" in text or " 201" in text):
            return False, "代理拒绝了：%s" % text.strip()

        if not handshake:
            # 只判活：代理答应了就算数。够快，但**不能用来选通道**（见上面注释）。
            return True, "代理已应答（未做握手，仅判活）"

        # ★ 关键的一步：真握手。过不去，这条隧道就是不通。
        t0 = time.time()
        ctx = ssl.create_default_context()
        ts = ctx.wrap_socket(s, server_hostname=host)
        tls_s = time.time() - t0
        try:
            ts.close()
        except Exception:
            pass
        return True, "隧道可用（CONNECT 成功 + TLS 握手 %.2fs）" % tls_s
    except socket.timeout:
        return False, ("CONNECT 过了但 TLS 握手超时（节点多半是半死的）"
                       if handshake else "连上代理后等待超时（节点多半是死的）")
    except ssl.SSLError as e:
        return False, "TLS 握手失败：%s" % e
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
    finally:
        try:
            s.close()
        except Exception:
            pass


def socks5_test(proxy_port, host=TARGET_HOST, port=TARGET_PORT, timeout=8):
    """按 SOCKS5 代理协议握手并请求连接目标。"""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", proxy_port))
        # 握手：支持「无认证」
        s.sendall(b"\x05\x01\x00")
        buf = s.recv(2)
        if len(buf) < 2 or buf[0] != 0x05:
            return False, "不是 SOCKS5 协议"
        if buf[1] == 0xFF:
            return False, "SOCKS5 要求认证，脚本不支持"
        # 请求连接目标域名
        raw = host.encode("ascii")
        payload = (b"\x05\x01\x00\x03" + bytes([len(raw)]) + raw
                   + port.to_bytes(2, "big"))
        s.sendall(payload)
        rep = s.recv(10)
        if len(rep) < 2:
            return False, "SOCKS5 没有返回结果"
        code = rep[1]
        if code == 0x00:
            return True, "SOCKS5 隧道建立成功"
        meanings = {
            0x01: "代理内部故障", 0x02: "规则不允许", 0x03: "网络不可达",
            0x04: "目标不可达", 0x05: "目标拒绝连接", 0x06: "TTL 超时",
            0x07: "命令不支持", 0x08: "地址类型不支持",
        }
        return False, "SOCKS5 失败：%s" % meanings.get(code, "错误码 %d" % code)
    except socket.timeout:
        return False, "连上代理后等待超时（节点多半是死的）"
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
    finally:
        s.close()


def evaluate(ports=None, host=TARGET_HOST, target_port=TARGET_PORT, handshake=True):
    """整体体检。返回一个 dict，供编排脚本判断。

    结果里的 "通" 为 True 才算网络正常。

    · handshake=True（默认）：**真握手**。用于「开跑前决定用哪条通道」——
      这是唯一能给「这条隧道真的能跑数据」背书的方式。
    · handshake=False：只判活，快。用于循环内部的**高频判活**
      （看门狗每 5 秒问一次「网络还通吗」），**不许拿它做通道选择**。
    """
    ports = ports or CANDIDATE_PORTS
    listening = [p for p in ports if port_open("127.0.0.1", p)]

    result = {
        "目标": "%s:%d" % (host, target_port),
        "在听的端口": listening,
        "端口": None,
        "协议": None,
        "通": False,
        "说明": "",
        "检查时间": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    if not listening:
        result["说明"] = "本地没有任何代理端口在监听 —— v2rayN 可能没启动"
        return result

    # 优先试 HTTP（Codex 走的是 HTTP_PROXY/HTTPS_PROXY 环境变量）
    for p in listening:
        ok, why = http_connect_test(p, host, target_port, handshake=handshake)
        if ok:
            result.update({"端口": p, "协议": "http", "通": True, "说明": why})
            return result
        first_http_reason = why

    # 再试 SOCKS5（如果是混合入站，可以当 SOCKS 用）
    for p in listening:
        ok, why = socks5_test(p, host, target_port)
        if ok:
            result.update({"端口": p, "协议": "socks5", "通": True, "说明": why})
            return result

    result["说明"] = ("代理端口在听（%s），但连不通 %s。"
                      "最可能的原因：代理客户端当前的节点已失效 —— 换一个节点再试。"
                      % (listening, result["目标"]))
    return result


def main(argv):
    port = None
    target = TARGET_HOST
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
        if a == "--target" and i + 1 < len(argv):
            target = argv[i + 1]

    r = evaluate([port] if port else None, target)

    print("=" * 62)
    print("网络体检 · Codex 通道")
    print("=" * 62)
    print("检查时间    %s" % r["检查时间"])
    print("目标        %s" % r["目标"])
    print("在听端口    %s" % (r["在听的端口"] or "无"))
    print("可用通道    %s" % ("%s（%s）" % (r["端口"], r["协议"]) if r["通"] else "无"))
    print("结论        %s" % ("✔ 网络正常，Codex 可以开工" if r["通"] else "✘ " + r["说明"]))
    print()
    return 0 if r["通"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
