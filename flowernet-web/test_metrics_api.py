#!/usr/bin/env python3
"""
FlowerNet 指标 API 测试脚本
验证所有新增的指标展示 API 端点是否正常工作
"""

import requests
import json
import sys
from typing import Dict, Any, List

# Prevent pytest from collecting this operational script as a test module.
__test__ = False

# 配置
API_BASE_URL = "http://localhost:8010"
API_PREFIX = "/api/metrics"
ENDPOINTS = [
    ("/all", "获取所有指标定义"),
    ("/categories", "获取指标分类"),
    ("/features", "获取核心特性"),
    ("/dashboard-summary", "获取仪表板概览"),
    ("/comparison", "获取指标对比分析"),
    ("/documentation", "获取完整文档"),
]

CATEGORY_ENDPOINTS = [
    "/category/内容质量",
    "/category/逻辑证据",
    "/category/结构表达",
    "/category/引用质量",
    "/category/文档质量",
    "/category/生成效率",
]

METRIC_ENDPOINTS = [
    "/metric/relevancy_index",
    "/metric/redundancy_index",
    "/metric/domain_similarity",
    "/metric/quality_score_avg",
]


class Colors:
    """ANSI 颜色代码"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def print_header(text: str):
    """打印标题"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}")
    print(f"{text}")
    print(f"{'='*60}{Colors.RESET}\n")


def print_success(text: str):
    """打印成功信息"""
    print(f"{Colors.GREEN}✅ {text}{Colors.RESET}")


def print_error(text: str):
    """打印错误信息"""
    print(f"{Colors.RED}❌ {text}{Colors.RESET}")


def print_warning(text: str):
    """打印警告信息"""
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.RESET}")


def print_info(text: str):
    """打印信息"""
    print(f"{Colors.CYAN}ℹ️  {text}{Colors.RESET}")


def test_endpoint(endpoint: str, description: str = "") -> Dict[str, Any]:
    """测试单个端点"""
    url = f"{API_BASE_URL}{API_PREFIX}{endpoint}"
    
    try:
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            try:
                data = response.json()
                if data.get("success"):
                    status = "✅ 成功"
                    metrics_count = ""
                    
                    # 提取统计信息
                    if "metrics_count" in data:
                        metrics_count = f" ({data['metrics_count']} 个指标)"
                    elif "categories_count" in data:
                        metrics_count = f" ({data['categories_count']} 个分类)"
                    elif "features_count" in data:
                        metrics_count = f" ({data['features_count']} 个特性)"
                    elif "summary" in data:
                        summary = data['summary']
                        metrics_count = f" (总计: {summary.get('total_metrics', 0)} 指标, {summary.get('total_categories', 0)} 分类)"
                    
                    print_success(f"{description}{metrics_count}")
                    return {"success": True, "data": data}
                else:
                    print_error(f"{description} - 返回 success=false")
                    return {"success": False, "error": "success=false"}
            except json.JSONDecodeError:
                print_error(f"{description} - 响应不是有效的 JSON")
                return {"success": False, "error": "invalid_json"}
        elif response.status_code == 404:
            print_warning(f"{description} - 404 Not Found (端点可能尚未部署)")
            return {"success": False, "error": "not_found"}
        else:
            print_error(f"{description} - HTTP {response.status_code}")
            return {"success": False, "error": f"http_{response.status_code}"}
    except requests.ConnectionError:
        print_error(f"无法连接到 {API_BASE_URL} - 请确保 FlowerNet Web 服务正在运行")
        return {"success": False, "error": "connection_error"}
    except requests.Timeout:
        print_error(f"{description} - 请求超时")
        return {"success": False, "error": "timeout"}
    except Exception as e:
        print_error(f"{description} - {str(e)}")
        return {"success": False, "error": str(e)}


def validate_metrics_structure(metrics: Dict[str, Any]):
    """验证指标结构的完整性"""
    print_info("验证指标数据结构...")
    
    required_fields = ["name", "description", "category", "threshold", "pass_criteria"]
    issues = []
    
    for metric_key, metric_data in metrics.items():
        for field in required_fields:
            if field not in metric_data:
                issues.append(f"  - {metric_key}: 缺少字段 '{field}'")
        
        # 验证特定字段类型
        if not isinstance(metric_data.get("threshold"), (int, float, str, type(None))):
            issues.append(f"  - {metric_key}: threshold 类型不正确")
    
    if issues:
        print_warning(f"发现 {len(issues)} 个结构问题：")
        for issue in issues[:10]:  # 只显示前 10 个
            print(f"    {issue}")
        if len(issues) > 10:
            print(f"    ... 及 {len(issues)-10} 个其他问题")
    else:
        print_success("所有指标结构完整")
    
    return len(issues) == 0


def validate_categories_structure(categories: Dict[str, Any]):
    """验证分类结构的完整性"""
    print_info("验证分类数据结构...")
    
    issues = []
    
    for category_name, category_data in categories.items():
        if not isinstance(category_data, dict):
            issues.append(f"  - {category_name}: 分类数据不是字典")
            continue
        
        if "metrics" not in category_data:
            issues.append(f"  - {category_name}: 缺少 'metrics' 字段")
        
        if not isinstance(category_data.get("metrics"), list):
            issues.append(f"  - {category_name}: 'metrics' 不是列表")
    
    if issues:
        print_warning(f"发现 {len(issues)} 个结构问题：")
        for issue in issues:
            print(f"    {issue}")
    else:
        print_success("所有分类结构完整")
    
    return len(issues) == 0


