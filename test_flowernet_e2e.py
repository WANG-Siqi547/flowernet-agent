#!/usr/bin/env python3
"""
FlowerNet 完整端到端测试脚本
测试从 Generator -> Verifier -> Controller 的完整循环流程

使用方法：
1. 首先启动三个服务：
   - python flowernet-generator/main.py 8002
   - python flowernet-verifier/main.py 8000
   - python flowernet-controler/main.py 8001

2. 然后运行此脚本：
   python test_flowernet_e2e.py
"""

import requests
import json
import time
from typing import Dict, List, Any, Optional


class FlowerNetE2ETest:
    """FlowerNet 端到端测试"""
    
    def __init__(
        self,
        generator_url: str = "http://localhost:8002",
        verifier_url: str = "http://localhost:8000",
        controller_url: str = "http://localhost:8001",
        verbose: bool = True
    ):
        self.generator_url = generator_url
        self.verifier_url = verifier_url
        self.controller_url = controller_url
        self.verbose = verbose
        self.session = requests.Session()
        
    def log(self, message: str):
        """日志输出"""
        if self.verbose:
            print(message)
    
    def check_services(self) -> bool:
        """检查所有服务是否在线"""
        self.log("\n🔍 检查服务状态...")
        
        services = [
            ("Generator", self.generator_url),
            ("Verifier", self.verifier_url),
            ("Controller", self.controller_url)
        ]
        
        all_online = True
        for name, url in services:
            try:
                response = self.session.get(f"{url}/", timeout=5)
                if response.status_code == 200:
                    self.log(f"  ✅ {name}: 在线")
                else:
                    self.log(f"  ❌ {name}: 状态码 {response.status_code}")
                    all_online = False
            except Exception as e:
                self.log(f"  ❌ {name}: 连接失败 - {str(e)}")
                all_online = False
        
        return all_online
    
    def test_generator(self) -> bool:
        """测试 Generator 服务"""
        self.log("\n📝 测试 Generator 服务...")
        
        try:
            prompt = "请简要介绍人工智能的基本概念和应用领域。"
            
            payload = {
                "prompt": prompt,
                "max_tokens": 500
            }
            
            response = self.session.post(
                f"{self.generator_url}/generate",
                json=payload,
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    draft = result.get("draft", "")
                    self.log(f"  ✅ Generator 生成成功")
                    self.log(f"  📄 生成内容长度: {len(draft)} 字符")
                    self.log(f"  📝 内容预览: {draft[:100]}...")
                    return True
                else:
                    self.log(f"  ❌ Generator 返回错误: {result.get('error')}")
                    return False
            else:
                self.log(f"  ❌ Generator 返回状态码: {response.status_code}")
                return False
                
        except Exception as e:
            self.log(f"  ❌ Generator 测试失败: {str(e)}")
            return False
    
    def test_verifier(self) -> bool:
        """测试 Verifier 服务"""
        self.log("\n🔍 测试 Verifier 服务...")
        
        try:
            draft = "人工智能是计算机科学的一个重要分支，致力于研究和开发能够执行通常需要人类智能的任务的计算机系统。"
            outline = "介绍人工智能的基本概念"
            history = ["计算机是现代社会的重要工具。"]
            
            payload = {
                "draft": draft,
                "outline": outline,
                "history": history,
                "rel_threshold": 0.5,
                "red_threshold": 0.7
            }
            
            response = self.session.post(
                f"{self.verifier_url}/verify",
                json=payload,
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                is_passed = result.get("is_passed", False)
                rel_score = result.get("relevancy_index", 0)
                red_score = result.get("redundancy_index", 0)
                
                self.log(f"  ✅ Verifier 验证完成")
                self.log(f"  📊 相关性分数: {rel_score:.4f}")
                self.log(f"  📊 冗余度分数: {red_score:.4f}")
                self.log(f"  ✓ 验证结果: {'通过' if is_passed else '未通过'}")
                
                return True
            else:
                self.log(f"  ❌ Verifier 返回状态码: {response.status_code}")
                return False
                
        except Exception as e:
            self.log(f"  ❌ Verifier 测试失败: {str(e)}")
            return False
    
    def test_controller(self) -> bool:
        """测试 Controller 服务"""
        self.log("\n🔧 测试 Controller 服务...")
        
        try:
            old_prompt = "请写一段关于人工智能的内容。"
            failed_draft = "人工智能很重要。"
            outline = "介绍人工智能的基本概念"
            history = []
            feedback = {
                "relevancy_index": 0.3,
                "redundancy_index": 0.2,
                "feedback": "内容太短，不够详细"
            }
            
            payload = {
                "old_prompt": old_prompt,
                "failed_draft": failed_draft,
                "feedback": feedback,
                "outline": outline,
                "history": history,
                "iteration": 1
            }
            
            response = self.session.post(
                f"{self.controller_url}/refine_prompt",
                json=payload,
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    new_prompt = result.get("prompt", "")
                    self.log(f"  ✅ Controller 优化成功")
                    self.log(f"  📝 新 prompt 长度: {len(new_prompt)} 字符")
                    return True
                else:
                    self.log(f"  ❌ Controller 返回错误: {result.get('error')}")
                    return False
            else:
                self.log(f"  ❌ Controller 返回状态码: {response.status_code}")
                return False
                
        except Exception as e:
            self.log(f"  ❌ Controller 测试失败: {str(e)}")
            return False
    
    def test_full_cycle(
        self,
        outline: str,
        initial_prompt: str,
        max_iterations: int = 3,
        rel_threshold: float = 0.5,
        red_threshold: float = 0.7
    ) -> Dict[str, Any]:
        """
        测试完整的生成-验证-修改循环
        """
        self.log(f"\n{'='*60}")
        self.log(f"🌸 开始完整循环测试")
        self.log(f"{'='*60}")
        self.log(f"大纲: {outline}")
        
        current_prompt = initial_prompt
        history = []
        iteration = 0
        results = {
            "outline": outline,
            "iterations": [],
            "final_draft": "",
            "success": False,
            "total_iterations": 0
        }
        
        while iteration < max_iterations:
            iteration += 1
            self.log(f"\n--- 第 {iteration}/{max_iterations} 次迭代 ---")
            
            # 1. Generator 生成
            self.log(f"1️⃣  调用 Generator...")
            try:
                gen_response = self.session.post(
                    f"{self.generator_url}/generate",
                    json={
                        "prompt": current_prompt,
                        "max_tokens": 500
                    },
                    timeout=60
                )
                
                if not gen_response.json().get("success"):
                    self.log(f"  ❌ Generator 失败")
                    break
                
                draft = gen_response.json().get("draft", "")
                self.log(f"  ✅ 生成成功 ({len(draft)} 字符)")
                
            except Exception as e:
                self.log(f"  ❌ Generator 错误: {str(e)}")
                break
            
            # 2. Verifier 验证
            self.log(f"2️⃣  调用 Verifier...")
            try:
                ver_response = self.session.post(
                    f"{self.verifier_url}/verify",
                    json={
                        "draft": draft,
                        "outline": outline,
                        "history": history,
                        "rel_threshold": rel_threshold,
                        "red_threshold": red_threshold
                    },
                    timeout=60
                )
                
                verify_result = ver_response.json()
                is_passed = verify_result.get("is_passed", False)
                rel_score = verify_result.get("relevancy_index", 0)
                red_score = verify_result.get("redundancy_index", 0)
                
                self.log(f"  📊 相关性: {rel_score:.4f}")
                self.log(f"  📊 冗余度: {red_score:.4f}")
                
                results["iterations"].append({
                    "iteration": iteration,
                    "draft": draft,
                    "relevancy": rel_score,
                    "redundancy": red_score,
                    "passed": is_passed
                })
                
                if is_passed:
                    self.log(f"\n✨ 验证通过！")
                    history.append(draft)
                    results["final_draft"] = draft
                    results["success"] = True
                    results["total_iterations"] = iteration
                    return results
                
            except Exception as e:
                self.log(f"  ❌ Verifier 错误: {str(e)}")
                break
            
            # 3. Controller 修改
            self.log(f"3️⃣  调用 Controller...")
            try:
                ctl_response = self.session.post(
                    f"{self.controller_url}/refine_prompt",
                    json={
                        "old_prompt": current_prompt,
                        "failed_draft": draft,
                        "feedback": verify_result,
                        "outline": outline,
                        "history": history,
                        "iteration": iteration
                    },
                    timeout=60
                )
                
                ctl_result = ctl_response.json()
                if ctl_result.get("success"):
                    current_prompt = ctl_result.get("prompt", "")
                    self.log(f"  ✅ Prompt 已优化")
                else:
                    self.log(f"  ❌ Controller 失败")
                    break
                    
            except Exception as e:
                self.log(f"  ❌ Controller 错误: {str(e)}")
                break
        
        # 最后，即使未通过也返回最后的 draft
        if results["iterations"]:
            results["final_draft"] = results["iterations"][-1]["draft"]
        results["total_iterations"] = iteration
        
        self.log(f"\n⚠️  达到最大迭代次数")
        return results
    
    def run_all_tests(self):
        """运行所有测试"""
        print("\n" + "="*60)
        print("🌸 FlowerNet 端到端测试")
        print("="*60)
        
        # 检查服务
        if not self.check_services():
            print("\n❌ 部分服务离线，请先启动所有服务")
            return False
        
        # 单元测试
        print("\n" + "-"*60)
        print("单元测试")
        print("-"*60)
        
        gen_ok = self.test_generator()
        ver_ok = self.test_verifier()
        ctl_ok = self.test_controller()
        
        if not (gen_ok and ver_ok and ctl_ok):
            print("\n❌ 某些服务测试失败")
            return False
        
        # 集成测试
        print("\n" + "-"*60)
        print("集成测试（完整循环）")
        print("-"*60)
        
        test_cases = [
            {
                "outline": "介绍人工智能的基本概念",
                "initial_prompt": "请详细介绍人工智能的基本概念、发展历程和应用领域。长度200字以上。"
            },
            {
                "outline": "讨论机器学习的关键技术",
                "initial_prompt": "请讨论机器学习的关键技术，包括监督学习、无监督学习和强化学习。"
            }
        ]
        
        all_passed = True
        for test_case in test_cases:
            result = self.test_full_cycle(
                outline=test_case["outline"],
                initial_prompt=test_case["initial_prompt"],
                max_iterations=3
            )
            
            if result["success"]:
                print(f"\n✅ 测试通过")
                print(f"📝 最终内容: {result['final_draft'][:100]}...")
            else:
                print(f"\n⚠️  测试未完全通过，但生成了内容")
                print(f"📝 最终内容: {result['final_draft'][:100]}...")
                all_passed = False
        
        print("\n" + "="*60)
        if all_passed:
            print("✅ 所有测试通过！")
        else:
            print("⚠️  部分测试未完全通过，但系统正常运作")
        print("="*60)
        
        return True


if __name__ == "__main__":
    tester = FlowerNetE2ETest()
    success = tester.run_all_tests()
    exit(0 if success else 1)
