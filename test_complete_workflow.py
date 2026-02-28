#!/usr/bin/env python3
"""
FlowerNet Complete Document Generation Workflow Test
测试完整文档生成流程: 大纲生成 → 内容生成 → 验证 → 历史记录
"""

import requests
import json
import sqlite3
import time
from datetime import datetime

DB_PATH = "/Users/k1ns9sley/Desktop/msc project/flowernet-agent/flowernet_history.db"
OUTLINER = "http://localhost:8003"
GENERATOR = "http://localhost:8002"
VERIFIER = "http://localhost:8000"
CONTROLLER = "http://localhost:8001"

class DocumentGenerationTest:
    def __init__(self):
        self.doc_id = f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.generated_sections = {}
        self.verified_sections = {}
        
    def print_header(self, title):
        print(f"\n{'='*70}")
        print(f" {title} ".center(70))
        print(f"{'='*70}")
    
    def print_step(self, step_num, title):
        print(f"\n[STEP {step_num}] {title}")
        print("-" * 70)
    
    def step1_generate_outline(self):
        """生成文档大纲"""
        self.print_step(1, "生成文档大纲 (Outliner)")
        
        try:
            payload = {
                "user_background": "计算机科学博士研究生，专注于自然语言处理",
                "user_requirements": "撰写深度学习基础知识概览，包括基本概念、核心算法和应用案例",
                "max_sections": 3,
                "max_subsections_per_section": 2
            }
            
            print(f"📤 发送请求到 {OUTLINER}/generate-outline")
            print(f"   背景: {payload['user_background'][:40]}...")
            print(f"   需求: {payload['user_requirements'][:40]}...")
            
            resp = requests.post(f"{OUTLINER}/generate-outline", json=payload, timeout=30)
            
            if resp.status_code != 200:
                print(f"❌ 失败: HTTP {resp.status_code}")
                print(f"   响应: {resp.text[:200]}")
                return False
            
            outline_data = resp.json()
            self.outline = outline_data
            
            print(f"✅ 成功生成大纲")
            print(f"   文档标题: {outline_data.get('document_title', 'N/A')}")
            print(f"   章节数: {len(outline_data['structure']['sections'])}")
            
            for i, section in enumerate(outline_data['structure']['sections']):
                subsec_count = len(section.get('subsections', []))
                print(f"      [{i+1}] {section['title']} ({subsec_count} 小节)")
            
            return True
            
        except Exception as e:
            print(f"❌ 错误: {e}")
            return False
    
    def step2_save_outline(self):
        """保存大纲到数据库"""
        self.print_step(2, "保存大纲到数据库")
        
        try:
            payload = {
                "document_id": self.doc_id,
                "outline_content": json.dumps(self.outline),
                "outline_type": "document"
            }
            
            print(f"📤 保存大纲...")
            print(f"   文档ID: {self.doc_id}")
            print(f"   大纲大小: {len(json.dumps(self.outline))} 字符")
            
            resp = requests.post(f"{OUTLINER}/outline/save", json=payload, timeout=10)
            
            if resp.status_code != 200:
                print(f"❌ 失败: HTTP {resp.status_code}")
                return False
            
            print(f"✅ 大纲已保存")
            
            # 验证数据库里有数据
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM outlines WHERE document_id = ?", (self.doc_id,))
            count = cursor.fetchone()[0]
            conn.close()
            
            print(f"   数据库记录数: {count}")
            return count > 0
            
        except Exception as e:
            print(f"❌ 错误: {e}")
            return False
    
    def step3_generate_content(self):
        """为每个小节生成内容"""
        self.print_step(3, "生成每个小节的内容 (按顺序)")
        
        try:
            sections = self.outline['structure']['sections']
            cumulative_history = []  # 保存已通过的内容
            
            total_subsections = sum(len(s.get('subsections', [])) for s in sections)
            current = 1
            
            for section_idx, section in enumerate(sections):
                section_title = section['title']
                print(f"\n📚 第 {section_idx + 1} 章节: {section_title}")
                
                for subsec_idx, subsection in enumerate(section.get('subsections', [])):
                    subsec_title = subsection['title']
                    subsec_desc = subsection['description']
                    
                    print(f"\n  [{current}/{total_subsections}] 小节: {subsec_title}")
                    
                    # 构建 prompt，包含历史内容
                    prompt = f"""你是一位资深的深度学习研究员。

## 文档背景
- 文档标题: {self.outline.get('document_title', 'Deep Learning Overview')}
- 当前章节: {section_title}
- 当前小节: {subsec_title}

## 小节描述
{subsec_desc}

## 已生成的内容 (用于保持一致性)
{chr(10).join([f'- {h[:100]}...' for h in cumulative_history[-2:]]) if cumulative_history else '(无)'}

## 任务
请撰写上述小节的内容，字数300-500字，使用清晰的逻辑结构。"""
                    
                    payload = {
                        "prompt": prompt,
                        "max_tokens": 600
                    }
                    
                    print(f"     📤 生成内容中...", end=" ", flush=True)
                    resp = requests.post(f"{GENERATOR}/generate", json=payload, timeout=30)
                    
                    if resp.status_code != 200:
                        print(f"\n     ❌ 失败: HTTP {resp.status_code}")
                        continue
                    
                    gen_data = resp.json()
                    content = gen_data.get('draft', '')
                    
                    print(f"✅ ({len(content)} 字字符)")
                    
                    # 存储生成的内容
                    self.generated_sections[f"{section_idx}_{subsec_idx}"] = {
                        "title": subsec_title,
                        "content": content,
                        "section": section_title
                    }
                    
                    # 添加到历史记录（用于下一个小节）
                    cumulative_history.append(content[:200])
                    
                    # 保存到数据库
                    conn = sqlite3.connect(DB_PATH)
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO history (document_id, section_id, subsection_id, content, timestamp)
                        VALUES (?, ?, ?, ?, datetime('now'))
                    """, (self.doc_id, f"section_{section_idx}", f"subsec_{subsec_idx}", content))
                    conn.commit()
                    conn.close()
                    
                    # 存储到 subsection_tracking
                    conn = sqlite3.connect(DB_PATH)
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO subsection_tracking 
                        (document_id, section_id, subsection_id, outline, generated_content, is_passed, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                    """, (self.doc_id, f"section_{section_idx}", f"subsec_{subsec_idx}", 
                          subsec_desc, content, 1))  # 1 = passed
                    conn.commit()
                    conn.close()
                    
                    # 添加到 passed_history
                    conn = sqlite3.connect(DB_PATH)
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO passed_history 
                        (document_id, section_id, subsection_id, content, order_index, created_at)
                        VALUES (?, ?, ?, ?, ?, datetime('now'))
                    """, (self.doc_id, f"section_{section_idx}", f"subsec_{subsec_idx}", 
                          content, current))
                    conn.commit()
                    conn.close()
                    
                    current += 1
                    time.sleep(1)  # 避免过快请求
            
            print(f"\n✅ 完成 {len(self.generated_sections)} 个小节的内容生成")
            return len(self.generated_sections) > 0
            
        except Exception as e:
            print(f"❌ 错误: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def step4_verify_content(self):
        """验证生成的内容"""
        self.print_step(4, "验证生成的内容质量")
        
        try:
            verified_count = 0
            
            for subsec_key, subsec_data in self.generated_sections.items():
                content = subsec_data['content']
                title = subsec_data['title']
                
                print(f"\n  ✓ 验证: {title}")
                print(f"     内容长度: {len(content)} 字符")
                print(f"     内容预览: {content[:80]}...")
                
                # 简单的质量检查
                checks = {
                    "长度检查": len(content) > 100,
                    "中文检查": any('\u4e00' <= c <= '\u9fff' for c in content),
                    "句子检查": '。' in content or '！' in content or '？' in content
                }
                
                all_passed = all(checks.values())
                
                for check_name, passed in checks.items():
                    status = "✅" if passed else "❌"
                    print(f"       {status} {check_name}")
                
                if all_passed:
                    verified_count += 1
                    self.verified_sections[subsec_key] = True
                else:
                    self.verified_sections[subsec_key] = False
            
            print(f"\n✅ 验证完成: {verified_count}/{len(self.generated_sections)} 小节通过")
            return verified_count > 0
            
        except Exception as e:
            print(f"❌ 错误: {e}")
            return False
    
    def step5_database_check(self):
        """检查数据库状态"""
        self.print_step(5, "数据库完整性检查")
        
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # 检查各表的记录数
            tables = {
                "outlines": f"WHERE document_id = '{self.doc_id}'",
                "subsection_tracking": f"WHERE document_id = '{self.doc_id}'",
                "passed_history": f"WHERE document_id = '{self.doc_id}'",
                "history": f"WHERE document_id = '{self.doc_id}'"
            }
            
            for table_name, where_clause in tables.items():
                cursor.execute(f"SELECT COUNT(*) FROM {table_name} {where_clause}")
                count = cursor.fetchone()[0]
                print(f"  📊 {table_name:25} : {count:3} 记录")
            
            # 显示 passed_history 中的内容摘要
            print(f"\n  📝 历史记录链:")
            cursor.execute("""
                SELECT order_index, subsection_id, LENGTH(content) as content_len
                FROM passed_history 
                WHERE document_id = ?
                ORDER BY order_index
            """, (self.doc_id,))
            
            for order_idx, subsec_id, content_len in cursor.fetchall():
                print(f"       [{order_idx}] {subsec_id:15} - {content_len} 字符")
            
            conn.close()
            print(f"\n✅ 数据库检查完成")
            return True
            
        except Exception as e:
            print(f"❌ 错误: {e}")
            return False
    
    def step6_retrieve_document(self):
        """从数据库检索完整生成的文档"""
        self.print_step(6, "从数据库检索完整文档")
        
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # 按顺序获取所有生成的内容
            cursor.execute("""
                SELECT section_id, subsection_id, content, title
                FROM subsection_tracking
                WHERE document_id = ?
                ORDER BY section_id, subsection_id
            """, (self.doc_id,))
            
            sections_content = {}
            total_content_len = 0
            
            for section_id, subsec_id, content, title in cursor.fetchall():
                if section_id not in sections_content:
                    sections_content[section_id] = []
                
                sections_content[section_id].append({
                    "subsection_id": subsec_id,
                    "title": title,
                    "content": content
                })
                total_content_len += len(content)
            
            conn.close()
            
            print(f"\n  📄 文档结构:")
            print(f"      文档ID: {self.doc_id}")
            print(f"      章节数: {len(sections_content)}")
            print(f"      总字数: {total_content_len}")
            
            for idx, (section_id, subsections) in enumerate(sections_content.items(), 1):
                print(f"\n      第 {idx} 章节 ({section_id}):")
                for subsec in subsections:
                    print(f"        • {subsec['title']}: {len(subsec['content'])} 字符")
            
            print(f"\n✅ 文档检索完成")
            return True
            
        except Exception as e:
            print(f"❌ 错误: {e}")
            return False
    
    def run_all_tests(self):
        """运行完整测试流程"""
        self.print_header("🚀 FlowerNet 完整文档生成流程测试")
        
        print(f"\n📋 测试信息:")
        print(f"   文档ID: {self.doc_id}")
        print(f"   开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        results = {}
        
        results['step1'] = self.step1_generate_outline()
        if not results['step1']:
            print("\n❌ 大纲生成失败，无法继续")
            return results
        
        results['step2'] = self.step2_save_outline()
        if not results['step2']:
            print("\n❌ 大纲保存失败，无法继续")
            return results
        
        results['step3'] = self.step3_generate_content()
        if not results['step3']:
            print("\n⚠️  内容生成失败")
            return results
        
        results['step4'] = self.step4_verify_content()
        results['step5'] = self.step5_database_check()
        results['step6'] = self.step6_retrieve_document()
        
        return results
    
    def print_final_summary(self, results):
        """打印最终总结"""
        self.print_header("📊 测试结果总结")
        
        steps = {
            'step1': '生成大纲',
            'step2': '保存大纲',
            'step3': '生成内容',
            'step4': '验证内容',
            'step5': '数据库检查',
            'step6': '检索文档'
        }
        
        passed = sum(1 for v in results.values() if v)
        total = len(results)
        
        print(f"\n测试阶段结果:")
        for step_key, step_name in steps.items():
            if step_key in results:
                status = "✅ PASS" if results[step_key] else "❌ FAIL"
                print(f"  {status} : {step_name}")
        
        print(f"\n最终结果: {passed}/{total} 阶段成功")
        
        if passed == total:
            print(f"\n🎉 完整文档生成流程测试成功！")
            print(f"   文档ID: {self.doc_id}")
            print(f"   生成章节数: {len(self.generated_sections)}")
            print(f"   已验证章节: {len(self.verified_sections)}")
        else:
            print(f"\n⚠️  有 {total - passed} 个阶段失败")

def main():
    tester = DocumentGenerationTest()
    results = tester.run_all_tests()
    tester.print_final_summary(results)
    
    print(f"\n{'='*70}\n")
    return 0 if all(results.values()) else 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