def test_categories(metrics_data: Dict[str, Any]):
    """测试分类端点"""
    print_header("测试分类端点")
    
    result = test_endpoint("/categories", "获取分类列表")
    if not result["success"]:
        return False
    
    categories = result["data"].get("categories", {})
    
    # 验证分类中的指标是否存在
    print_info("验证分类中指标的有效性...")
    missing_metrics = set()
    
    for category_name, category_data in categories.items():
        metric_keys = category_data.get("metrics", [])
        for metric_key in metric_keys:
            if metric_key not in metrics_data:
                missing_metrics.add(metric_key)
    
    if missing_metrics:
        print_warning(f"发现 {len(missing_metrics)} 个不存在的指标：")
        for metric_key in list(missing_metrics)[:5]:
            print(f"    - {metric_key}")
        if len(missing_metrics) > 5:
            print(f"    ... 及 {len(missing_metrics)-5} 个其他指标")
        return False
    else:
        print_success("所有分类中的指标都有效")
    
    return validate_categories_structure(categories)


def test_specific_endpoints():
    """测试特定的分类和指标端点"""
    print_header("测试特定的分类端点")
    
    success_count = 0
    for endpoint in CATEGORY_ENDPOINTS:
        result = test_endpoint(endpoint, f"获取{endpoint.split('/')[-1]}")
        if result["success"]:
            success_count += 1
    
    print_info(f"特定分类端点: {success_count}/{len(CATEGORY_ENDPOINTS)} 成功")
    
    print_header("测试特定的指标端点")
    
    success_count = 0
    for endpoint in METRIC_ENDPOINTS:
        result = test_endpoint(endpoint, f"获取{endpoint.split('/')[-1]}")
        if result["success"]:
            success_count += 1
    
    print_info(f"特定指标端点: {success_count}/{len(METRIC_ENDPOINTS)} 成功")


def test_data_consistency():
    """测试数据一致性"""
    print_header("验证数据一致性")
    
    # 获取所有指标
    all_result = test_endpoint("/all", "获取所有指标")
    if not all_result["success"]:
        print_warning("无法获取所有指标数据，跳过一致性检查")
        return
    
    all_metrics = all_result["data"].get("metrics", {})
    
    # 获取仪表板数据
    dash_result = test_endpoint("/dashboard-summary", "获取仪表板概览")
    if not dash_result["success"]:
        print_warning("无法获取仪表板数据，跳过一致性检查")
        return
    
    dashboard_metrics_count = dash_result["data"]["summary"].get("total_metrics", 0)
    
    # 比较
    if len(all_metrics) == dashboard_metrics_count:
        print_success(f"指标数量一致: {len(all_metrics)} == {dashboard_metrics_count}")
    else:
        print_error(f"指标数量不一致: {len(all_metrics)} != {dashboard_metrics_count}")


def print_summary(results: List[Dict[str, Any]]):
    """打印总结"""
    print_header("测试总结")
    
    successful = sum(1 for r in results if r["success"])
    total = len(results)
    success_rate = (successful / total * 100) if total > 0 else 0
    
    print(f"通过率: {Colors.GREEN}{successful}/{total}{Colors.RESET} ({success_rate:.1f}%)")
    
    if success_rate == 100:
        print_success("所有端点都正常工作！")
        print_info("下一步: 访问 http://localhost:8010/static/metrics-dashboard.html 查看可视化仪表板")
    elif success_rate >= 80:
        print_warning("大部分端点正常工作，但存在部分问题")
    else:
        print_error("存在较多问题，请检查 FlowerNet Web 服务的配置")
    
    return success_rate == 100


def main():
    """主函数"""
    print(f"\n{Colors.BOLD}{Colors.CYAN}")
    print("  ╔═══════════════════════════════════════════════════════════╗")
    print("  ║     FlowerNet 指标 API 端点测试工具 v1.0                  ║")
    print("  ╚═══════════════════════════════════════════════════════════╝")
    print(Colors.RESET)
    
    print_info(f"目标服务: {API_BASE_URL}")
    print_info(f"API 前缀: {API_PREFIX}\n")
    
    # 测试基本端点
    print_header("测试基本端点")
    
    results = []
    all_metrics_data = None
    
    for endpoint, description in ENDPOINTS:
        result = test_endpoint(endpoint, description)
        results.append(result)
        
        # 保存 all 端点的数据用于后续验证
        if endpoint == "/all" and result["success"]:
            all_metrics_data = result["data"].get("metrics", {})
    
    # 测试特定端点
    test_specific_endpoints()
    
    # 如果成功获取了指标数据，进行验证
    if all_metrics_data:
        print_header("数据质量验证")
        validate_metrics_structure(all_metrics_data)
        
        # 测试分类
        test_categories(all_metrics_data)
        
        # 测试数据一致性
        test_data_consistency()
    
    # 打印总结
    success = print_summary(results)
    
    # 返回退出码
    return 0 if success else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}✋ 测试被用户中断{Colors.RESET}")
        sys.exit(130)
    except Exception as e:
        print(f"\n{Colors.RED}❌ 发生未预期的错误: {str(e)}{Colors.RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
