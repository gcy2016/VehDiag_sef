# VehDiag_sef（自用，加密）

售后诊断助手（内部版）：制动控制模块 DTC 查询 + 报文数据分析。仓库公开，但所有数据都是密文：

| 内容 | 位置 | 用哪个密码解开 |
|---|---|---|
| 网站数据 | `docs/data.enc.json` | 网站访问密码（6 位数字） |
| 原始 Excel、DBC | `source/*.enc` | 原始文件密码（长密码） |

均为 AES-256-GCM 加密，密钥由密码经 PBKDF2-SHA256（100 万次）派生。密码不在仓库里。
明文文件已在 `.gitignore` 中排除（`*.xlsx`、`*.dbc` 等），不会被误提交。

## 网页功能
首页选车型 → 再选功能：
- **故障码查询**：输入 DTC，查故障描述、可能原因、维修建议。
- **数据分析**：拖入 `.blf` / `.asc` 报文（只在本机浏览器解析，不上传）
  - 自动识别故障码：诊断仪读码应答（UDS 19 服务，含多帧）和 DBC 里广播的 DTC 信号，并按该车型问卷给出含义、原因、维修建议；同时列出清码等诊断过程
  - 列出“故障 / 报警 / 无效”取值的疑似异常信号
  - 按 DBC 画信号曲线：每个信号一条泳道、各自 Y 轴，上下均分；滚轮/双指缩放、拖动平移、双击复位，悬停看数值
  - 报文和别的 DBC 更匹配时会提示车型可能选错，也可以手动换 DBC
  - 报文提取（类似 CANalyzer Trace）：按 ID / 范围 / 报文名筛选；点选一行，DBC 报文显示各信号值，诊断报文说明 ISO-TP 帧类型并按通用诊断知识解释完整请求 / 应答（如 7F 负响应的否定码含义），知识表在 `tools/uds.json`

车型与 DBC 的对应见 `tools/dbc_map.json`：

| DBC | 车型 |
|---|---|
| CIIA1.0_ICE | 逸达、三代逸动 |
| CIIA1.5_ICE | 四代逸动大陆、四代逸动拿森 |
| CIIA1.5_HEV_PHEV | 四代逸动HEV伯特利、四代逸动HEV拿森、A05基础款、A05怀挡款、A05大陆 |
| CIIA1.5_EV | 欧尚520DY41、欧尚520DY51 |

网页要在浏览器里解析报文，所以 DBC 的信号定义和问卷一起放在 `docs/data.enc.json`（网站密码加密）；原始 DBC 文件只在 `source/` 里，用长密码加密。

## 目录
- `source/` 加密后的原始文件（Excel 每个 Sheet = 一个车型；`*.dbc.enc` 为 DBC）
- `tools/build.py` 加解密与生成网页；`tools/template.html` 加密版页面；`tools/template_pub.html` 公开版页面（不含报文分析）
- `tools/web/canlog.js` 报文读取、DBC 解码、故障码识别；`tools/web/plot.js` 曲线；生成时内联进页面
- `tools/dbc.py` DBC 解析；`tools/dbc_map.json` 车型 → DBC；`tools/zh_map.json` 英文描述的中文对照
- `tools/analyze.py` 命令行报文分析（规则与网页相同，在对话里分析报文用）
- `docs/` 生成的网站（GitHub Pages 发布这个目录）

## 常用操作
```bash
pip install openpyxl cryptography
export VEHDIAG_SRC_KEY='原始文件密码'
python tools/build.py --add 新表格.xlsx                  # 加密新原始文件存入 source/（之后可删掉明文）
python tools/build.py --add CIIA2.0_EV.dbc               # 新 DBC 同样加密存入，再在 tools/dbc_map.json 里挂到车型
python tools/build.py --decrypt plain/                    # 解出原始文件查看/修改
python tools/build.py --pin 6位网站密码                   # 重新生成 docs/（改网站密码也用这个）
python tools/build.py --pin 6位网站密码 --pub ../VehDiag_pub  # 同时更新公开版
```
Excel 里新增的英文描述，在 `tools/zh_map.json` 里补一条“英文: 中文”即可。

命令行分析报文（另需 `pip install python-can`，画图需 `matplotlib`）：
```bash
python tools/analyze.py 报文.blf --model 四代逸动HEV伯特利                    # 概况、故障码（对照问卷）、疑似异常信号
python tools/analyze.py 报文.blf --model 四代逸动HEV伯特利 --find 轮速        # 搜索信号
python tools/analyze.py 报文.blf --model 四代逸动HEV伯特利 --sig ESP_VehicleSpeed --plot 曲线.png --csv 数据.csv
python tools/analyze.py 报文.blf --model 四代逸动HEV伯特利 --trace 700-7FF          # 报文列表，诊断报文附解释
```

## 发布
Settings → Pages → Source 选 “Deploy from a branch”，Branch 选 `main`，目录选 `/docs`。
网址：https://gcy2016.github.io/VehDiag_sef/
