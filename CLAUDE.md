# 给 Claude 的说明

用户会在对话里发来 blf / asc 报文并提问。处理方法：

1. 原始文件密码不在仓库里，向用户要（或用户已在对话中给出），设为环境变量 `VEHDIAG_SRC_KEY`。不要把密码写进任何文件或提交。
2. 安装依赖：`pip install python-can openpyxl cryptography matplotlib`
3. 报文等上传文件先复制到仓库外的独立目录再分析。车型不明时先问用户；也可以不带 `--model`，脚本会按 DBC 覆盖率自动选 DBC（这时不对照问卷）。
4. 运行 `python tools/analyze.py 报文 --model 车型`：看概况、故障码（已对照诊断问卷）、疑似异常信号；
   用 `--find 关键词` 找信号，`--sig 信号... [--t0 --t1] [--plot x.png]` 看具体信号变化。
5. 回答时以问卷中的故障描述 / 可能原因 / 维修建议为准，再结合信号变化和汽车诊断通用知识给出排查思路；
   问卷只收录制动控制模块，其他模块的故障码要说明“问卷未收录、仅按通用知识判断”。

不要把解密后的 DBC、Excel 或报文明文提交到仓库（`.gitignore` 已排除 `*.dbc`、`*.xlsx`、`*.blf`、`*.asc`）。
改了 `tools/template.html` 或 `tools/web/*.js` 后，需要网站密码（6 位）重新生成 `docs/`：`python tools/build.py --pin 网站密码`。
