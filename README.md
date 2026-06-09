# Xueqiu Cube Watcher

监听雪球组合 `ZH2369777` 的调仓记录和详细仓位快照，保存 raw JSON、结构化 JSONL，并在发现新数据时打印播报。

## 已接入接口

调仓历史接口：

```text
GET https://xueqiu.com/cubes/rebalancing/history.json?cube_symbol=ZH2369777&count=20&page=1
```

当前仓位候选接口：

```text
GET https://xueqiu.com/cubes/show.json?cube_symbol=ZH2369777
```

历史仓位候选接口：

```text
GET https://xueqiu.com/cubes/rebalancing/show.json?rb_id=...
GET https://xueqiu.com/cubes/rebalancing/show_origin.json?rb_id=...
GET https://xueqiu.com/cubes/rebalancing/show.json?rebalancing_id=...
GET https://xueqiu.com/cubes/rebalancing/show_origin.json?rebalancing_id=...
```

如果你在浏览器 Network 里确认了新的历史仓位接口，可以用 `XQ_HOLDING_ENDPOINTS` 配置逗号分隔的 URL/path 模板，代码会优先尝试这些模板。可用占位符：`{rb_id}`、`{cube_symbol}`。

## 安装

推荐使用 conda：

```bash
cd /home/horizon/xueqiu
conda activate xueqiu
pip install -r requirements.txt
cp .env.example .env
```

如果还没有创建环境：

```bash
conda create -y -n xueqiu python=3.11 pip
conda activate xueqiu
pip install -r requirements.txt
```

也可以使用 venv：

```bash
cd /home/horizon/xueqiu
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，填入登录雪球后的 Cookie：

```bash
XQ_COOKIE='xq_a_token=xxx; u=xxx; ...'
```

Cookie 获取方式：

1. 浏览器登录 `https://xueqiu.com/p/ZH2369777`
2. 打开 F12 -> Network -> Fetch/XHR
3. 刷新页面或点击“详细仓位”的历史时间点
4. 选择一个 xueqiu.com 请求，复制 Request Headers 里的 `Cookie`
5. 粘贴到 `.env` 的 `XQ_COOKIE`

不要把 `.env` 提交到 git。

## 邮件通知

编辑 `.env`，填入 QQ 邮箱 SMTP 配置。`SMTP_PASSWORD` 是 QQ 邮箱生成的授权码，不是 QQ 登录密码。

```bash
SMTP_HOST='smtp.qq.com'
SMTP_PORT='465'
SMTP_SSL='true'
SMTP_USER='your_email@qq.com'
SMTP_PASSWORD='这里填QQ邮箱授权码'
EMAIL_FROM='your_email@qq.com'
EMAIL_TO='your_email@qq.com'
```

先单独测试邮件：

```bash
cd /home/horizon/xueqiu
conda activate xueqiu
python test_email.py
```

如果显示 `test email sent`，说明 SMTP 可用。

## 历史回补

```bash
cd /home/horizon/xueqiu
conda activate xueqiu
python xq_backfill.py --cube ZH2369777 --max-pages 3
```

常用参数：

```bash
python xq_backfill.py --cube ZH2369777 --max-pages 20 --count 20
python xq_backfill.py --cube ZH2369777 --max-pages 3 --no-reconstruct
```

默认情况下，如果历史仓位接口没有返回完整 holdings，脚本会用调仓记录递推生成 `source = "reconstructed"` 的仓位快照，不会冒充真实 API 快照。

## 实时监听

```bash
cd /home/horizon/xueqiu
conda activate xueqiu
python xq_watch.py --cube ZH2369777 --interval 60
```

每轮请求都打印最新调仓、详细仓位百分比和股票现价：

```bash
python xq_watch.py --cube ZH2369777 --interval 90 --jitter 0.5 --print-latest-each-poll
```

`--interval 90 --jitter 0.5` 表示每次等待在 45 到 135 秒之间随机取值，长期平均约 90 秒。

实时监听默认只在 UTC+8 周一到周五 `09:00-15:30` 请求雪球；其他时间脚本会保持运行并睡到下一个监听窗口，避免无效轮询。

如果临时需要不限制交易时段，可以显式关闭：

```bash
python xq_watch.py --cube ZH2369777 --interval 90 --jitter 0.5 --print-latest-each-poll --no-market-hours-only
```

启动后如果 SMTP 配置完整，会发送一封“监听启动”邮件。发现新的调仓事件时，会发送调仓邮件。首次运行且 `data/state.json` 为空时，脚本默认只记录当前已存在的调仓状态，不会把旧调仓当新信号发邮件。

如果你确实希望首次运行也对当前可见调仓发送邮件：

```bash
python xq_watch.py --cube ZH2369777 --interval 90 --jitter 0.5 --print-latest-each-poll --notify-on-first-run
```

如果想在发现新调仓时也优先尝试当前完整持仓接口，可以打开当前仓位兜底：

```bash
python xq_watch.py --cube ZH2369777 --interval 60 --current-fallback
```

遇到 `401`、`403`、`400016` 时，脚本会提示 Cookie 可能失效。遇到 `429` 会自动暂停 5 到 15 分钟。

## 数据位置

默认写入 `data/`：

```text
data/
  raw/
    rebalancing/
    holdings/
  parsed/
    rebalances.jsonl
    holding_snapshots.jsonl
  state.json
```

`state.json` 保存去重状态。调仓事件使用 `rebalance_id + fingerprint` 去重；仓位快照使用 `snapshot_id + fingerprint` 去重。

## Network 调试

如果详细仓位历史接口没有被内置候选命中：

1. 打开 `https://xueqiu.com/p/ZH2369777`
2. F12 -> Network -> Fetch/XHR
3. 搜索 `history`、`rebalancing`、`cube`、`holding`、`show`
4. 点击右侧“详细仓位”的历史时间点
5. 找到返回完整 holdings 的 JSON 请求，Copy as cURL
6. 把 URL 模板写入 `.env`

示例：

```bash
XQ_HOLDING_ENDPOINTS='/cubes/rebalancing/show.json?rb_id={rb_id}'
```
