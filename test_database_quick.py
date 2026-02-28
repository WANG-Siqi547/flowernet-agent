#!/usr/bin/env python3
"""
快速数据库集成测试
验证 FlowerNet 的 Database 自动存储功能
"""

import http.client
import json
import time
from datetime import datetime

# 颜色输出
class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def print_success(msg):
    print(f"{bcolors.OKGREEN}✅ {msg}{bcolors.ENDC}")

def print_error(msg):
    print(f"{bcolors.FAIL}❌ {msg}{bcolors.ENDC}")

def print_info(msg):
    print(f"{bcolors.OKCYAN}ℹ️  {msg}{bcolors.ENDC}")

def print_header(msg):
    print(f"\n{bcolors.HEADER}{bcolors.BOLD}{'='*80}")
    print(f"{msg}")
    print(f"{'='*80}{bcolors.ENDC}\n")


def http_post(host, port, path, data, timeout=120):
    """使用 http.client 发送 POST 请求"""
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        body = json.dumps(data)
        conn.request("POST", path, body, {"Content-Type": "application/json"})
        response = conn.getresponse()
        result_data = response.read().decode()
        status = response.status
        conn.close()
        
        # 调试输出
        if status != 200:
            print(f"[DEBUG] HTTP {status} from {host}:{port}{path}")
            print(f"[DEBUG] Response body: {result_data[:200]}")
        
        if not result_data or not result_data.strip():
            raise Exception(f"Empty response body (HTTP {status})")
        
        try:
            return json.loads(result_data)
        except json.JSONDecodeError as e:
            print(f"[DEBUG] Failed to parse JSON: {e}")
            print(f"[DEBUG] Raw response: {result_data[:500]}")
            raise Exception(f"Invalid JSON response: {str(e)} - Body: {result_data[:100]}")
    except Exception as e:
        raise Exception(f"HTTP POST {host}:{port}{path} failed: {e}")


def check_services():
    """检查所有服务是否在线"""
    print_header("🔍 检查服务状态")
    
    services = {
        "Verifier": 8000,
        "Controller": 8001,
        "Generator": 8002,
        "Outliner": 8003
    }
    
    all_ok = True
    for name, port in services.items():
        try:
            conn = http.client.HTTPConnection(f"localhost", port, timeout=3)
            conn.request("GET", "/")
            response = conn.getresponse()
            conn.close()
            
            if response.status == 200:
                print_success(f"{name} (端口 {port}) - 在线")
            else:
                print_error(f"{name} (端口 {port}) - 响应异常 (HTTP {response.status})")
                all_ok = False
        except Exception as e:
            print_error(f"{name} (端口 {port}) - 离线: {e}")
            all_ok = False
    
    return all_ok


def test_single_subsection():
    """测试单个 subsection 的数据库存储"""
    print_header("🧪 测试 1: 单个 Subsection 数据库存储")
    
    document_id = f"test_single_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    print_info(f"文档ID: {document_id}")
    print_info("生成单个 subsection...")
    
    # 调用 Generator
    request_data = {
        "outline": "介绍人工智能的基本概念和定义",
        "initial_prompt": "请撰写一段关于人工智能基本概念的内容，字数约200-300字。要求专业、准确、易懂。",
        "document_id": document_id,
        "section_id": "section_1",
        "subsection_id": "subsection_1_1",
        "history": [],
        "rel_threshold": 0.4,
        "red_threshold": 0.7
    }
    
    try:
        print_info("调用 Generator API...")
        result = http_post("localhost", 8002, "/generate_section", request_data, timeout=120)
        
        if result.get("success"):
            print_success("生成成功！")
            print(f"   🔄 迭代次数: {result['iterations']}")
            print(f"   📊 相关性: {result['verification']['relevancy_index']:.4f}")
            print(f"   📊 冗余度: {result['verification']['redundancy_index']:.4f}")
            print(f"   💾 已存入数据库: {result.get('stored_in_db', False)}")
            print(f"\n   📄 内容片段:")
            print(f"   {result['draft'][:150]}...")
            
            # 验证数据库中的记录
            print_info("\n验证数据库记录...")
            try:
                history_data = http_post("localhost", 8003, "/history/get", {"document_id": document_id}, timeout=10)
                history_count = len(history_data.get('history', []))
                
                if history_count > 0:
                    print_success(f"数据库验证通过！找到 {history_count} 条记录")
                    print(f"\n   📋 记录详情:")
                    for entry in history_data['history']:
                        print(f"      - Section: {entry['section_id']}")
                        print(f"        Subsection: {entry['subsection_id']}")
                        print(f"        内容长度: {len(entry['content'])} 字符")
                        print(f"        时间: {entry['timestamp']}")
                    return True
                else:
                    print_error("数据库中没有找到记录！")
                    return False
            except Exception as e:
                print_error(f"无法查询数据库: {str(e)}")
                return False
        else:
            print_error(f"生成失败: {result.get('error')}")
            return False
            
    except Exception as e:
        print_error(f"测试失败: {str(e)}")
        return False


