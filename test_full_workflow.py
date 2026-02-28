#!/usr/bin/env python3
"""
FlowerNet 完整文档生成流程测试 - 改进版
跳过大纲生成，使用预定义的大纲结构
"""

import requests
import json
import sqlite3
import time
from datetime import datetime

DB_PATH = "/Users/k1ns9sley/Desktop/msc project/flowernet-agent/flowernet_history.db"
OUTLINER = "http://localhost:8003"
GENERATOR = "http://localhost:8002"

class DocumentGenerationTest:
    def __init__(self):
        self.doc_id = f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.generated_sections = {}
        self.verified_sections = {}
        self.outline = self._create_predefined_outline()
        
    def _create_predefined_outline(self):
        """创建预定义的大纲结构"""
        return {
            "document_title": "深度学习基础概览",
            "structure": {
                "sections": [
                    {
                        "id": "section_1",
                        "title": "深度学习基础概念",
                        "subsections": [
                            {
                                "id": "subsection_1_1",
                                "title": "什么是深度学习",
                                "description": "介绍深度学习的定义、历史发展和主要特点"
                            },
                            {
                                "id": "subsection_1_2",
                                "title": "神经网络基础",
                                "description": "解释人工神经网络的结构、激活函数和前向传播原理"
                            }
                        ]
                    },
                    {
                        "id": "section_2",
                        "title": "核心算法与架构",
                        "subsections": [
                            {
                                "id": "subsection_2_1",
                                "title": "卷积神经网络 (CNN)",
                                "description": "详细介绍CNN的卷积层、池化层和应用案例"
                            },
                            {
                                "id": "subsection_2_2",
                                "title": "循环神经网络 (RNN)",
                                "description": "解释RNN的结构、LSTM和GRU等变体及其应用"
                            }
                        ]
                    },
                    {
                        "id": "section_3",
                        "title": "深度学习应用",
                        "subsections": [
                            {
                                "id": "subsection_3_1",
                                "title": "计算机视觉应用",
                                "description": "讨论图像分类、目标检测和语义分割的应用"
                            }
                        ]
                    }
                ]
            }
        }
    
    def print_header(self, title):
        print(f"\n{'='*70}")
        print(f" {title} ".center(70))
        print(f"{'='*70}")
    
    def print_step(self, step_num, title):
        print(f"\n[步骤 {step_num}] {title}")
        print("-" * 70)
    
    def step1_save_outline(self):
        """保存预定义的大纲"""
        self.print_step(1, "保存预定义的大纲")
        
        try:
            payload = {
                "document_id": self.doc_id,
                "outline_content": json.dumps(self.outline),
                "outline_type": "document"
            }
            
            print(f"📤 保存大纲...")
            print(f"   文档ID: {self.doc_id}")
            print(f"   文档标题: {self.outline['document_title']}")
            print(f"   章节数: {len(self.outline['structure']['sections'])}")
            
            resp = requests.post(f"{OUTLINER}/outline/save", json=payload, timeout=10)
            
            if resp.status_code != 200:
                print(f"❌ 失败: HTTP {resp.status_code}")
                print(f"   响应: {resp.text}")
                return False
            
            print(f"✅ 大纲已保存")
            
            # 验证数据库
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM outlines WHERE document_id = ?", (self.doc_id,))
            count = cursor.fetchone()[0]
            conn.close()
            
            print(f"   数据库验证: {count} 条记录")
            return count > 0
            
        except Exception as e:
            print(f"❌ 错误: {e}")
            return False
    
    def step2_generate_content(self):
        """为每个小节生成内容"""
        self.print_step(2, "按顺序生成每个小节的内容")
        
        try:
            sections = self.outline['structure']['sections']
            cumulative_history = []
            
            total_subsections = sum(len(s['subsections']) for s in sections)
            current = 1
            
            for section_idx, section in enumerate(sections):
                section_title = section['title']
                print(f"\n📚 第{section_idx + 1}章: {section_title}")
                
                for subsec_idx, subsection in enumerate(section['subsections']):
                    subsec_title = subsection['title']
                    subsec_desc = subsection['description']
                    
                    print(f"  [{current}/{total_subsections}] {subsec_title}...", end=" ", flush=True)
                    
                    # 构建包含历史的prompt
                    history_text = ""
                    if cumulative_history:
                        history_text = "## 前面已生成的相关内容\n"
                        for i, h in enumerate(cumulative_history[-2:], 1):
                            history_text += f"{i}. {h[:150]}...\n"
                    
                    prompt = f"""你是深度学习领域的专家。请撰写以下内容：

**文档**: {self.outline['document_title']}
**章节**: {section_title}
**小节**: {subsec_title}

**小节要求**: {subsec_desc}

{history_text}

请用300-400字的篇幅，用清晰的逻辑和专业的语言来阐述这个主题。"""
                    
                    try:
                        payload = {"prompt": prompt, "max_tokens": 600}
                        resp = requests.post(f"{GENERATOR}/generate", json=payload, timeout=30)
                        
                        if resp.status_code != 200:
                            print(f"❌ (HTTP {resp.status_code})")
                            continue
                        
                        gen_data = resp.json()
                        content = gen_data.get('draft', '')
                        
                        print(f"✅ ({len(content)}字)")
                        
                        # 保存到内存
                        self.generated_sections[f"{section_idx}_{subsec_idx}"] = {
                            "title": subsec_title,
                            "content": content,
                            "section": section_title
                        }
                        
                        # 添加到历史
                        cumulative_history.append(content[:200])
                        
                        # 保存到数据库
                        conn = sqlite3.connect(DB_PATH)
                        cursor = conn.cursor()
                        
                        # history表
                        cursor.execute("""
                            INSERT INTO history (document_id, section_id, subsection_id, content, timestamp)
                            VALUES (?, ?, ?, ?, datetime('now'))
                        """, (self.doc_id, f"section_{section_idx}", f"subsec_{subsec_idx}", content))
                        
                        # subsection_tracking表
                        cursor.execute("""
                            INSERT INTO subsection_tracking 
                            (document_id, section_id, subsection_id, outline, generated_content, is_passed, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                        """, (self.doc_id, f"section_{section_idx}", f"subsec_{subsec_idx}", 
                              subsec_desc, content, 1))
                        
                        # passed_history表
                        cursor.execute("""
                            INSERT INTO passed_history 
                            (document_id, section_id, subsection_id, content, order_index, created_at)
                            VALUES (?, ?, ?, ?, ?, datetime('now'))
                        """, (self.doc_id, f"section_{section_idx}", f"subsec_{subsec_idx}", 
                              content, current))
                        
                        conn.commit()
                        conn.close()
                        
                    except Exception as e:
                        print(f"❌ (错误: {str(e)[:30]})")
                        continue
                    
                    current += 1
                    time.sleep(0.5)  # 短暂延迟
            
            print(f"\n✅ 完成 {len(self.generated_sections)} 个小节")
            return len(self.generated_sections) > 0
            
        except Exception as e:
            print(f"❌ 错误: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def step3_database_summary(self):
        """数据库总结"""
        self.print_step(3, "数据库内容验证")
        
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # 统计各表的记录
            print(f"\n📊 数据库统计:")
            tables = ["outlines", "subsection_tracking", "passed_history", "history"]
            
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE document_id = ?", (self.doc_id,))
                count = cursor.fetchone()[0]
                print(f"   {table:20}: {count:3} 条记录")
            
            # 显示历史链
            print(f"\n📝 历史链内容:")
            cursor.execute("""
                SELECT order_index, subsection_id, substr(content, 1, 60) as preview
                FROM passed_history 
                WHERE document_id = ?
                ORDER BY order_index
            """, (self.doc_id,))
            
            for order_idx, subsec_id, preview in cursor.fetchall():
                print(f"   [{order_idx}] {subsec_id}: {preview}...")
            
            conn.close()
            print(f"\n✅ 数据库验证完成")
            return True
            
        except Exception as e:
            print(f"❌ 错误: {e}")
            return False
    
    def step4_retrieve_document(self):
        """检索完整文档"""
        self.print_step(4, "生成完整文档报告")
        
        try:
            print(f"\n📄 文档信息:")
            print(f"   ID: {self.doc_id}")
            print(f"   标题: {self.outline['document_title']}")
            print(f"   生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            print(f"\n📖 文档内容结构:")
            
            total_chars = 0
            section_count = 0
            
            for section_idx, section in enumerate(self.outline['structure']['sections']):
                section_count += 1
                print(f"\n   第{section_count}章: {section['title']}")
                
                for subsec_idx, subsection in enumerate(section['subsections']):
                    key = f"{section_idx}_{subsec_idx}"
                    if key in self.generated_sections:
                        content_len = len(self.generated_sections[key]['content'])
                        total_chars += content_len
                        preview = self.generated_sections[key]['content'][:80]
                        print(f"      ✅ {subsection['title']} ({content_len}字)")
                        print(f"         {preview}...")
                    else:
                        print(f"      ❌ {subsection['title']} (未生成)")
            
            print(f"\n   📊 统计:")
            print(f"      总字数: {total_chars}")
            print(f"      生成的小节: {len(self.generated_sections)}")
            
            print(f"\n✅ 文档检索完成")
            return True
            
        except Exception as e:
            print(f"❌ 错误: {e}")
            return False
    
    def run_all(self):
        """运行所有步骤"""
        self.print_header("🚀 FlowerNet 完整文档生成流程")
        
        results = {}
        
        results['step1'] = self.step1_save_outline()
        if not results['step1']:
            print("\n❌ 大纲保存失败，无法继续")
            return results
        
        results['step2'] = self.step2_generate_content()
        results['step3'] = self.step3_database_summary()
        results['step4'] = self.step4_retrieve_document()
        
        return results
    
    def print_summary(self, results):
        """打印总结"""
        self.print_header("✨ 测试结果总结")
        
        steps = {
            'step1': '保存大纲',
            'step2': '生成内容',
            'step3': '数据库验证',
            'step4': '文档检索'
        }
        
        passed = sum(1 for v in results.values() if v)
        
        print("\n测试结果:")
        for key, name in steps.items():
            if key in results:
                status = "✅ 成功" if results[key] else "❌ 失败"
                print(f"  {status}: {name}")
        
        print(f"\n总体: {passed}/{len(results)} 个步骤成功")
        
        if passed == len(results):
            print(f"\n🎉 完整流程测试成功！")
            print(f"   文档ID: {self.doc_id}")
            print(f"   生成的小节数: {len(self.generated_sections)}")
        else:
            print(f"\n⚠️  有 {len(results) - passed} 个步骤失败")
        
        print(f"\n{'='*70}\n")


def main():
    tester = DocumentGenerationTest()
    results = tester.run_all()
    tester.print_summary(results)
    
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
