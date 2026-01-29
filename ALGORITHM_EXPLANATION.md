# FlowerNet 控制层算法详解

## 系统整体流程

```
大纲 (Outline)
    ↓
[构建 Prompt] ← 调用 4 个算法组件
    ↓
[LLM 生成] ← generator() 返回 draft
    ↓
[验证层检查] ← POST 请求到 verifier
    ↓
判定是否通过 → 通过：存入历史 | 不通过：根据反馈循环
```

---

## 详细算法映射

### 📌 **第一层：Prompt 构建阶段** (`build_prompt` 方法)

#### 1️⃣ **Entity Recall** (提高相关性)

**目的：** 强制 LLM 在生成内容时涵盖大纲中的所有关键实体和概念

**代码执行流程：**
```python
entity_instr = FlowerNetAlgos.entity_recall(outline)
```

**实现细节：**
```python
@staticmethod
def entity_recall(outline):
    doc = nlp(outline)
    
    # Step 1: 使用 spaCy NER 提取命名实体 (Named Entity Recognition)
    entities = [ent.text for ent in doc.ents]
    # 示例：outline = "Discuss the impact of AI on healthcare"
    # 提取结果：entities = ["AI", "healthcare"]
    
    # Step 2: 提取名词短语 (Noun Chunks)
    noun_chunks = [chunk.text for chunk in doc.noun_chunks]
    # 提取结果：noun_chunks = ["the impact", "AI", "healthcare"]
    
    # Step 3: 去重并合并
    all_terms = list(set(entities + noun_chunks))
    # 最终列表：["AI", "healthcare", "the impact"]
    
    # Step 4: 生成强制指令
    return f"你必须在段落中包含以下所有术语，确保事实相关性：{', '.join(all_terms)}"
```

**输入 → 输出示例：**
- **输入：** "Discuss the impact of AI on modern healthcare and medical diagnosis"
- **提取的实体：** `["AI", "healthcare", "medical diagnosis"]`
- **生成的指令：** `"你必须在段落中包含以下所有术语，确保事实相关性：AI, healthcare, medical diagnosis, impact"`
- **作用：** LLM 会在生成时意识到必须提到这些关键词，确保相关性

---

#### 2️⃣ **LayRED** (提高相关性)

**目的：** 提取大纲的逻辑结构（主-谓-宾），强制 LLM 遵循相同的逻辑链条

**代码执行流程：**
```python
logic_instr = FlowerNetAlgos.layred_structure(outline)
```

**实现细节：**
```python
@staticmethod
def layred_structure(outline):
    doc = nlp(outline)
    relations = []
    
    # Step 1: 遍历所有动词（关键谓语）
    for token in doc:
        if token.pos_ == "VERB":
            # Step 2: 找出主语 (依存关系)
            subj = [w.text for w in token.lefts if w.dep_ in ("nsubj", "nsubjpass")]
            
            # Step 3: 找出宾语
            obj = [w.text for w in token.rights if w.dep_ in ("dobj", "pobj")]
            
            # Step 4: 构建三元组
            if subj and obj:
                relations.append(f"{subj[0]} -> {token.text} -> {obj[0]}")
    
    return f"请遵循以下层级逻辑结构展开，严禁偏离：{'; '.join(relations)}"
```

**输入 → 输出示例：**
- **输入：** "AI revolutionizes healthcare by improving diagnosis"
- **依存树分析：**
  ```
  AI (nsubj) → revolutionizes (VERB) → healthcare (dobj)
  AI (nsubj) → improving (VERB) → diagnosis (dobj)
  ```
- **生成的指令：** `"请遵循以下层级逻辑结构展开，严禁偏离：AI -> revolutionizes -> healthcare; AI -> improving -> diagnosis"`
- **作用：** 确保生成的段落保留原大纲的逻辑关系，不偏离主题

---

#### 3️⃣ **Anti-Hallucination** (基础约束)

**目的：** 减少 LLM 的幻觉内容

**代码：**
```python
hallucination_instr = FlowerNetAlgos.anti_hallucination()
# 输出："请仅依据提供的大纲和背景信息生成内容。如果信息不足，请保持客观，严禁捏造事实或产生幻觉。"
```

---

### 📌 **第二层：上下文模板生成** 

#### 4️⃣ **PacSum** (降低冗余)

