"""单件台架测试：生成残余总线仿真 CAPL（CANalyzer 10.0 程序节点）。

按车型 DBC 找出被测件（--dut）以外的节点发出的周期报文，初始数据取实车报文某一时刻的快照，
校验和 / 滚动计数器从实车报文反推（在全部报文帧上逐一验证），生成一个 .can 文件：
CANalyzer 发送分支里加 Program 节点挂上它，编译后开始测量，就会按 DBC 周期发出这些报文，
每帧自动加计数器、重算校验，被测件就像装在整车上一样。

依赖：pip install python-can cryptography
用法：
  export VEHDIAG_SRC_KEY='原始文件密码'
  python tools/restbus.py 报文目录或文件... --model C363H --dut IBCU --out IBCU_C363H.can
  可选：--at 5.5            快照时刻（秒，取第一个报文文件中该时刻之前最近一帧；默认文件末尾）
        --set 信号=值 ...   改快照里的初始物理值（报文名.信号名 或 信号名）
        --key 键=信号:值 ...  CANalyzer 里按键改信号：值写数字为设定，写 +5 / -5 为加减
        --nm 400-4FF        这个范围内、DBC 没定义的实车报文按原样周期发送（网络管理，保持常唤醒）；--nm none 不发
        --channel 1         发送通道
生成的 .can 不要提交到仓库（含实车数据）。
"""
import argparse, collections, math, os, re, sys, warnings
from functools import reduce

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze as A, build, dbc  # noqa: E402
from aeb_response import collect  # noqa: E402

warnings.filterwarnings('ignore')
FD_DLC = {8: 8, 12: 9, 16: 10, 20: 11, 24: 12, 32: 13, 48: 14, 64: 15}
RESERVED_KEYS = 'pr'


# ---------- DBC ----------
def load_dbc(a):
    import json
    dbc_map = json.load(open(os.path.join(A.ROOT, 'tools', 'dbc_map.json'), encoding='utf-8'))
    name = a.dbc or dbc_map.get(build.market_name(a.model))
    if not name:
        sys.exit(f'不认识车型 {a.model}，请用 --dbc 指定 DBC 名')
    text = build.open_file(open(os.path.join(A.ROOT, 'source', name + '.dbc.enc'), 'rb').read(), a.src_key)
    text = text.decode('gb18030', errors='replace')
    db = dbc.parse(text, name)
    attr = lambda k: {int(i): int(v) for i, v in re.findall(rf'^BA_\s+"{k}"\s+BO_\s+(\d+)\s+(\d+)\s*;', text, re.M)}
    dflt = lambda k, d: int((re.search(rf'^BA_DEF_DEF_\s+"{k}"\s+"?(\d+)"?\s*;', text, re.M) or [0, d])[1])
    cyc, fmt, brs = attr('GenMsgCycleTime'), attr('VFrameFormat'), attr('CANFD_BRS')
    start = {(int(i), s): int(v) for i, s, v in
             re.findall(r'^BA_\s+"GenSigStartValue"\s+SG_\s+(\d+)\s+(\w+)\s+(-?\d+)\s*;', text, re.M)}
    brs_def = dflt('CANFD_BRS', 1)
    for m in db['msgs']:
        raw = A.msg_key(m)
        m['cyc'] = cyc.get(raw, 0)
        m['fd'] = fmt.get(raw, 0) in (14, 15)
        m['brs'] = brs.get(raw, brs_def)
        m['start'] = {s['n']: start.get((raw, s['n']), 0) for s in m['sigs']}
    return db


def pick(db, spec, msgs=None):
    mn, _, sn = spec.rpartition('.')
    hits = [(m, s) for m in (msgs or db['msgs']) for s in m['sigs'] if s['n'] == sn and (not mn or m['name'] == mn)]
    if not hits:
        sys.exit(f'仿真的报文里没有信号 {spec}（被测件自己发的信号不能改）')
    if len(hits) > 1:
        sys.exit(f'信号 {sn} 在多个报文里：' + '、'.join(m['name'] for m, _ in hits) + '，请写成 报文名.信号名')
    return hits[0]


