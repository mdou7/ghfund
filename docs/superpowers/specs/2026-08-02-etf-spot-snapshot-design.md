# ETF 实时快照定时抓取与回写方案

- 日期:2026-08-02
- 状态:已批准,待实施

## 背景与目标

`ghfund` 仓库的目标:在 GitHub Actions 上跑定时任务,通过 akshare 调用东方财富接口
`ak.fund_etf_spot_em()` 抓取全市场 ETF 实时行情快照,并把结果以 CSV 文件形式提交回本仓库,
让仓库本身成为一份可追溯的历史快照数据集。不依赖任何外部数据库或服务器。

## 架构

一个 GitHub Actions workflow,在 A 股交易时段内每 5 分钟触发一次:

1. checkout 仓库最新 default 分支
2. 安装 Python 依赖(akshare、pandas)
3. 运行抓取脚本,调用 `ak.fund_etf_spot_em()` 拿到当前快照并写成 CSV
4. 用默认 `GITHUB_TOKEN` 执行 `git add / commit / push`,把新文件提交回仓库

全程单一 workflow + 单一脚本,不引入数据库、不引入第三方 commit action、不走 REST API 提交
(评估过 `git-auto-commit-action` 和 PyGithub REST API 两种替代方案,均无必要收益,详见下方
"已考虑但未采用的方案")。

## 目录结构

```
.github/workflows/fetch_etf_spot.yml   # 定时工作流
scripts/fetch_etf_spot.py              # 抓取 + 落盘脚本
requirements.txt                       # akshare、pandas,锁定具体版本号
data/2026-08-02/153000.csv             # 抓取产物:按天分目录,文件名为北京时间 HHMMSS
```

- `data/<YYYY-MM-DD>/<HHMMSS>.csv`:日期与时间均以北京时间(`Asia/Shanghai`)为准,与
  runner 自身系统时区无关。
- 每次运行只产出一个新文件,不修改历史文件,天然可追加、可追溯。

## 调度策略

北京时间交易时段 09:30–11:30、13:00–15:00,换算为 UTC(GitHub Actions cron 用 UTC)为
01:30–03:30、05:00–07:00。因为这两个窗口内本地时间均 ≥ 08:00,减去 8 小时偏移不会跨到
前一个 UTC 日期,所以 UTC 的星期几与北京时间一致,`weekday` 字段可以直接按周一到周五配置,
无需额外换算。

用 6 条 cron 精确覆盖这两个窗口、每 5 分钟一次、仅周一到周五,一天正好 50 次:

```yaml
on:
  schedule:
    - cron: '30-59/5 1 * * 1-5'   # 09:30-09:55
    - cron: '*/5 2 * * 1-5'       # 10:00-10:55
    - cron: '0-30/5 3 * * 1-5'    # 11:00-11:30
    - cron: '0-59/5 5 * * 1-5'    # 13:00-13:55
    - cron: '*/5 6 * * 1-5'       # 14:00-14:55
    - cron: '0 7 * * 1-5'         # 15:00(收盘快照)
  workflow_dispatch: {}
```

- `workflow_dispatch` 用于手动触发调试,不占用交易时段之外的额外计划任务。
- 法定节假日不做识别,照常触发,拿到的是收盘静止数据,属于已知的可接受噪音——本次不引入
  交易日历依赖(见下方"已考虑但未采用的方案")。
- GitHub Actions 定时任务本身存在数分钟级触发延迟,属于已知限制,不做补偿。

## 数据流

1. Cron(或手动)触发 → `actions/checkout`(拉取最新 default 分支)→
   `actions/setup-python`(固定版本 + pip 缓存)→ `pip install -r requirements.txt`。
2. 脚本调用 `ak.fund_etf_spot_em()`;失败自动重试,共 3 次,间隔 5s/15s 递增退避;
   全部失败则 `sys.exit(1)`,不落盘、不提交。
3. 成功后用 `zoneinfo("Asia/Shanghai")` 取北京时间,构造路径
   `data/<日期>/<HHMMSS>.csv`,写文件(`utf-8-sig` 编码,兼容 Excel 打开中文列名)。
4. Workflow 用默认 `GITHUB_TOKEN`(`permissions: contents: write`)执行
   `git add / commit / push`;若暂存区为空(防御性判断,理论上不会出现,因为每次文件名唯一)
   则跳过 commit。

## 并发与错误处理

- workflow 设置 `concurrency: { group: etf-spot-snapshot, cancel-in-progress: false }`,
  防止触发延迟导致相邻两次运行重叠推送产生冲突——让它们排队而不是抢跑。
- 抓取重试耗尽 → job 失败(Actions 页面红叉可见),不静默吞错,不产出半成品文件。
- push 极少数情况下因竞态失败,做一次 `git pull --rebase` 后重试;仍失败则 job 失败。

## 数据留存

暂不引入归档、压缩或清理机制。先让数据按当前方式自然增长,仓库体积问题留待以后单独评估
和设计(可能的方向:按月归档为 parquet、设置保留期等),不在本次范围内。

## 依赖版本管理

`requirements.txt` 中 `akshare`、`pandas` 均锁定具体版本号,保证构建可复现。东财接口格式
变化导致 akshare 抓取失败时,需要人工升级 `akshare` 版本号——这是已知的、可接受的日常维护
成本,本次不引入自动升级依赖的机制。

## 测试计划

- 本地先手动跑一次 `scripts/fetch_etf_spot.py`,确认能生成合理行列数的 CSV。
- workflow 合并后用 `workflow_dispatch` 手动触发一次,验证
  checkout → 安装依赖 → 抓取 → commit → push 全链路打通,不用等到真实交易时段。
- 之后靠 cron 自然触发验证时段覆盖是否准确。

## 已考虑但未采用的方案

**回写仓库机制**,评估过三种做法:

- **A(采用)**:workflow 内直接用 git 命令 + 默认 `GITHUB_TOKEN`。零额外依赖,失败排查
  最直接。
- B:第三方 Action `git-auto-commit-action` 代劳 commit/push。能省几行 git 命令,但引入
  第三方 Action 的供应链信任面,本质和 A 一样,未采用。
- C:Python 内用 PyGithub 走 REST API 提交(不走 shell git 命令)。比 A 复杂不少,没有必要,
  未采用。

**非交易日判断**,评估过两种做法:

- **仅按周一至周五过滤(采用)**:cron 表达式直接限定,不识别法定节假日,实现简单,代价是
  节假日会产生一些重复的收盘静止数据快照。
- 调用交易日历接口(如 `ak.tool_trade_date_hist_sina`)判断并跳过节假日:逻辑更严谨,但
  多一次网络请求依赖,本次不采用。

**数据存储粒度**,评估过三种做法:

- **每次运行单独一个快照文件(采用)**:`data/<日期>/<HHMMSS>.csv`,文件独立完整,便于
  定位某一时刻的快照。
- 按天单文件追加快照行:CSV 体积更集中,但需要处理并发追加和文件锁问题,未采用。
- Parquet 按天存储:体积更小、读写更快,但不是纯文本、`git diff` 不可读,需要 pandas/pyarrow
  才能查看,未采用。

## 范围之外

- 不做基金数据(`fund_*` 系列)或指数数据的抓取,仅限 `ak.fund_etf_spot_em()` 这一个接口。
- 不做数据归档/清理机制。
- 不做交易日历识别。
- 不做通知(邮件/IM)机制。
