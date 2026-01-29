#!/usr/bin/env python3
"""
FlowerNet 反向代理
将单一 Ngrok URL 路由到多个本地服务
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.error

class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.proxy_request()
    
    def do_POST(self):
        self.proxy_request()
    
    def proxy_request(self):
        # 路由规则
        if self.path.startswith('/controller') or self.path.startswith('/process'):
            backend = 'http://localhost:8001'
            path = self.path.replace('/controller', '')
        elif self.path.startswith('/verifier') or self.path.startswith('/verify'):
            backend = 'http://localhost:8000'
            path = self.path.replace('/verifier', '')
        elif self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"""
            <h1>FlowerNet Proxy</h1>
            <p>Available endpoints:</p>
            <ul>
                <li><a href="/controller/">/controller/*</a> -> Controller (8001)</li>
                <li><a href="/verifier/">/verifier/*</a> -> Verifier (8000)</li>
            </ul>
            """)
            return
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found. Use /controller or /verifier")
            return
        
        # 转发请求
        target_url = backend + path
        try:
            # 获取请求体
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length) if content_length > 0 else None
            
            # 创建请求
            req = urllib.request.Request(target_url, data=body, method=self.command)
            
            # 复制相关 headers
            for key, value in self.headers.items():
                if key.lower() not in ['host', 'connection']:
                    req.add_header(key, value)
            
            # 发送请求
            response = urllib.request.urlopen(req)
            
            # 返回响应
            self.send_response(response.status)
            for key, value in response.headers.items():
                if key.lower() not in ['connection', 'transfer-encoding']:
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.read())
            
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(f"Backend error: {str(e)}".encode())
    
    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")

if __name__ == '__main__':
    PORT = 9000
    server = HTTPServer(('127.0.0.1', PORT), ProxyHandler)
    print(f"🚀 FlowerNet Proxy running on http://localhost:{PORT}")
    print(f"   /controller/* -> localhost:8001")
    print(f"   /verifier/*   -> localhost:8000")
    server.serve_forever()