# ---------- 位操作（与 analyze.raw_value 同一套位序） ----------
def bit_positions(s):
    """信号各位在报文中的位置，按从高位到低位的顺序。"""
    if s['bo'] == 1:
        return [s['sb'] + i for i in range(s['bl'] - 1, -1, -1)]
    out, p = [], s['sb']
    for _ in range(s['bl']):
        out.append(p)
        p = p + 15 if p & 7 == 0 else p - 1
    return out


def put_raw(d, s, v):
    for i, p in enumerate(bit_positions(s)):
        b = (v >> (s['bl'] - 1 - i)) & 1
        d[p >> 3] = (d[p >> 3] | (1 << (p & 7))) if b else (d[p >> 3] & ~(1 << (p & 7)))


def phys_to_raw(s, v):
    r = round((v - s['o']) / s['f'])
    return r & ((1 << s['bl']) - 1)


# ---------- 校验和 / 计数器反推 ----------
def crc8(data, poly, init, xo):
    c = init
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ poly) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c ^ xo


def crc16(data):
    c = 0xFFFF
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


ALGO8 = {
    'crc8J1850': lambda d: crc8(d, 0x1D, 0xFF, 0xFF),
    'crc8H2F': lambda d: crc8(d, 0x2F, 0xFF, 0xFF),
    'chkXor': lambda d: reduce(lambda x, y: x ^ y, d, 0),
    'chkSum': lambda d: sum(d) & 0xFF,
}


def find_checks(seqs, L):
    """seqs：各文件里该报文的数据序列。返回 [(算法, 校验位置, 起始, 长度)]，16 位放在最后（覆盖 8 位校验字节）。"""
    sample = list(dict.fromkeys(d for seq in seqs for d in seq if len(d) == L))[:300]
    if len(sample) < 3:
        return []
    out = []
    for p in range(L):
        if len({d[p] for d in sample}) < 3:
            continue
        b = p - p % 8
        for lo, hi in ((b, p), (p + 1, b + 8), (0, p)):
            if hi - lo < 1 or hi > L:
                continue
            algo = next((k for k, f in ALGO8.items() if all(f(d[lo:hi]) == d[p] for d in sample)), None)
            if algo:
                out.append((algo, p, lo, hi - lo))
                break
    for p in range(2, L - 1):
        if any(c[0] == 'crc16Ccitt' and c[1] == p - 1 for c in out):    # 紧跟 CRC16 的下一字节为 0 时也会“吻合”
            continue
        if len({d[p:p + 2] for d in sample}) >= 3 and all(crc16(d[:p]) == int.from_bytes(d[p:p + 2], 'big') for d in sample):
            out = [c for c in out if c[1] not in (p, p + 1)] + [('crc16Ccitt', p, 0, p)]
    return out


def find_counters(seqs, m, checks):
    """DBC 里名字像计数器的信号 + 每个字节的高低半字节，实车报文中逐帧 +1（≥95%）的算计数器。"""
    cands = [s for s in m['sigs'] if re.search(r'counter|cntr', s['n'], re.I) and s['bl'] <= 8]
    L = m['len']
    for byte in range(L):
        for bl, shs in ((4, (0, 4, 1, 2, 3)), (3, range(6))):
            for sh in shs:
                cands.append({'n': f'byte{byte}_bit{sh}-{sh + bl - 1}', 'sb': byte * 8 + sh, 'bl': bl, 'bo': 1, 's': 0, 'f': 1, 'o': 0})
    taken = {p for c in checks for p in ((c[1],) if c[0] != 'crc16Ccitt' else (c[1], c[1] + 1))}
    out, used = [], set()
    for s in cands:
        bits = set(bit_positions(s))
        if bits & used or {p >> 3 for p in bits} & taken:
            continue
        vals = [[A.raw_value(d, s) for d in seq] for seq in seqs]
        pairs = [(x, y) for v in vals for x, y in zip(v, v[1:])]
        allv = [x for v in vals for x in v]
        if len(pairs) < 10 or len(set(allv)) < 4:
            continue
        mod = max(allv) + 1
        if sum((x + 1) % mod == y for x, y in pairs) >= 0.95 * len(pairs):
            out.append(dict(s, mod=mod))
            used |= bits
    return out


