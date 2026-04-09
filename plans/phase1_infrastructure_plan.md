# Project Claw — Phase 1 基础设施详细规划
## 3,000-6,000 商户 · 香港部署 · AI 推理混合架构

> 基于 TradeStealth_Core 现有技术栈的完整部署方案
> 116/116 测试通过 · LangGraph 15+ 节点状态机 · A2A 协议 · HITL 中断

---

## 一、负载模型推算

### 1.1 基于代码的精确负载分析

| 模块 | 代码路径 | 单次请求资源消耗 | 日均调用量 (6000 商户) |
|------|---------|----------------|---------------------|
| 询盘解析 (DemandAgent) | `demand_agent.py` | LLM 1 次 (~500ms) + DB 1 次 | 30,000 次 |
| 合规检查 (RegGuard) | `export_control.py` | 内存计算 (~5ms) + embargo_keywords.json 匹配 | 30,000 次 |
| 本地库存查询 (local_inventory_node) | `matching_graph.py` | DB ilike 查询 (~20ms) 或 MockInventory (~1ms) | 30,000 次 |
| LLM 寻源 (llm_sourcing_node) | `matching_graph.py` | LLM 1 次 (~2-5s) + A2A 模拟 (~1.5s) | 12,000 次 (40% 未命中本地) |
| 谈判引擎 (NegotiatorAgent) | `negotiator.py` | LLM 1 次 + EpisodicMemory DB 查询 + Ticker 事件 | 20,000 次 |
| 阶梯报价 (TieredQuoteEngine) | `tiered_quote.py` | 纯计算 (~10ms) | 20,000 次 |
| 采购对冲 (procurement_graph) | `procurement_graph.py` | asyncio.gather 3 并发 + DB 写入 3 次 | 15,000 次 |
| 文档生成 (DocuForge) | `invoice_generator.py` | Jinja2 渲染 + Playwright PDF (~3s) + SHA-256 | 10,000 次 |
| ASKB 查询 | `askb_agent.py` | MockInventory 查询 + MarketDataBus 读取 | 5,000 次 |
| SSE 推送 (merchant/stream) | `main.py` | Redis Pub/Sub 长连接 | 3,000-6,000 持久连接 |
| MarketDataBus 事件 | `ticker_plant.py` | Redis Pub/Sub 广播 | 50,000 事件/天 |

### 1.2 峰值并发计算

```
6,000 商户 × 10% 同时在线 = 600 活跃用户
600 用户 × 平均 1 req/10s = 60 QPS (常态)
峰值 (促销/紧急采购): 60 × 5 = 300 QPS
SSE 长连接: 600 个持久 TCP 连接
LLM 推理队列: 峰值 30 并发 (每次 2-5s)
```

### 1.3 存储增长预测

| 数据类型 | 单条大小 | 日增量 | 年增量 |
|---------|---------|--------|--------|
| TransactionLedger | ~500B | 15,000 条 = 7.5MB | 2.7GB |
| NegotiationRound | ~1KB | 60,000 条 = 60MB | 22GB |
| PurchaseOrder | ~2KB | 10,000 条 = 20MB | 7.3GB |
| DocumentHash (PDF 元数据) | ~200B | 10,000 条 = 2MB | 730MB |
| PDF 文件 (DocuForge) | ~50KB | 10,000 份 = 500MB | 183GB |
| 审计日志 (加密) | ~500B | 100,000 条 = 50MB | 18GB |
| pgvector 向量 | ~4KB | 1,000 条 = 4MB | 1.5GB |
| 总计 | | ~640MB/天 | ~235GB/年 |

---

## 二、完整硬件配置

### 2.1 香港云服务器集群

#### 应用服务器 ×2 (负载均衡)

