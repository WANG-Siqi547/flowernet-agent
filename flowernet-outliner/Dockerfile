# FlowerNet Outliner
FROM python:3.9-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY outliner.py .
COPY database.py .
COPY history_store.py .
COPY main.py .

# 暴露端口
EXPOSE 8003

# 设置环境变量
ENV PORT=8003
ENV USE_DATABASE=false

# 启动服务
CMD ["python", "main.py"]