def table_check(seqs, s, counters):
    """算法反推不出、但报文其余内容不变（校验只随计数器变化）时，用实车报文里的“计数器→校验值”对照表。"""
    if len(counters) != 1 or s['bl'] != 8 or len({q >> 3 for q in bit_positions(s)}) != 1:
        return None
    c, p = counters[0], bit_positions(s)[0] >> 3
    skip = {q >> 3 for q in bit_positions(c)} | {p}
    tab, rest = {}, set()
    for seq in seqs:
        for d in seq:
            k = A.raw_value(d, c)
            if tab.setdefault(k, d[p]) != d[p]:
                return None
            rest.add(bytes(b for i, b in enumerate(d) if i not in skip))
    if len(rest) != 1 or len(tab) != c['mod']:
        return None
    return ('table', p, c, [tab[i] for i in range(c['mod'])])


# ---------- CAPL ----------
CAPL_LIB = {
    'crc8J1850': 'byte crc8J1850(byte d[], int ofs, int n)\n{\n  int i, k;\n  byte c;\n  c = 0xFF;\n'
                 '  for (i = ofs; i < ofs + n; i++)\n  {\n    c = c ^ d[i];\n'
                 '    for (k = 0; k < 8; k++) { if (c & 0x80) c = (c << 1) ^ 0x1D; else c = c << 1; }\n  }\n'
                 '  return c ^ 0xFF;\n}\n',
    'crc8H2F': 'byte crc8H2F(byte d[], int ofs, int n)\n{\n  int i, k;\n  byte c;\n  c = 0xFF;\n'
               '  for (i = ofs; i < ofs + n; i++)\n  {\n    c = c ^ d[i];\n'
               '    for (k = 0; k < 8; k++) { if (c & 0x80) c = (c << 1) ^ 0x2F; else c = c << 1; }\n  }\n'
               '  return c ^ 0xFF;\n}\n',
    'chkXor': 'byte chkXor(byte d[], int ofs, int n)\n{\n  int i;\n  byte c;\n  c = 0;\n'
              '  for (i = ofs; i < ofs + n; i++) c = c ^ d[i];\n  return c;\n}\n',
    'chkSum': 'byte chkSum(byte d[], int ofs, int n)\n{\n  int i;\n  byte c;\n  c = 0;\n'
              '  for (i = ofs; i < ofs + n; i++) c = c + d[i];\n  return c;\n}\n',
    'crc16Ccitt': 'word crc16Ccitt(byte d[], int ofs, int n)\n{\n  int i, k;\n  word c;\n  c = 0xFFFF;\n'
                  '  for (i = ofs; i < ofs + n; i++)\n  {\n    c = c ^ (d[i] << 8);\n'
                  '    for (k = 0; k < 8; k++) { if (c & 0x8000) c = (c << 1) ^ 0x1021; else c = c << 1; }\n  }\n'
                  '  return c;\n}\n',
}