| 配置项 | 规格 | 选型理由 |
|--------|------|---------|
| 云商 | 腾讯云香港 (ap-hongkong) | 东南亚延迟 30-80ms, 中国大陆回源 <20ms |
| 机型 | SA3.4XLARGE32 | AMD EPYC 第三代 |
| CPU | 16 核 | gunicorn 4 workers × uvicorn (每 worker 4 线程) + Next.js + Nginx |
| 内存 | 32GB | FastAPI 进程 ~2GB × 4 + Next.js ~500MB + Nginx ~100MB + 系统 ~4GB + 余量 |
| 系统盘 | 200GB SSD (Enhanced) | Docker 镜像 (~5GB) + 项目代码 + 日志 + Playwright Chromium (~500MB) |
| 带宽 | 按流量计费, 上限 20Mbps | 6000 商户 SSE + API 响应 |
| 系统 | Ubuntu 24.04 LTS | 与当前开发环境一致 |
| 安全组 | 见下方防火墙规则 | |

每台服务器运行的进程:
```
├── Docker: tradestealth-backend (gunicorn 4w × uvicorn)
│   ├── FastAPI :8900
│   ├── LangGraph StateGraph (15+ nodes)
│   ├── MarketDataBus (Redis Pub/Sub client)
│   └── Playwright Chromium pool (DocuForge PDF)
├── PM2: tradestealth-web (Next.js 16)
│   └── :3000
└── Nginx
    ├── :80 → redirect :443
    └── :443 → proxy_pass :8900 + :3000
```

#### 数据库服务器 ×1 (独立部署)

| 配置项 | 规格 | 选型理由 |
|--------|------|---------|
| 机型 | SA3.2XLARGE32 | |
| CPU | 8 核 | PostgreSQL 并行查询 + WAL 写入 |
| 内存 | 32GB | shared_buffers=8GB + work_mem=128MB + OS cache |
| 系统盘 | 50GB SSD | |
| 数据盘 | 500GB SSD (Enhanced, IOPS 6000) | 年增长 235GB, 留 2 年余量 |
| 系统 | Ubuntu 24.04 LTS | |

PostgreSQL 15 关键配置:
```ini
# /etc/postgresql/15/main/postgresql.conf
max_connections = 200
shared_buffers = 8GB
effective_cache_size = 24GB
work_mem = 128MB
maintenance_work_mem = 1GB
wal_buffers = 64MB
checkpoint_completion_target = 0.9
random_page_cost = 1.1
effective_io_concurrency = 200
max_worker_processes = 8
max_parallel_workers_per_gather = 4
max_parallel_workers = 8

# pgvector 扩展 (向量检索)
shared_preload_libraries = 'vector'
```

#### Redis 服务器 ×1

| 配置项 | 规格 | 选型理由 |
|--------|------|---------|
| 方案 A (推荐) | 腾讯云 Redis 标准版 8GB | 托管免运维, 自动备份 |
| 方案 B (自建) | SA3.MEDIUM8 (2C8G) | 手动运维 |
| 用途 | MarketDataBus Pub/Sub + LangGraph checkpoint + 会话缓存 |

Redis 配置:
```ini
maxmemory 6gb
maxmemory-policy allkeys-lru
# Pub/Sub 不受 maxmemory 限制
# 预计内存使用: Pub/Sub channels ~100MB + checkpoint ~2GB + cache ~2GB
```

### 2.2 本地 AI 工作站