**目的：** 从历史内容中提取最相关、最中心的部分，作为"背景上下文"来引导 LLM 避免重复

**代码执行流程：**
```python
context = FlowerNetAlgos.pacsum_template(self.history)
```

**实现细节：**
```python
@staticmethod
def pacsum_template(history, top_k=3):
    if not history or len(history) <= top_k:
        return " ".join(history)
    
    # 核心逻辑：Position-Augmented 中心度
    # 假设越靠后的内容越重要（最近生成的通常最相关）
    selected = history[-top_k:]
    return " ".join(selected)
```

**执行场景示例：**

假设已生成了 5 个段落的历史：
```
history = [
    "段落1: 历史背景...",
    "段落2: 基础概念...",
    "段落3: 技术发展...",
    "段落4: 应用场景...",
    "段落5: 现状分析..."
]
```

**PacSum 处理：**
- 从 5 个段落中选择最后 3 个（top_k=3）
- 生成：`context = "段落3: 技术发展... 段落4: 应用场景... 段落5: 现状分析..."`
- **为什么？** 最近生成的段落代表当前的话题焦点，用作背景能减少与之重复的可能

---

### 📌 **第三层：循环修正阶段**

#### 5️⃣ **SemDedup** (降低冗余 - 修正模式)

**目的：** 当生成失败（冗余度过高）时，提取失败草稿中的语义点，作为"负面约束"告诉 LLM 不要重复这些内容

**代码执行流程：**
```python
if res_data["redundancy_index"] > 0.6:
    current_prompt = self.build_prompt(outline, draft, "fix_redundancy")
    # 在 build_prompt 中：
    dedup_instr = FlowerNetAlgos.sem_dedup(failed_draft, self.history)
```

**实现细节：**
```python
@staticmethod
def sem_dedup(failed_draft, history):
    if not history: 
        return ""
    
    doc = nlp(failed_draft)
    
    # Step 1: 提取失败草稿中长度合理的句子（通常是关键表述）
    redundant_candidates = [sent.text for sent in doc.sents if len(sent.text) > 10]
    
    # Step 2: 仅保留前 2 个最典型的冗余表述
    return f"严禁重复以下语义点或内容：{'; '.join(redundant_candidates[:2])}"
```

**执行场景示例：**

**第一轮失败：**
```
outline: "Discuss AI impact on healthcare"
draft: "AI has revolutionized modern healthcare... AI enables early detection of diseases... 
        AI optimizes healthcare delivery..."
verification: redundancy_index = 0.75 > 0.6 ❌
```

**第二轮修正：**
- **提取的冗余句子：**
  - "AI has revolutionized modern healthcare by introducing unprecedented efficiency"
  - "AI enables early detection of diseases like cancer and cardiovascular disorders"
  
- **修正后的 Prompt 包含：**
  ```
  严禁重复以下语义点或内容：
  - AI has revolutionized modern healthcare by introducing unprecedented efficiency
  - AI enables early detection of diseases like cancer and cardiovascular disorders
  ```

- **LLM 在第二次生成时会避免这些表述，换个角度描述**

---

## 完整工作流示例

### 📋 完整执行序列