CAPL_BITS = '''// 按 DBC 位序读写信号原始值（intel=1：Intel；0：Motorola，sb 为 DBC 起始位）
void putBits(byte d[], int sb, int bl, int intel, dword v)
{
  int i, p;
  p = sb;
  for (i = bl - 1; i >= 0; i--)
  {
    if (intel) p = sb + i;
    if ((v >> i) & 1) d[p >> 3] = d[p >> 3] | (1 << (p & 7));
    else d[p >> 3] = d[p >> 3] & ~(1 << (p & 7));
    if (!intel) { if ((p & 7) == 0) p = p + 15; else p = p - 1; }
  }
}

dword getBits(byte d[], int sb, int bl, int intel)
{
  int i, p;
  dword v;
  v = 0;
  p = sb;
  for (i = bl - 1; i >= 0; i--)
  {
    if (intel) p = sb + i;
    v = (v << 1) | ((d[p >> 3] >> (p & 7)) & 1);
    if (!intel) { if ((p & 7) == 0) p = p + 15; else p = p - 1; }
  }
  return v;
}

void setPhys(byte d[], int sb, int bl, int intel, double f, double o, double v)
{
  double r;
  r = (v - o) / f;
  if (r < 0) r = r - 0.5; else r = r + 0.5;
  if (bl >= 32) putBits(d, sb, bl, intel, (long)r);
  else putBits(d, sb, bl, intel, ((long)r) & ((1 << bl) - 1));
}

double getPhys(byte d[], int sb, int bl, int intel, int sgn, double f, double o)
{
  dword r;
  r = getBits(d, sb, bl, intel);
  if (sgn && bl < 32 && ((r >> (bl - 1)) & 1)) return ((double)r - (double)(1 << bl)) * f + o;
  if (sgn && bl == 32) return (double)((long)r) * f + o;
  return (double)r * f + o;
}
'''


def c_bytes(d):
    return ', '.join(f'0x{b:02X}' for b in d)


def capl(sims, nms, keys, a, info):
    base = reduce(math.gcd, [x['cyc'] for x in sims + nms])
    used_algos = sorted({c[0] for x in sims for c in x['checks']} - {'table'})
    L = ['/*@!Encoding:936*/',
         f"// 残余总线仿真：{info}",
         '// 由 VehDiag_sef tools/restbus.py 生成，请勿手改（要改信号初值 / 按键，重新生成）',
         '// 用法：CANalyzer 测量设置 → 发送分支插入 Program 节点 → 选本文件 → 编译 → 开始测量',
         f"// 按键：p 暂停/恢复全部发送；r 恢复初始值" + ''.join(f"；{k['key']} {k['desc']}" for k in keys),
         '', 'includes\n{\n}', '', 'variables\n{',
         f'  int gChannel = {a.channel};      // 发送通道',
         '  int gRun = 1;',
         '  dword gTick = 0;',
         '  msTimer tTick;']
    for x in sims + nms:
        n = x['var']
        L.append(f"\n  // 0x{x['id']:03X} {x['name']}  {x['len']} 字节  {x['cyc']} ms  {'CAN FD' if x['fd'] else 'CAN'}"
                 + (f"  {x['note']}" if x.get('note') else ''))
        L.append(f"  message 0x{x['id']:X} m{n};")
        L.append(f"  byte d{n}[{x['len']}] = {{{c_bytes(x['data'])}}};")
        L.append(f"  byte b{n}[{x['len']}] = {{{c_bytes(x['data'])}}};")
        for c in x.get('checks', []):
            if c[0] == 'table':
                L.append(f"  byte t{n}[{len(c[3])}] = {{{c_bytes(c[3])}}};   // 计数器 → 字节{c[1]} 校验值（实车对照表）")
    L.append('}\n')
    L += [CAPL_LIB[k] for k in used_algos]
    L.append(CAPL_BITS)

    for x in sims + nms:
        n = x['var']
        L.append(f'void send{n}()\n{{\n  int i;')
        if x.get('counters') or x.get('checks'):
            L.append('  word w;' if any(c[0] == 'crc16Ccitt' for c in x['checks']) else '')
        for c in x.get('counters', []):
            L.append(f"  putBits(d{n}, {c['sb']}, {c['bl']}, {c['bo']}, (getBits(d{n}, {c['sb']}, {c['bl']}, {c['bo']}) + 1) % {c['mod']});"
                     f"  // 计数器 {c['n']}")
        for algo, p, lo, ln in x.get('checks', []):
            if algo == 'table':
                L.append(f"  d{n}[{p}] = t{n}[getBits(d{n}, {lo['sb']}, {lo['bl']}, {lo['bo']})];")
            elif algo == 'crc16Ccitt':
                L.append(f'  w = crc16Ccitt(d{n}, {lo}, {ln});  d{n}[{p}] = w >> 8;  d{n}[{p + 1}] = w & 0xFF;')
            else:
                L.append(f'  d{n}[{p}] = {algo}(d{n}, {lo}, {ln});')
        L.append(f"  for (i = 0; i < {x['len']}; i++) m{n}.byte(i) = d{n}[i];")
        L.append(f'  output(m{n});\n}}\n')
    L = [s for s in L if s != '']

    L.append('on start\n{')
    for x in sims + nms:
        n = x['var']
        L.append(f'  m{n}.CAN = gChannel;')
        if x['fd']:
            L.append(f"  m{n}.FDF = 1;  m{n}.BRS = {x['brs']};  m{n}.DLC = {FD_DLC[x['len']]};")
        else:
            L.append(f"  m{n}.DLC = {x['len']};")
    L.append(f'  setTimerCyclic(tTick, {base});')
    L.append(f'  write("残余总线仿真已启动：{len(sims)} 个报文' + (f'＋{len(nms)} 个网络管理报文' if nms else '') +
             '。按 p 暂停/恢复，r 恢复初始值");')
    L.append('}\n')

    L.append('on timer tTick\n{\n  if (!gRun) return;')
    k = 0
    for x in sims + nms:
        off = (k * base) % x['cyc']
        L.append(f"  if (gTick % {x['cyc']} == {off}) send{x['var']}();")
        k += 1
    L.append(f'  gTick = gTick + {base};\n}}\n')

    L.append('void resetAll()\n{\n  int i;')
    for x in sims + nms:
        L.append(f"  for (i = 0; i < {x['len']}; i++) d{x['var']}[i] = b{x['var']}[i];")
    L.append('}\n')
    L.append("on key 'p'\n{\n  gRun = !gRun;\n  if (gRun) write(\"仿真恢复发送\"); else write(\"仿真已暂停（全部报文停发）\");\n}\n")
    L.append("on key 'r'\n{\n  resetAll();\n  write(\"信号已恢复为初始值\");\n}\n")
    for kd in keys:
        L.append(f"on key '{kd['key']}'\n{{\n{kd['body']}\n}}\n")
    return '\n'.join(L) + '\n'