| 组件 | 型号 | 规格 | 价格 (¥) | 选型理由 |
|------|------|------|---------|---------|
| CPU | AMD Ryzen 9 9950X | 16C/32T, 5.7GHz | 4,500 | asyncio 并发 + pytest 116 测试并行 + Docker |
| 主板 | ASUS ProArt X870E-CREATOR | AM5, 2×PCIe 5.0 x16 | 3,000 | 双 GPU 扩展 + 万兆网卡 |
| GPU | NVIDIA RTX 5090 32GB | 32GB GDDR7 | 16,000 | qwen3:32b Q4 (20GB VRAM) + qwen-vl:7b (5GB) 同时加载 |
| 内存 | 128GB DDR5-6000 (4×32GB) | G.Skill Trident Z5 | 3,500 | Ollama 模型加载 + PostgreSQL 本地 + Docker + Playwright |
| NVMe 1 | Samsung 990 PRO 4TB | PCIe 4.0, 7450MB/s | 3,500 | 系统 + 项目 + Docker 镜像 |
| NVMe 2 | Samsung 990 PRO 2TB | PCIe 4.0 | 1,500 | Ollama 模型专用 (qwen3 各版本 ~50GB + VLM ~10GB) |
| 电源 | Corsair RM1000x 2024 | 1000W 80+ Gold | 1,200 | RTX 5090 TDP 575W + 系统 ~200W |
| 散热 | Arctic Liquid Freezer III 360 | 360mm AIO | 800 | 9950X 满载 ~170W |
| 机箱 | Fractal Design Torrent | 全塔, 优秀风道 | 1,500 | GPU 散热空间 |
| UPS | APC Smart-UPS 2200VA | 在线互动式 | 3,000 | 防断电保护 LLM 推理中间状态 |
| 网卡 | Intel X710-DA2 10GbE | 双口万兆 SFP+ | 1,500 | VPN 隧道到香港 (高带宽低延迟) |
| 显示器 | Dell U2723QE ×2 | 27" 4K IPS, USB-C | 6,000 | Bloomberg TUI + God Dashboard + 开发 |
| 总计 | | | ¥46,000 | |

Ollama 模型部署:
```bash
# 安装 Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 下载模型
ollama pull qwen3:14b      # 主推理模型 (~10GB VRAM, Q4)
ollama pull qwen3:32b      # 高精度模型 (~20GB VRAM, Q4)
ollama pull qwen-vl:7b     # VLM 自愈 (~5GB VRAM)

# 验证
ollama list
# qwen3:14b    10GB
# qwen3:32b    20GB
# qwen-vl:7b    5GB
```

---

## 三、网络架构详细配置

### 3.1 网络拓扑

```
                    ┌─────────────────────────────┐
                    │     Cloudflare (免费 Plan)    │
                    │  DNS + CDN + SSL + DDoS 防护  │
                    │  域名: projectclaw.com        │
                    └──────────────┬──────────────┘
                                   │ HTTPS
                    ┌──────────────▼──────────────┐
                    │   腾讯云 CLB (香港)            │
                    │   公网 IP: x.x.x.x           │
                    │   监听: 80→443 redirect       │
                    │         443→后端 (加权轮询)    │
                    └──────┬───────────────┬──────┘
                           │ VPC 内网       │
              ┌────────────▼──┐    ┌──────▼────────────┐
              │ App Server 1   │    │ App Server 2       │
              │ 10.0.1.10      │    │ 10.0.1.11          │
              │ :8900 (API)    │    │ :8900 (API)        │
              │ :3000 (Next)   │    │ :3000 (Next)       │
              └──────┬─────────┘    └──────┬─────────────┘
                     │                     │
              ┌──────▼─────────────────────▼──────┐
              │        VPC 子网: 10.0.2.0/24       │
              │                                    │
              │  ┌──────────────┐ ┌──────────────┐│
              │  │ PostgreSQL   │ │ Redis 8GB    ││
              │  │ 10.0.2.10   │ │ 10.0.2.20    ││
              │  │ :5432       │ │ :6379        ││
              │  └──────────────┘ └──────────────┘│
              └────────────────────────────────────┘
                           │
                    WireGuard VPN
                    UDP :51820
                           │
              ┌────────────▼──────────────────────┐
              │     本地 AI 工作站 (深圳/国内)       │
              │     VPN IP: 10.66.66.2             │
              │     Ollama :11434 (qwen3:14b/32b)  │
              │     Ollama :11435 (qwen-vl:7b)     │
              │     开发环境 + Bloomberg TUI         │
              └────────────────────────────────────┘
```

### 3.2 VPC 网络配置

```
VPC CIDR: 10.0.0.0/16
├── 子网 1 (应用层): 10.0.1.0/24
│   ├── App Server 1: 10.0.1.10
│   └── App Server 2: 10.0.1.11
├── 子网 2 (数据层): 10.0.2.0/24
│   ├── PostgreSQL: 10.0.2.10
│   └── Redis: 10.0.2.20
└── 子网 3 (管理): 10.0.3.0/24
    └── 堡垒机 (可选): 10.0.3.10
```

