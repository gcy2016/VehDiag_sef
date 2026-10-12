"""极简 DBC 解析：只取画曲线和识别故障码要用的部分（报文、信号、值描述、注释）。

输出结构（网页和 analyze.py 共用）：
  {'name': 'CIIA1.5_ICE', 'msgs': [{'id', 'ext', 'name', 'len', 'tx', 'sigs': [...]}, ...]}
  信号：{'n', 'sb' 起始位, 'bl' 位长, 'bo' 1=Intel/0=Motorola, 's' 有符号, 'f' 系数, 'o' 偏移,
         'mn', 'mx', 'u' 单位, 'vt' 值描述 {原始值: 文字}, 'c' 注释, 'mux' 'M' 或复用值, 'fl' 1=float 2=double}
"""
import re

RE_BO = re.compile(r'^BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+(\w+)')
RE_SG = re.compile(r'^SG_\s+(\w+)\s*(M|m\d+)?\s*:\s*(\d+)\|(\d+)@([01])([+-])\s*'
                   r'\(([^,]+),([^)]+)\)\s*\[([^|]*)\|([^\]]*)\]\s*"([^"]*)"')
RE_CM = re.compile(r'CM_\s+(SG_|BO_)\s+(\d+)\s+(\w+)?\s*"((?:[^"\\]|\\.)*)"\s*;', re.S)
RE_VAL = re.compile(r'^VAL_\s+(\d+)\s+(\w+)\s+((?:-?\d+\s+"(?:[^"\\]|\\.)*"\s*)*);', re.M)
RE_VALPAIR = re.compile(r'(-?\d+)\s+"((?:[^"\\]|\\.)*)"')
RE_LONG = re.compile(r'^BA_\s+"SystemSignalLongSymbol"\s+SG_\s+(\d+)\s+(\w+)\s+"([^"]+)"', re.M)
RE_VT = re.compile(r'^SIG_VALTYPE_\s+(\d+)\s+(\w+)\s*:\s*([12])\s*;', re.M)


def num(s):
    v = float(s)
    return int(v) if v.is_integer() and abs(v) < 2 ** 53 else v


def parse(text, name):
    msgs, by_id, cur = [], {}, None
    for line in text.splitlines():
        line = line.strip()
        m = RE_BO.match(line)
        if m:
            raw = int(m[1])
            if raw == 0xC0000000:            # VECTOR__INDEPENDENT_SIG_MSG：未分配信号的占位报文
                cur = None
                continue
            cur = {'id': raw & 0x1FFFFFFF, 'ext': int(bool(raw & 0x80000000)), 'name': m[2],
                   'len': int(m[3]), 'tx': m[4], 'sigs': []}
            msgs.append(cur)
            by_id[raw] = cur
            continue
        m = RE_SG.match(line)
        if m and cur is not None:
            s = {'n': m[1], 'sb': int(m[3]), 'bl': int(m[4]), 'bo': int(m[5]), 's': int(m[6] == '-'),
                 'f': num(m[7]), 'o': num(m[8]), 'mn': num(m[9] or 0), 'mx': num(m[10] or 0), 'u': m[11]}
            if m[2]:
                s['mux'] = 'M' if m[2] == 'M' else int(m[2][1:])
            k = 2
            while any(x['n'] == s['n'] for x in cur['sigs']):    # 个别 DBC 同一报文里信号重名，后者加 #2
                s['n'] = f"{m[1]}#{k}"
                k += 1
            cur['sigs'].append(s)
        elif not line:
            cur = None

    def sig(mid, sname):
        msg = by_id.get(int(mid))
        return msg and next((s for s in msg['sigs'] if s['n'] == sname), None)

    for m in RE_CM.finditer(text):
        txt = m[4].replace('\\"', '"').replace('\r', '').strip()
        if not txt:
            continue
        if m[1] == 'SG_':
            s = sig(m[2], m[3])
            if s:
                s['c'] = txt
        elif int(m[2]) in by_id:
            by_id[int(m[2])]['c'] = txt
    for m in RE_VAL.finditer(text):
        s = sig(m[1], m[2])
        if s:
            s['vt'] = {int(v): ' '.join(t.split()) for v, t in RE_VALPAIR.findall(m[3])}
    for m in RE_VT.finditer(text):
        s = sig(m[1], m[2])
        if s:
            s['fl'] = int(m[3])
    for m in RE_LONG.finditer(text):           # 超过 32 字符的信号名，DBC 里存的是截断名
        s = sig(m[1], m[2])
        if s:
            s['n'] = m[3]
    msgs.sort(key=lambda x: x['id'])
    return {'name': name, 'msgs': msgs}


def load_bytes(data, name):
    # 中文注释是 GBK；个别字节不合法时用替换字符，不影响信号定义
    return parse(data.decode('gb18030', errors='replace'), name)