def key_handler(spec, sims):
    m_ = re.fullmatch(r'(\w)=([\w.]+):([+-]?)([\d.]+)', spec)
    if not m_:
        sys.exit(f'--key 格式：键=信号:值 或 键=信号:+步长，例如 1=ACC_AEBActive:1、u=GW_15D.ESP_LongAccel:+0.5（{spec}）')
    key, sig, sign, val = m_[1], m_[2], m_[3], float(m_[4])
    if key in RESERVED_KEYS:
        sys.exit(f'按键 {key} 已被占用（p 暂停、r 复位）')
    msg, s = pick(None, sig, [x['msg'] for x in sims])
    if s.get('fl'):
        sys.exit(f'{sig} 是浮点信号，暂不支持')
    x = next(x for x in sims if x['msg'] is msg)
    n, args = x['var'], f"{s['sb']}, {s['bl']}, {s['bo']}"
    phys = f"d{n}, {args}, {s['f']}, {s['o']}"
    get = f"getPhys(d{n}, {args}, {s['s']}, {s['f']}, {s['o']})"
    v = f'{get} {sign} {val}' if sign else f'{val}'
    unit = s['u']
    body = f'  setPhys({phys}, {v});\n  write("{s["n"]} = %f {unit}", {get});'
    desc = f"{s['n']} {'加' if sign == '+' else '减' if sign == '-' else '设为'} {val:g}"
    return {'key': key, 'body': body, 'desc': desc}


