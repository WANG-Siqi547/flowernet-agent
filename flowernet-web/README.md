# FlowerNet Web

一个最小可用网页：输入主题和章节要求，一键调用 FlowerNet 生成长文档，并支持下载 DOCX。

## 本地运行

```bash
cd flowernet-web
pip install -r requirements.txt
uvicorn main:app --reload --port 8010
```

打开：http://localhost:8010

## 环境变量

- `OUTLINER_URL`：Outliner 服务地址（默认 `http://localhost:8003`）
- `GENERATOR_URL`：Generator 服务地址（默认 `http://localhost:8002`）
- `REQUEST_TIMEOUT`：下游请求超时秒数（默认 `300`）
