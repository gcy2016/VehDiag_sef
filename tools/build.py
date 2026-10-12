"""从 source/*.enc（加密的原始 Excel）生成 DTC 查询网页。

仓库是公开的，原始文件只以密文存放（source/*.enc），明文不进仓库。
两个密码：
  原始文件密码 --src-key（或环境变量 VEHDIAG_SRC_KEY）：加解密 source/ 里的原始文件
  网站访问密码 --pin（或环境变量 VEHDIAG_PIN）：6 位数字，加密网站数据 docs/data.enc.json

用法：
  python tools/build.py --src-key KEY --add 新表格.xlsx            # 加密一个原始文件存入 source/
  python tools/build.py --src-key KEY --pin 123456                 # 生成加密版 docs/
  python tools/build.py --src-key KEY --pin 123456 --pub ../VehDiag_pub   # 同时生成公开版
  python tools/build.py --src-key KEY --decrypt 输出目录          # 解出原始文件（查看/修改用）

每个 Sheet 名即车型；读取 显示码 / 高中字节 / 低字节 / 故障描述 / 可能原因 / 维修建议。
“描述”栏若为英文，按 tools/zh_map.json 译成中文，原文保留为 *_en。
"""
import argparse, base64, glob, io, json, os, re, secrets
import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CJK = re.compile('[一-鿿]')
PBKDF2_ITER = 1_000_000


def cell(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def hexbyte(v, width):
    v = re.sub(r'^0X', '', v.upper().replace(' ', ''))
    return v.zfill(width) if re.fullmatch(r'[0-9A-F]+', v) else v


def zh(cn, en, zh_map):
    """返回 (中文, 英文原文)。"""
    if CJK.search(cn):
        return cn, ''
    if CJK.search(en):          # 中英文栏填反
        return en, cn
    if cn in zh_map:
        return zh_map[cn], cn
    return cn, ''


def load(xlsx, zh_map):
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    models = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        head = rows[2]

        def col(key):
            return next(i for i, c in enumerate(head) if c and key in str(c))
        cd, cf, cp, cr = col('DTC Number'), col('Faults Types'), col('Possible Fault'), col('Corrective')
        items = []
        for r in rows[4:]:
            disp = cell(r[cd]).upper()
            if not re.fullmatch(r'[PCBU][0-9A-F]{6}', disp):
                continue
            it = {'d': disp, 'hm': hexbyte(cell(r[cd + 1]), 4), 'l': hexbyte(cell(r[cd + 2]), 2)}
            for k, c in (('desc', cf), ('cause', cp), ('fix', cr)):
                it[k], en = zh(cell(r[c]), cell(r[c + 1]), zh_map)
                if en:
                    it[k + '_en'] = en
            items.append(it)
        models.append({'model': ws.title, 'items': items})
    return models


def derive(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    return PBKDF2HMAC(hashes.SHA256(), 32, salt, PBKDF2_ITER).derive(password.encode())


def encrypt(data: bytes, pin: str):
    """网站数据：JSON 格式，浏览器端 WebCrypto 解密。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = secrets.token_bytes(16), secrets.token_bytes(12)
    b64 = lambda b: base64.b64encode(b).decode()
    return {'v': 1, 'iter': PBKDF2_ITER, 'salt': b64(salt), 'iv': b64(iv),
            'ct': b64(AESGCM(derive(pin, salt)).encrypt(iv, data, None))}


SRC_MAGIC = b'VDENC1'


def seal_file(data: bytes, key: str) -> bytes:
    """原始文件：MAGIC + salt(16) + iv(12) + AES-256-GCM 密文。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = secrets.token_bytes(16), secrets.token_bytes(12)
    return SRC_MAGIC + salt + iv + AESGCM(derive(key, salt)).encrypt(iv, data, None)


def open_file(blob: bytes, key: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    assert blob[:6] == SRC_MAGIC, '不是加密的原始文件'
    salt, iv = blob[6:22], blob[22:34]
    try:
        return AESGCM(derive(key, salt)).decrypt(iv, blob[34:], None)
    except InvalidTag:
        raise SystemExit('原始文件密码错误')


def page(mode):
    html = open(os.path.join(ROOT, 'tools', 'template.html'), encoding='utf-8').read()
    return html.replace('__MODE__', mode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src-key', default=os.environ.get('VEHDIAG_SRC_KEY'), help='原始文件密码')
    ap.add_argument('--pin', default=os.environ.get('VEHDIAG_PIN'), help='6 位数字网站密码，生成加密版到 docs/')
    ap.add_argument('--pub', help='公开版输出目录（如 ../VehDiag_pub）')
    ap.add_argument('--add', nargs='+', metavar='FILE', help='加密原始文件存入 source/')
    ap.add_argument('--decrypt', metavar='DIR', help='把 source/ 里的原始文件解密到 DIR')
    a = ap.parse_args()
    if not a.src_key:
        ap.error('需要原始文件密码 --src-key（或环境变量 VEHDIAG_SRC_KEY）')
    src = os.path.join(ROOT, 'source')

    if a.add:
        os.makedirs(src, exist_ok=True)
        for f in a.add:
            out = os.path.join(src, os.path.basename(f) + '.enc')
            open(out, 'wb').write(seal_file(open(f, 'rb').read(), a.src_key))
            print('已加密', f, '->', out)
        return
    if a.decrypt:
        os.makedirs(a.decrypt, exist_ok=True)
        for f in sorted(glob.glob(os.path.join(src, '*.enc'))):
            out = os.path.join(a.decrypt, os.path.basename(f)[:-4])
            open(out, 'wb').write(open_file(open(f, 'rb').read(), a.src_key))
            print('已解密', out)
        return

    zh_map = json.load(open(os.path.join(ROOT, 'tools', 'zh_map.json'), encoding='utf-8'))
    models = []
    for f in sorted(glob.glob(os.path.join(src, '*.xlsx.enc'))):
        models += load(io.BytesIO(open_file(open(f, 'rb').read(), a.src_key)), zh_map)
    payload = json.dumps({'models': models}, ensure_ascii=False, separators=(',', ':'))
    print('车型：', ', '.join(f"{m['model']}({len(m['items'])})" for m in models))

    if a.pin:
        assert re.fullmatch(r'\d{6}', a.pin), '密码必须是 6 位数字'
        out = os.path.join(ROOT, 'docs')
        os.makedirs(out, exist_ok=True)
        json.dump(encrypt(payload.encode(), a.pin), open(os.path.join(out, 'data.enc.json'), 'w'))
        open(os.path.join(out, 'index.html'), 'w', encoding='utf-8').write(page('sef'))
        open(os.path.join(out, '.nojekyll'), 'w').close()
        print('已生成加密版', out)
    if a.pub:
        os.makedirs(a.pub, exist_ok=True)
        open(os.path.join(a.pub, 'data.json'), 'w', encoding='utf-8').write(payload)
        open(os.path.join(a.pub, 'index.html'), 'w', encoding='utf-8').write(page('pub'))
        open(os.path.join(a.pub, '.nojekyll'), 'w').close()
        print('已生成公开版', a.pub)


if __name__ == '__main__':
    main()
