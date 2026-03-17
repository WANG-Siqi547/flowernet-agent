FROM python:3.13-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制项目文件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码和静态文件
COPY main.py .
COPY static/ ./static/

# 暴露端口（Render 会通过 PORT 环境变量注入实际端口）
EXPOSE 10000

# 运行应用（优先使用 Render 提供的 PORT）
CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
