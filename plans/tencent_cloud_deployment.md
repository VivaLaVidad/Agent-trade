# TradeStealth_Core 腾讯云部署指南

> 适用于 Ubuntu 24.04 LTS + Docker 部署方案
> 服务器配置: 4核 8GB + 60GB SSD

---

## 第一阶段: 服务器初始化 (购买后立即执行)

### 1.1 安全组配置

登录腾讯云控制台 → 轻量应用服务器 → 防火墙/安全组

| 端口 | 协议 | 用途 | 操作 |
|------|------|------|------|
| 22 | TCP | SSH 远程登录 | 放行 |
| 80 | TCP | HTTP (Nginx) | 放行 |
| 443 | TCP | HTTPS (Nginx + SSL) | 放行 |

> ⚠️ 不要放行 8900 (FastAPI) 和 5432 (PostgreSQL)，它们只在 Docker 内部网络通信

### 1.2 SSH 连接

```bash
# 方式一: 终端
ssh root@你的公网IP

# 方式二 (推荐): VSCode Remote-SSH
# 1. 安装 Remote-SSH 插件
# 2. Ctrl+Shift+P → Remote-SSH: Connect to Host
# 3. 输入 root@你的公网IP
```

### 1.3 系统更新 + 时区

```bash
apt update && apt upgrade -y
timedatectl set-timezone Asia/Shanghai
```

---

## 第二阶段: 安装基础软件

### 2.1 Docker + Docker Compose

```bash
# 安装 Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker && systemctl start docker

# 验证
docker --version
docker compose version
```

### 2.2 Nginx

```bash
apt install -y nginx
systemctl enable nginx
```

### 2.3 Node.js 20 (前端构建用)

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs
node --version  # 应显示 v20.x
```

### 2.4 Git

```bash
apt install -y git
```

---

## 第三阶段: 上传项目代码

### 方式一: Git Clone (推荐)

```bash
cd /opt
git clone https://github.com/VivaLaVidad/Agent-trade.git TradeStealth_Core
cd TradeStealth_Core
```

### 方式二: SCP 上传 (从本地电脑)

```bash
# 在你的 Windows 电脑上执行:
scp -r D:\桌面\TradeStealth_Core root@你的公网IP:/opt/
```

---

## 第四阶段: 配置环境变量

```bash
cd /opt/TradeStealth_Core
cp .env.example .env
nano .env
```

修改以下关键配置:

```env
# ═══ 必须修改 ═══

# 演示模式 (云端无 Playwright，必须开启)
IS_DEMO_MODE=true

# AES 加密密钥 (运行以下命令生成)
# python3 -c "import secrets; print(secrets.token_hex(32))"
AES_MASTER_KEY=你生成的64位hex字符串

# 数据库 (Docker 内部网络地址)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@agent-postgres:5432/tradestealth

# CORS 白名单 (替换为你的域名)
ALLOWED_ORIGINS=https://你的域名,http://你的公网IP

# Redis (Docker 内部网络)
REDIS_URL=redis://agent-redis:6379/0

# ═══ 保持默认即可 ═══
MINER_MODE=mock
TS_FX_VOLATILITY_SOURCE=mock
HEADLESS=true
SLOW_MO=50
```

保存: `Ctrl+O` → `Enter` → `Ctrl+X`

---

## 第五阶段: Docker 一键启动后端

```bash
cd /opt/TradeStealth_Core

# 构建并启动 (首次约 3-5 分钟)
docker compose -f docker-compose.prod.yml up -d --build

# 查看状态
docker compose -f docker-compose.prod.yml ps

# 查看日志
docker compose -f docker-compose.prod.yml logs -f agent-backend

# 验证健康
curl http://localhost:8900/health
# 应返回: {"status":"ok","machine_bound":"xxxxxxxx****"}
```

如果需要重启:
```bash
docker compose -f docker-compose.prod.yml restart agent-backend
```

---

## 第六阶段: 构建前端

```bash
cd /opt/TradeStealth_Core/frontend_web

# 配置前端环境变量
cat > .env.local << 'EOF'
NEXT_PUBLIC_API_BASE_URL=https://你的域名
EOF

# 安装依赖 + 构建
npm install
npm run build

# 使用 PM2 启动 (生产级进程管理)
npm install -g pm2
pm2 start npm --name "tradestealth-web" -- start
pm2 save
pm2 startup  # 开机自启
```

---

## 第七阶段: 配置 Nginx 反向代理

```bash
nano /etc/nginx/sites-available/tradestealth
```

粘贴以下内容 (替换 `你的域名`):

```nginx
server {
    listen 80;
    server_name 你的域名;

    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;

    # 后端 API 代理
    location /api/ {
        proxy_pass http://127.0.0.1:8900;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    # 健康检查
    location /health {
        proxy_pass http://127.0.0.1:8900;
    }

    # WebSocket 代理
    location /ws {
        proxy_pass http://127.0.0.1:8900;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400s;
    }

    # 前端 (Next.js)
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

启用配置:

```bash
ln -s /etc/nginx/sites-available/tradestealth /etc/nginx/sites-enabled/
rm /etc/nginx/sites-enabled/default  # 删除默认站点
nginx -t  # 测试配置
systemctl reload nginx
```

---

## 第八阶段: HTTPS 证书 (可选但强烈推荐)

> 需要先将域名 DNS 解析到服务器公网 IP

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d 你的域名
# 按提示操作，选择自动重定向 HTTP → HTTPS
```

证书自动续期:
```bash
certbot renew --dry-run  # 测试续期
# Certbot 已自动添加 cron 任务
```

---

## 第九阶段: 最终验证

```bash
# 1. 后端健康
curl https://你的域名/health

# 2. 前端页面
# 浏览器打开:
#   https://你的域名/          → 首页导航
#   https://你的域名/buyer     → 买家门户
#   https://你的域名/merchant  → 套利台
#   https://你的域名/admin     → 上帝视角 (token 输入任意 4+ 字符)

# 3. Docker 服务状态
docker compose -f docker-compose.prod.yml ps
```

---

## 常用运维命令

```bash
# 查看后端日志
docker compose -f docker-compose.prod.yml logs -f agent-backend --tail 100

# 重启后端
docker compose -f docker-compose.prod.yml restart agent-backend

# 重启前端
pm2 restart tradestealth-web

# 更新代码后重新部署
cd /opt/TradeStealth_Core
git pull
docker compose -f docker-compose.prod.yml up -d --build
cd frontend_web && npm run build && pm2 restart tradestealth-web

# 查看资源占用
docker stats
htop
```

---

## 故障排查

| 问题 | 排查命令 |
|------|----------|
| 后端无响应 | `docker compose -f docker-compose.prod.yml logs agent-backend` |
| 数据库连接失败 | `docker compose -f docker-compose.prod.yml logs agent-postgres` |
| 前端 502 | `pm2 logs tradestealth-web` |
| Nginx 报错 | `nginx -t && tail -f /var/log/nginx/error.log` |
| 端口占用 | `ss -tlnp \| grep -E '8900\|3000\|5432'` |
