FROM python:3.9-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码文件
COPY algo_toolbox.py .
COPY controler.py .
COPY main.py .

# 启动命令（优先使用 Render 注入的 PORT）
CMD ["sh", "-c", "echo Starting controller on port ${PORT:-8001} && exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8001}"]
