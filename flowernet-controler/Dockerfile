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

# 下载 spaCy 英文模型
RUN python -m spacy download en_core_web_sm

# 复制代码文件
COPY algo_toolbox.py .
COPY controler.py .
COPY main.py .

# 暴露端口（从环境变量读取）
EXPOSE 8001

# 启动命令（使用 PORT 环境变量，默认 8001）
CMD sh -c 'uvicorn main:app --host 0.0.0.0 --port ${PORT:-8001}'
