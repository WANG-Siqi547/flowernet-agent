"""
Enhanced Citation Quality Control Configuration
================================================

This module provides additional citation constraints and validation rules
for the Generator to prevent citation drift and cross-domain pollution.
"""

# Enhanced citation constraints for Generator prompts
CITATION_DRIFT_PREVENTION_PROMPT = """
【参考文献源选择与校验 - 解决"引证漂移"问题 (CRITICAL)】

核心原则：验证引用内容与小节主题的领域一致性，防止跨学科污染

❌ 禁止的行为：
   - 引用与主题无关的学科论文
   - 例子：主题="谈判策略" 但引用"物理学论文"或"激光-等离子体互作"
   - 例子：主题="商业管理" 但引用"超导材料研究"
   
✓ 必须执行：
   - 检查论文标题/摘要是否与小节内容语义相关
   - 进行领域对齐性检查（domain alignment check）
   - 若提供的来源列表中出现完全无关的源，主动过滤或不引用

✓ 优先级规则：
   - 商业/管理主题 → 商业期刊、经济学论文、管理学研究
   - 技术主题 → CS/工程论文、技术博客、官方文档
   - 心理学主题 → 心理学论文、行为研究、学术期刊
   - 其他主题 → 相应学科的认可出版物和学术资源

✓ 来源优先级（从高到低）：
   1. 学术期刊和同行评审的会议论文（arXiv、SSRN、IEEE、ACM、Nature等）
   2. 官方文档和权威机构出版物
   3. 高质量技术博客和社区（GitHub、Stack Overflow、Medium、Dev.to）
   4. 高质量社交媒体（知乎、Reddit、微博、X/Twitter）
   5. 商业新闻和行业报告

✓ 实施方式：
   - 只引用提供的参考资料列表中的来源
   - 不编造论文、虚构链接或不存在的引用
   - 若无法找到领域相关的源，主动降级而不是引用无关源
   - 在参考文献中保留足够的元数据（标题、作者、出版年份），便于来源验证

参考文献末尾保留"References"小节，按[1][2][3]顺序列出所有引用源。
"""

# Domain-keyword mapping for citation validation
DOMAIN_KEYWORD_MAP = {
    "business": {
        "keywords": ["谈判", "商业", "市场", "销售", "管理", "企业", "合同", "战略", "经济", "竞争", "投资", "品牌", "客户", "供应链"],
        "valid_sources": ["HBR", "McKinsey", "Harvard Business Review", "商业期刊", "经济学论文", "管理研究"],
    },
    "technology": {
        "keywords": [
            "编程", "算法", "数据", "计算", "软件", "网络", "系统", "代码", "云", "AI", "机器学习",
            "长上下文", "长文档", "上下文窗口", "位置编码", "注意力", "稀疏注意力", "Transformer",
            "大语言模型", "LLM", "LongBench", "RoPE", "ALiBi", "FlashAttention", "检索增强",
            "long context", "long document", "context window", "positional encoding", "attention",
            "large language model", "retrieval augmented generation",
        ],
        "valid_sources": ["arXiv", "ACM", "IEEE", "ACL", "NeurIPS", "OpenReview", "技术博客", "CS论文", "工程研究", "官方文档"],
    },
    "psychology": {
        "keywords": ["心理", "行为", "认知", "情感", "压力", "学习", "记忆", "大脑", "神经"],
        "valid_sources": ["心理学期刊", "行为研究", "神经科学论文", "学术研究"],
    },
    "education": {
        "keywords": [
            "大学", "学生", "新生", "学习", "学习习惯", "时间管理", "自我调节", "自主学习",
            "学业", "复习", "拖延", "专注", "课堂", "高校", "教育",
            "student", "students", "college", "university", "academic", "learning",
            "study habits", "time management", "self-regulated learning", "procrastination",
        ],
        "valid_sources": ["教育研究", "心理学期刊", "高等教育研究", "ERIC", "APA", "Springer", "Taylor & Francis", "SAGE"],
    },
    "science": {
        "keywords": ["物理", "化学", "生物", "实验", "科学", "理论", "研究"],
        "valid_sources": ["Nature", "Science", "学科期刊", "同行评审", "研究所"],
    },
    "medicine": {
        "keywords": ["医学", "临床", "疾病", "治疗", "诊断", "患者", "护理", "药物", "medical", "clinical", "patient", "disease", "treatment"],
        "valid_sources": ["PubMed", "NIH", "WHO", "Cochrane", "BMJ", "Lancet", "NEJM"],
    },
    "law": {
        "keywords": ["法律", "法规", "司法", "法院", "判例", "合同", "合规", "law", "legal", "regulation", "court", "contract"],
        "valid_sources": ["法律数据库", "法院", "政府法规", "Law Review", "SSRN"],
    },
    "finance": {
        "keywords": ["金融", "投资", "银行", "风险", "资产", "财务", "finance", "financial", "investment", "bank", "risk"],
        "valid_sources": ["IMF", "World Bank", "BIS", "NBER", "金融期刊"],
    },
    "environment": {
        "keywords": ["环境", "气候", "碳", "生态", "污染", "能源", "可持续", "climate", "carbon", "environment", "sustainability"],
        "valid_sources": ["IPCC", "UN", "OECD", "Nature", "Science", "环境期刊"],
    },
}

# Cross-domain drift detection keywords
CROSS_DOMAIN_RED_FLAGS = {
    "physics_only": ["量子", "粒子", "超导", "激光", "等离子体", "LaFeAsO", "原子", "光子"],
    "chemistry_only": ["分子", "化学反应", "元素", "催化", "氧化", "还原", "化学键"],
    "biology_only": ["基因", "蛋白质", "DNA", "细胞", "微生物", "进化", "生物学"],
    "engineering_only": ["施工", "工程", "建筑", "热电", "低碳建筑", "超危大", "矿业", "混凝土"],
    "cs_only": ["算法", "机器学习", "深度学习", "神经网络", "sequence-to-sequence", "spectral sequence"],
    "medicine_only": ["临床试验", "患者", "疾病", "治疗组", "clinical trial", "patient cohort"],
    "law_only": ["法院", "判例", "诉讼", "合同法", "court", "case law", "litigation"],
    "finance_only": ["股票", "银行资本", "投资组合", "financial risk", "portfolio", "banking"],
}

print("✅ Citation Drift Prevention Configuration Loaded")