def main():
    ap = argparse.ArgumentParser(description='生成残余总线仿真 CAPL（单件台架测试）')
    ap.add_argument('logs', nargs='+', help='实车报文（文件或目录），第一个文件用作初始数据快照')
    ap.add_argument('--src-key', default=os.environ.get('VEHDIAG_SRC_KEY'))
    ap.add_argument('--model', help='车型（项目代号或市场名），决定 DBC')
    ap.add_argument('--dbc', help='直接指定 DBC 名')
    ap.add_argument('--dut', required=True, help='被测件节点名（DBC 里的发送节点，如 IBCU），它发的报文不仿真')
    ap.add_argument('--at', type=float, help='快照时刻（秒），默认第一个文件末尾')
    ap.add_argument('--set', nargs='*', default=[], metavar='信号=值')
    ap.add_argument('--key', nargs='*', default=[], metavar='键=信号:值')
    ap.add_argument('--nm', default='400-4FF', help='网络管理报文 ID 范围，none 为不发')
    ap.add_argument('--channel', type=int, default=1)
    ap.add_argument('--out', required=True, help='输出 .can 文件')
    a = ap.parse_args()
    if not a.src_key:
        ap.error('需要原始文件密码 --src-key（或环境变量 VEHDIAG_SRC_KEY）')
    if not a.model and not a.dbc:
        ap.error('需要 --model 或 --dbc')

    db = load_dbc(a)
    files = list(dict.fromkeys(os.path.realpath(f) for f in collect(a.logs)))
    logs = [A.read_log(f) for f in files]
    if not logs or not logs[0]:
        sys.exit('没有读到报文')
    seq = collections.defaultdict(list)          # key → 每个文件一个数据序列
    for fr in logs:
        per = collections.defaultdict(list)
        for t, k, ch, d, fd in fr:
            per[k].append(d)
        for k, v in per.items():
            seq[k].append(v)
    snap = {}
    for t, k, ch, d, fd in logs[0]:
        if a.at is None or t <= a.at:
            snap[k] = d

    dut_tx = [m for m in db['msgs'] if m['tx'] == a.dut]
    if not dut_tx:
        sys.exit(f"DBC 里没有 {a.dut} 发送的报文，节点有：" + '、'.join(sorted({m['tx'] for m in db['msgs']})))
    sims, skipped, warn = [], [], []
    for m in db['msgs']:
        k = A.msg_key(m)
        if m['tx'] == a.dut:
            continue
        if 0x700 <= m['id'] <= 0x7FF:
            skipped.append(f"0x{m['id']:03X} {m['name']}（诊断报文）")
            continue
        if not m['cyc']:
            skipped.append(f"0x{m['id']:03X} {m['name']}（事件型，不周期发送" + ('；实车报文中出现过' if k in seq else '') + '）')
            continue
        if k in snap and len(snap[k]) == m['len']:
            data, note = bytearray(snap[k]), ''
        else:
            data, note = bytearray(m['len']), '实车报文中没有，初值取 DBC 起始值'
            for s in m['sigs']:
                put_raw(data, s, m['start'][s['n']] & ((1 << s['bl']) - 1))
            warn.append(f"0x{m['id']:03X} {m['name']}：{note}")
        x = {'id': m['id'], 'name': m['name'], 'len': m['len'], 'cyc': m['cyc'], 'fd': m['fd'] or m['len'] > 8,
             'brs': m['brs'], 'data': data, 'msg': m, 'var': f"_{m['id']:03X}", 'note': note}
        s_ = seq.get(k, [])
        x['checks'] = find_checks(s_, m['len'])
        x['counters'] = find_counters(s_, m, x['checks'])
        for s in m['sigs']:
            if re.search(r'crc|chks|checksum', s['n'], re.I):
                bytes_ = {p >> 3 for p in bit_positions(s)}
                if any(c[1] in bytes_ for c in x['checks']):
                    continue
                tab = table_check(s_, s, x['counters'])
                if tab:
                    x['checks'].append(tab)
                    warn.append(f"0x{m['id']:03X} {m['name']}：校验信号 {s['n']} 不是常见算法，按实车报文的“计数器→校验值”对照表发送")
                else:
                    warn.append(f"0x{m['id']:03X} {m['name']}：DBC 有校验信号 {s['n']}，但没能从实车报文反推出算法")
        sims.append(x)

    nms = []
    if a.nm != 'none':
        lo, hi = (int(v, 16) for v in a.nm.split('-'))
        known = {A.msg_key(m) for m in db['msgs']}
        for k in sorted(seq):
            if lo <= k <= hi and k not in known and k in snap:
                ts = [t for fr in logs[:1] for t, kk, *_ in fr if kk == k]
                gaps = sorted(y - x for x, y in zip(ts, ts[1:]))
                ms = gaps[len(gaps) // 2] * 1000 if gaps else 1000
                cyc = min((10, 20, 40, 50, 100, 200, 500, 1000, 2000), key=lambda c: abs(c - ms))
                fd = any(f[4] for f in logs[0] if f[1] == k)
                nms.append({'id': k, 'name': f'NM_{k:03X}', 'len': len(snap[k]), 'cyc': cyc, 'fd': fd, 'brs': 1,
                            'data': bytearray(snap[k]), 'var': f'_{k:03X}', 'checks': [], 'counters': [],
                            'note': '网络管理（DBC 未定义，按实车原样发送）'})

    for spec in a.set:
        name, _, v = spec.partition('=')
        msg, s = pick(None, name, [x['msg'] for x in sims])
        x = next(x for x in sims if x['msg'] is msg)
        put_raw(x['data'], s, phys_to_raw(s, float(v)))
        for algo, p, lo, ln in x['checks']:             # 改了初值后校验要重算
            if algo == 'table':
                continue
            if algo == 'crc16Ccitt':
                x['data'][p:p + 2] = crc16(x['data'][:p]).to_bytes(2, 'big')
            else:
                x['data'][p] = ALGO8[algo](x['data'][lo:lo + ln])
    keys = [key_handler(k, sims) for k in a.key]

    info = (f"被测件 {a.dut}，车型 {a.model or '-'}，DBC {db['name']}，快照 {os.path.basename(files[0])} "
            f"{'末尾' if a.at is None else f'{a.at}s'}")
    with open(a.out, 'w', encoding='gbk', errors='replace', newline='\r\n') as f:
        f.write(capl(sims, nms, keys, a, info))

    print(f'# 生成 {a.out}\n{info}')
    print(f'被测件 {a.dut} 自己发的报文（不仿真）：' + '、'.join(f"0x{m['id']:03X}" for m in dut_tx))
    print(f'\n# 仿真报文 {len(sims)} 个')
    for x in sims:
        prot = '；'.join([f"{c[0]}@字节{c[1]}" + ('(实车对照表)' if c[0] == 'table' else f"(对字节{c[2]}-{c[2] + c[3] - 1})")
                         for c in x['checks']] +
                        [f"计数器 {c['n']}(0-{c['mod'] - 1})" for c in x['counters']])
        print(f"- 0x{x['id']:03X} {x['name']:<10} {x['len']:2d}B {x['cyc']:4d}ms {'FD ' if x['fd'] else 'CAN'} {prot or '无校验/计数器'}")
    for x in nms:
        print(f"- 0x{x['id']:03X} {x['name']:<10} {x['len']:2d}B {x['cyc']:4d}ms {'FD ' if x['fd'] else 'CAN'} {x['note']}")
    if skipped:
        print('\n# 未仿真\n' + '\n'.join('- ' + s for s in skipped))
    if warn:
        print('\n# 注意\n' + '\n'.join('- ' + s for s in warn))
    if keys:
        print('\n# 按键\n' + '\n'.join(f"- {k['key']}：{k['desc']}" for k in keys))


if __name__ == '__main__':
    main()