### 3.3 安全组规则

#### 应用服务器安全组

| 方向 | 来源/目标 | 协议 | 端口 | 说明 |
|------|----------|------|------|------|
| 入站 | CLB 内网 IP | TCP | 8900 | FastAPI API |
| 入站 | CLB 内网 IP | TCP | 3000 | Next.js |
| 入站 | 10.66.66.0/24 | TCP | 22 | SSH (仅 VPN) |
| 入站 | 0.0.0.0/0 | UDP | 51820 | WireGuard VPN |
| 出站 | 10.0.2.10 | TCP | 5432 | PostgreSQL |
| 出站 | 10.0.2.20 | TCP | 6379 | Redis |
| 出站 | 0.0.0.0/0 | TCP | 443 | 外部 API (DeepSeek/OpenAI) |
| 出站 | 10.66.66.2 | TCP | 11434-11435 | 本地 Ollama (VPN) |

#### 数据库安全组

| 方向 | 来源/目标 | 协议 | 端口 | 说明 |
|------|----------|------|------|------|
| 入站 | 10.0.1.0/24 | TCP | 5432 | 仅应用层访问 |
| 入站 | 10.66.66.2 | TCP | 5432 | 本地开发 (VPN) |
| 出站 | 无 | - | - | 数据库不主动外连 |

#### Redis 安全组

| 方向 | 来源/目标 | 协议 | 端口 | 说明 |
|------|----------|------|------|------|
| 入站 | 10.0.1.0/24 | TCP | 6379 | 仅应用层访问 |
| 入站 | 10.66.66.2 | TCP | 6379 | 本地开发 (VPN) |

### 3.4 WireGuard VPN 详细配置

#### 香港 App Server 1 (VPN 服务端)

```bash
# 安装
sudo apt install -y wireguard

# 生成密钥
wg genkey | sudo tee /etc/wireguard/server_private.key | wg pubkey | sudo tee /etc/wireguard/server_public.key
sudo chmod 600 /etc/wireguard/server_private.key

# 配置
sudo cat > /etc/wireguard/wg0.conf << 'EOF'
[Interface]
Address = 10.66.66.1/24
ListenPort = 51820
PrivateKey = <server_private_key>
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

[Peer]
# 本地工作站
PublicKey = <local_public_key>
AllowedIPs = 10.66.66.2/32
EOF

sudo systemctl enable wg-quick@wg0
sudo systemctl start wg-quick@wg0
```

#### 本地工作站 (VPN 客户端, Windows)

下载 WireGuard Windows 客户端: https://www.wireguard.com/install/

```ini
[Interface]
PrivateKey = <local_private_key>
Address = 10.66.66.2/24
DNS = 8.8.8.8

[Peer]
PublicKey = <server_public_key>
Endpoint = <HK_SERVER_PUBLIC_IP>:51820
AllowedIPs = 10.66.66.0/24, 10.0.0.0/16
PersistentKeepalive = 25
```

### 3.5 域名 + SSL 配置

```
1. 购买域名: projectclaw.com (Cloudflare Registrar, ~$10/年)
2. Cloudflare DNS:
   A    @           → CLB 公网 IP
   A    www         → CLB 公网 IP
   A    api         → CLB 公网 IP
   CNAME _acme      → Cloudflare 自动管理

3. Cloudflare SSL: Full (Strict)
   - 边缘证书: Cloudflare 自动签发
   - 源站证书: Cloudflare Origin CA (15 年有效)

4. Cloudflare 安全规则:
   - WAF: 开启 OWASP Core Ruleset
   - Rate Limit: 100 req/min per IP
   - Bot Fight Mode: 开启
```

---

## 四、环境变量配置 (.env)

