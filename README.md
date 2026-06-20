# AI 产业链轮动扫描

基于 Tushare Pro 的 A 股 AI 产业链轮动监控工具。复刻研报 PDF 中的跟踪逻辑：**先判断板块量能与拥挤度，再在板块框架下对个股给出上车/观望建议**，并生成可视化 HTML 面板。

> 在线面板：若已开启 GitHub Pages，访问  
> `https://hyan1985.github.io/ai-stock-scanner/`  
> 或直接打开仓库中的 [`dashboard.html`](dashboard.html)。

---

## 功能概览

| 能力 | 说明 |
|------|------|
| 板块量能扫描 | 7 大优先级板块：量比趋势、主力净流入、涨跌分布、拥挤度 |
| 个股诊断 | 综合趋势、量能、资金、板块状态，输出 **可上车 / 可关注 / 观望 / 回避** |
| 评分体系 | 基础分 40，「可上车」阈值 75，含高位追涨、涨幅过大等风险标记 |
| HTML 面板 | 扫描结果写入 `data.json`，并嵌入 `dashboard.html` 自包含展示 |
| 自动更新 | GitHub Actions 每个交易日 15:30（北京时间）自动扫描并提交数据 |

---

## 跟踪板块（优先级由高到低）

| 优先级 | 板块 | 核心逻辑 |
|--------|------|----------|
| 1 | 液冷 / IDC 电力 | 全球电力约束最硬，800V DC 与高功率机柜强化需求 |
| 2 | 先进封装设备材料 | HBM / CoWoS / 3D IC 推动封测与材料需求 |
| 3 | CPO / 光互连 / 高速铜缆 | Rubin、CPO、1.6T/3.2T 等拉动 |
| 4 | PCB / CCL | AI 服务器与高速交换机提升层数与材料等级（拥挤预警） |
| 5 | AI 服务器 / 整机 | 受益数据中心建设，偏规模制造 |
| 6 | 国产算力 / AI 应用 | 国产化与 Agent 中线价值 |
| 7 | 超级电容 | AI 服务器电源缓冲 / 滤波 |

股票池与阈值均在 [`config.py`](config.py) 中配置，可按研报更新自行调整。

---

## 快速开始

### 1. 克隆与安装

```bash
git clone https://github.com/hyan1985/ai-stock-scanner.git
cd ai-stock-scanner

chmod +x install.sh
./install.sh
```

或手动安装：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置 Tushare Token

在 [Tushare Pro](https://tushare.pro) 注册并获取 Token（建议 5000 积分以上，以使用行情、资金流等接口）：

```bash
export TUSHARE_TOKEN="your_token_here"
```

可将上述命令写入 `~/.zshrc` 持久化。**请勿将 Token 提交到仓库。**

### 3. 运行扫描

```bash
source .venv/bin/activate
python main.py --save-json data.json
```

执行后会更新 `data.json` 与 `dashboard.html`，用浏览器打开 `dashboard.html` 即可查看。

---

## 命令行用法

```bash
# 全量扫描：板块量能 + 个股诊断，并更新面板
python main.py --save-json data.json

# 指定交易日
python main.py --date 20260618

# 只看「可上车 + 可关注」
python main.py --board

# 只看某一板块（名称模糊匹配）
python main.py --sector 液冷

# 终端 JSON 输出
python main.py --json
```

---

## GitHub Actions 自动扫描

仓库已配置 [`.github/workflows/daily-scan.yml`](.github/workflows/daily-scan.yml)：

- **触发时间**：周一至周五 15:30（北京时间）
- **手动触发**：GitHub → Actions →「AI产业链轮动扫描」→ Run workflow
- **产出**：自动 commit 并 push `dashboard.html`、`data.json`

### 配置 Secret

在仓库 **Settings → Secrets and variables → Actions** 中添加：

| Name | Value |
|------|-------|
| `TUSHARE_TOKEN` | 你的 Tushare Pro Token |

---

## 本地定时任务（macOS）

每个交易日收盘后自动扫描：

```bash
chmod +x setup_daily_run.sh
./setup_daily_run.sh
```

会在 `~/Library/LaunchAgents/` 注册 launchd 任务（周一至周五 15:30）。日志目录：`logs/`。

---

## 项目结构

```
├── main.py              # 入口：扫描 + JSON/HTML 输出
├── scanner.py           # 扫描引擎（板块 → 个股）
├── tushare_client.py    # Tushare 数据封装
├── config.py            # 股票池、板块映射、阈值
├── dashboard.html       # 可视化面板（内嵌 JSON）
├── index.html           # GitHub Pages 跳转页
├── data.json            # 最新扫描数据
├── install.sh           # 一键安装
├── setup_daily_run.sh   # macOS 定时任务
└── .github/workflows/   # 每日自动扫描
```

---

## 信号说明

| 结论 | 含义 |
|------|------|
| **可上车** | 趋势、量能、资金与板块状态综合评分 ≥ 75，且无重大风险标记 |
| **可关注** | 逻辑成立但信号未完全确认，适合列入观察池 |
| **观望** | 信号中性或板块退潮，暂不建议行动 |
| **回避** | 过热、破位或出现降级信号 |

具体打分规则见 [`scanner.py`](scanner.py) 中的 `_score_stock` 逻辑。

---

## 免责声明

本工具仅供学习与研究，**不构成任何投资建议**。股市有风险，决策请独立判断。

---

## License

MIT