```
输入: outline = "Discuss the impact of AI on modern healthcare and medical diagnosis"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【第 1 次尝试】

1️⃣ build_prompt() 执行：
   ├─ entity_recall()
   │  └─ 提取: ["AI", "healthcare", "medical diagnosis", "impact"]
   │     指令: "必须包含: AI, healthcare, medical diagnosis, impact"
   │
   ├─ layred_structure()
   │  └─ 提取依存关系: "AI -> impact -> healthcare"
   │     指令: "遵循逻辑: AI 主动产生影响 → 作用在 healthcare 上"
   │
   ├─ anti_hallucination()
   │  └─ 指令: "仅依据大纲生成，严禁捏造"
   │
   └─ pacsum_template(history=[])
      └─ 由于历史为空，context = ""

2️⃣ generator(prompt) 调用 LLM
   输入包含强制约束:
   - Entity: 必须提到 AI, healthcare, diagnosis, impact
   - Logic: 必须遵循 "AI -> impact -> healthcare" 的逻辑
   - Anti-hallucination: 仅依据信息
   
   生成 draft:
   "AI has revolutionized healthcare systems by enabling faster and more accurate
    medical diagnosis. The impact of AI on diagnostic accuracy has reached 95%..."

3️⃣ 验证层检查：
   POST /verify {
     "draft": "...",
     "outline": "...",
     "history": []
   }
   
   返回: {
     "is_passed": False,
     "relevancy_index": 0.82,  ✅ (超过 0.4 阈值)
     "redundancy_index": 0.65,  ❌ (超过 0.6 阈值)
     "feedback": "Content is redundant with previous sections..."
   }

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【第 2 次尝试 - 修正冗余】

1️⃣ 检测失败原因:
   if res_data["redundancy_index"] > 0.6:
       scenario = "fix_redundancy"

2️⃣ build_prompt() 再次执行（带修正参数）：
   ├─ Entity Recall: 同上 ✓
   ├─ LayRED: 同上 ✓
   ├─ Anti-hallucination: 同上 ✓
   ├─ PacSum: history = [draft1], 提取最后 3 个 → 返回 draft1
   │
   └─ SemDedup (新增!)
      ├─ 从 failed_draft 中提取冗余句:
      │  - "AI has revolutionized healthcare systems"
      │  - "The impact of AI on diagnostic accuracy"
      │
      └─ 指令: "严禁重复：
              1. AI has revolutionized healthcare systems
              2. The impact of AI on diagnostic accuracy"

3️⃣ 修正后的 Prompt:
   """
   任务：根据大纲编写内容。
   大纲：Discuss the impact of AI on modern healthcare and medical diagnosis
   背景上下文：[之前的草稿内容]
   
   指令约束：
   - Entity: 必须包含 AI, healthcare, medical diagnosis, impact
   - Logic: 遵循 AI -> impact -> healthcare
   - Anti-hallucination: 仅依据提供信息
   
   🚫 修正要求：
   - 严禁重复以下语义点或内容：
     1. AI has revolutionized healthcare systems
     2. The impact of AI on diagnostic accuracy
   - 请换一个角度描述，不要与前文重复。
   """

4️⃣ generator() 生成新 draft:
   "AI technology enables rapid medical diagnosis through pattern recognition...
    Healthcare providers leverage AI algorithms to detect early-stage diseases...
    The diagnostic workflow has been accelerated by machine learning models..."
   
   ✅ 避免了之前的表述方式，改用"enable"、"leverage"等不同动词

5️⃣ 验证层再次检查：
   返回: {
     "is_passed": True,  ✅
     "relevancy_index": 0.81,
     "redundancy_index": 0.42,
   }

6️⃣ 成功!
   self.history.append(draft2)
   return draft2, True

```

---

## 🔑 关键参数汇总

| 算法 | 位置 | 输入 | 输出 | 作用 |
|-----|------|------|------|------|
| **Entity Recall** | Step 1 | outline | 实体列表 + 强制指令 | 确保关键词必须出现 |
| **LayRED** | Step 1 | outline | 依存关系 + 逻辑指令 | 保持逻辑结构一致 |
| **Anti-Hallucination** | Step 1 | 无 | 通用约束指令 | 减少幻觉 |
| **PacSum** | Step 2 | history | 精选的最近 k 段内容 | 提供相关背景，避免重复 |
| **SemDedup** | Step 3 (修正) | failed_draft | 冗余句子 + 禁止指令 | 具体禁止已表达过的语义 |

---

## 🎯 算法的作用原理总结

### 提高 Relevancy 的机制：
1. **Entity Recall**: 硬约束，LLM 必须提到这些词 → 词汇覆盖 ✓
2. **LayRED**: 硬约束，LLM 遵循逻辑链 → 逻辑相关性 ✓
3. **Anti-Hallucination**: 软约束，避免无关信息 ✓

### 降低 Redundancy 的机制：
1. **PacSum**: 软约束，通过提供最相关的历史避免"打转" → 知道前面说了什么 ✓
2. **SemDedup**: 硬约束，具体禁止冗余表述 → 强制换个角度 ✓

### 反馈循环：
- 第 1 次失败 (冗余) → 加入 SemDedup 的负面约束 → 第 2 次强制改写
- 第 1 次失败 (相关性) → 保持 Entity/LayRED 的正面约束 → 第 2 次强化关键词

---