```env
# ═══ 生产环境 (香港服务器) ═══

# 关闭 DEMO 模式
IS_DEMO_MODE=false

# PostgreSQL (VPC 内网)
DATABASE_URL=postgresql+asyncpg://postgres:<STRONG_PASSWORD>@10.0.2.10:5432/tradestealth
TS_PROCUREMENT_STRICT_DB=1

# Redis (VPC 内网)
REDIS_URL=redis://10.0.2.20:6379/0

# AES 加密
AES_MASTER_KEY=<64位hex密钥>

# CORS
ALLOWED_ORIGINS=https://projectclaw.com,https://www.projectclaw.com

# LLM — 本地 Ollama (通过 VPN 隧道)
OLLAMA_BASE_URL=http://10.66.66.2:11434
INTENT_MODEL=qwen3:14b
STRATEGY_MODEL=qwen3:14b

# LLM — 外部 API (直连)
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat

# RPA
HEADLESS=true
MINER_MODE=live
TS_FX_VOLATILITY_SOURCE=http
TS_FX_API_URL=https://api.exchangerate-api.com/v4/latest/USD
```

---

## 五、部署步骤 (按顺序执行)

### Step 1: 购买云资源
1. 腾讯云香港区域创建 VPC (10.0.0.0/16)
2. 购买 2 台 SA3.4XLARGE32 (应用服务器)
3. 购买 1 台 SA3.2XLARGE32 (数据库服务器)
4. 购买腾讯云 Redis 标准版 8GB
5. 购买 CLB 共享型负载均衡器

### Step 2: 配置网络
1. 创建子网 10.0.1.0/24 和 10.0.2.0/24
2. 配置安全组规则 (见 3.3)
3. CLB 绑定公网 IP, 配置监听器 (443→8900, 443→3000)

### Step 3: 部署数据库
```bash
# 在 DB 服务器上
sudo apt install -y postgresql-15 postgresql-15-pgvector
sudo -u postgres createdb tradestealth
sudo -u postgres psql -c "ALTER USER postgres PASSWORD '<STRONG_PASSWORD>';"
# 修改 postgresql.conf (见 2.1)
# 修改 pg_hba.conf 允许 10.0.1.0/24 访问
sudo systemctl restart postgresql
```

### Step 4: 部署应用 (两台服务器相同操作)
```bash
# Docker + Nginx + Node.js (同当前流程)
curl -fsSL https://get.docker.com | sudo sh
sudo apt install -y nginx nodejs npm
sudo npm install -g pm2

# 拉取代码
cd /opt && sudo git clone https://github.com/VivaLaVidad/Agent-trade.git TradeStealth_Core
cd TradeStealth_Core

# 配置 .env (见第四节)
cp .env.example .env && nano .env

# 修改 docker-compose: 移除 postgres 和 redis 容器 (使用独立服务器)
# 只保留 agent-backend

# 启动
sudo docker compose -f docker-compose.prod.yml up -d --build
cd frontend_web && npm install && npm run build
pm2 start npm --name "tradestealth-web" -- start
```

### Step 5: 配置 WireGuard VPN (见 3.4)

### Step 6: 配置域名 + Cloudflare (见 3.5)

### Step 7: 验证
```bash
curl https://projectclaw.com/health
curl -X POST https://projectclaw.com/api/v1/buyer/flash-intent \
  -H "Content-Type: application/json" \
  -d '{"sku":"100nF","quantity":1000,"target_country":"VN","is_urgent":true}'
```

---

## 六、月费总览

| 资源 | 规格 | 月费 (USD) |
|------|------|-----------|
| App Server ×2 | SA3.4XLARGE32 (16C32G) | 2 × $150 = $300 |
| DB Server | SA3.2XLARGE32 (8C32G) + 500G SSD | $200 |
| Redis 托管 | 8GB 标准版 | $50 |
| CLB | 共享型 | $15 |
| 带宽 (按流量) | ~1TB/月 | $80 |
| Cloudflare | Free Plan | $0 |
| 域名 | .com | $1 |
| 外部 LLM API | DeepSeek ~$50 + OpenAI ~$30 | $80 |
| 总计 | | 约 $726/月 (¥5,200) |

本地工作站 (一次性): ¥46,000
