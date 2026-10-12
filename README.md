# VehDiag_sef（自用，加密）

制动控制模块 DTC 故障码查询（内部版）。仓库公开，但所有数据都是密文：

| 内容 | 位置 | 用哪个密码解开 |
|---|---|---|
| 网站数据 | `docs/data.enc.json` | 网站访问密码（6 位数字） |
| 原始 Excel / 以后的 DBC 等 | `source/*.enc` | 原始文件密码（长密码） |

均为 AES-256-GCM 加密，密钥由密码经 PBKDF2-SHA256（100 万次）派生。密码不在仓库里。
明文文件已在 `.gitignore` 中排除（`*.xlsx`、`*.dbc` 等），不会被误提交。

## 目录
- `source/` 加密后的原始文件（每个 Sheet = 一个车型）
- `tools/build.py` 加解密与生成网页；`tools/template.html` 页面模板；`tools/zh_map.json` 英文描述的中文对照
- `docs/` 生成的网站（GitHub Pages 发布这个目录）

## 常用操作
```bash
pip install openpyxl cryptography
export VEHDIAG_SRC_KEY='原始文件密码'
python tools/build.py --add 新表格.xlsx                  # 加密新原始文件存入 source/（之后可删掉明文）
python tools/build.py --decrypt plain/                    # 解出原始文件查看/修改
python tools/build.py --pin 6位网站密码                   # 重新生成 docs/（改网站密码也用这个）
python tools/build.py --pin 6位网站密码 --pub ../VehDiag_pub  # 同时更新公开版
```
Excel 里新增的英文描述，在 `tools/zh_map.json` 里补一条“英文: 中文”即可。

## 发布
Settings → Pages → Source 选 “Deploy from a branch”，Branch 选 `main`，目录选 `/docs`。
网址：https://gcy2016.github.io/VehDiag_sef/