def test_mini_document():
    """测试迷你文档生成（2 sections, 每个 2 subsections）"""
    print_header("🧪 测试 2: 迷你文档生成（带数据库存储）")
    
    # 1. 生成大纲
    print_info("步骤 1: 生成文档大纲...")
    
    outline_request = {
        "user_background": "我需要写一篇简短的AI介绍文章。",
        "user_requirements": "介绍人工智能的基础知识，包括定义和简单应用。",
        "max_sections": 2,
        "max_subsections_per_section": 2
    }
    
    try:
        outline_result = http_post("localhost", 8003, "/generate-outline", outline_request, timeout=90)
        
        if not outline_result.get("success"):
            print_error(f"大纲生成失败: {outline_result.get('error')}")
            return False
        
        print_success(f"大纲生成成功: {outline_result['document_title']}")
        print(f"   📊 总共 {outline_result['total_subsections']} 个 subsections")
        
        # 2. 构建层级结构
        print_info("\n步骤 2: 构建层级结构...")
        
        document_id = f"test_doc_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        structure = outline_result["structure"]
        
        formatted_outline = []
        for section in structure["sections"]:
            section_data = {
                "section_id": section["id"],
                "section_title": section["title"],
                "subsections": [
                    {
                        "subsection_id": subsection["id"],
                        "title": subsection["title"],
                        "outline": subsection["description"]
                    }
                    for subsection in section["subsections"]
                ]
            }
            formatted_outline.append(section_data)
        
        print_success(f"文档ID: {document_id}")
        print(f"   📚 Sections: {len(formatted_outline)}")
        for idx, section in enumerate(formatted_outline, 1):
            print(f"      {idx}. {section['section_title']}")
            for sub_idx, subsection in enumerate(section['subsections'], 1):
                print(f"         {idx}.{sub_idx} {subsection['title']}")
        
        # 3. 生成完整文档
        print_info("\n步骤 3: 生成完整文档（这可能需要几分钟）...")
        
        document_request = {
            "document_id": document_id,
            "title": outline_result["document_title"],
            "outline_list": formatted_outline,
            "system_prompt": "请使用简洁、清晰的语言。",
            "rel_threshold": 0.4,
            "red_threshold": 0.7
        }
        
        start_time = time.time()
        document_result = http_post("localhost", 8002, "/generate_document", document_request, timeout=600)
        elapsed = time.time() - start_time
        
        # 4. 展示结果
        print_header("📊 文档生成结果")
        
        print(f"⏱️  耗时: {elapsed:.1f} 秒")
        print(f"📄 文档标题: {document_result.get('title', 'N/A')}")
        print(f"🆔 文档ID: {document_result.get('document_id', 'N/A')}")
        print(f"✅ 成功: {document_result.get('success_count', 0)}/{document_result.get('total_subsections', 0)} subsections")
        print(f"❌ 失败: {len(document_result.get('failed_subsections', []))} subsections")
        print(f"🔄 总迭代: {document_result.get('total_iterations', 0)} 次")
        print(f"🗑️  历史已清空: {document_result.get('history_cleared', False)}")
        
        # 检查每个 subsection 是否存入数据库
        print(f"\n📚 详细结果:")
        all_stored = True
        for section in document_result.get("sections", []):
            print(f"\n   📖 {section['section_title']}")
            for subsection in section.get("subsections", []):
                stored = subsection.get('stored_in_db', False)
                status = "💾✅" if stored else "💾❌"
                print(f"      {status} {subsection['subsection_title']}")
                print(f"         相关性: {subsection['verification']['relevancy_index']:.4f}")
                print(f"         冗余度: {subsection['verification']['redundancy_index']:.4f}")
                print(f"         迭代: {subsection['iterations']} 次")
                if not stored:
                    all_stored = False
        
        # 5. 验证历史已清空
        print_info("\n步骤 4: 验证历史记录已清空...")
        
        try:
            history_data = http_post("localhost", 8003, "/history/get", {"document_id": document_id}, timeout=10)
            history_count = len(history_data.get('history', []))
            
            if history_count == 0:
                print_success("历史记录已成功清空！")
            else:
                print_error(f"历史记录未清空，仍有 {history_count} 条记录")
                all_stored = False
        except Exception as e:
            print_error(f"无法验证历史清空: {str(e)}")
            all_stored = False
        
        # 保存结果
        output_file = f"test_result_{document_id}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(document_result, f, ensure_ascii=False, indent=2)
        
        print_info(f"\n💾 完整结果已保存到: {output_file}")
        
        return all_stored and document_result.get('success_count', 0) > 0
        
    except Exception as e:
        print_error(f"测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主测试流程"""
    print_header("🌸 FlowerNet Database Integration 快速测试")
    
    # 1. 检查服务
    if not check_services():
        print_error("\n服务检查失败！请先启动所有服务：")
        print("  ./start-flowernet-full.sh")
        return False
    
    print_success("\n所有服务正常运行！\n")
    time.sleep(1)
    
    # 2. 测试单个 subsection
    test1_passed = test_single_subsection()
    
    time.sleep(2)
    
    # 3. 测试迷你文档
    test2_passed = test_mini_document()
    
    # 总结
    print_header("📊 测试总结")
    
    if test1_passed:
        print_success("✅ 测试 1: 单个 Subsection 存储 - 通过")
    else:
        print_error("❌ 测试 1: 单个 Subsection 存储 - 失败")
    
    if test2_passed:
        print_success("✅ 测试 2: 迷你文档生成 - 通过")
    else:
        print_error("❌ 测试 2: 迷你文档生成 - 失败")
    
    print("")
    
    if test1_passed and test2_passed:
        print_success("🎉 所有测试通过！Database 集成功能正常工作！")
        return True
    else:
        print_error("⚠️  部分测试失败，请检查日志文件")
        print("\n日志文件:")
        print("  tail -f /tmp/generator.log")
        print("  tail -f /tmp/outliner.log")
        return False


if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)
