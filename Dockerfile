# 面试模拟 Agent — 一键启动镜像
# 用法: docker compose up --build 后访问 http://localhost:8000
FROM python:3.13-slim

WORKDIR /app

# 先复制依赖清单并安装（利用 Docker 层缓存：源码变动不会重建依赖层）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY . .

EXPOSE 8000

# --app-dir 固定到 /app，避免容器内 cwd 漂移导致模块导入失败
CMD ["python", "-m", "uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app"]
