# FlowerNet 指标展示 API 文档

## 概述

FlowerNet Web 服务现在提供完整的指标定义和说明 API，允许前端获取并展示系统的所有质量检测指标，以及各指标的详细说明。

## 核心特性

✅ **全面的指标定义** - 16+ 个质量评估指标的完整描述  
✅ **分类组织** - 按功能分为 6 个类别  
✅ **竞争优势说明** - 6 个核心特性的详细阐述  
✅ **仪表板友好** - 专门设计的概览数据格式  
✅ **实时可用** - 无需额外的计算或数据库查询  

## API 端点列表

### 1. 获取所有指标定义
**端点:** `GET /api/metrics/all`

返回 FlowerNet 系统中所有的质量检测指标。

**示例请求:**
```bash
curl http://localhost:8010/api/metrics/all
```

**示例响应:**
```json
{
  "success": true,
  "metrics_count": 16,
  "metrics": {
    "relevancy_index": {
      "name": "相关性指数",
      "description": "度量生成内容与用户查询和文档大纲的相关程度",
      "category": "内容质量",
      "threshold": 0.75,
      "feature": "SBERT 语义相似度检测",
      "pass_criteria": "相关性 ≥ 0.75",
      "importance": "高",
      "feature_highlight": "多维质量保证"
    },
    ...更多指标
  }
}
```

### 2. 获取指标分类
**端点:** `GET /api/metrics/categories`

返回指标的分类结构。

**示例请求:**
```bash
curl http://localhost:8010/api/metrics/categories
```

**示例响应:**
```json
{
  "success": true,
  "categories_count": 6,
  "categories": {
    "内容质量": {
      "icon": "📝",
      "description": "评估内容的相关性、清晰度和完整性",
      "metrics": ["relevancy_index", "redundancy_index", "topic_alignment", "coverage_completeness"]
    },
    ...更多分类
  }
}
```

### 3. 获取指定分类的指标
**端点:** `GET /api/metrics/category/{category_name}`

返回指定分类下的所有指标详情。

**示例请求:**
```bash
curl http://localhost:8010/api/metrics/category/内容质量
```

**示例响应:**
```json
{
  "success": true,
  "category_name": "内容质量",
  "category_info": {
    "icon": "📝",
    "description": "评估内容的相关性、清晰度和完整性",
    "metrics": ["relevancy_index", "redundancy_index", "topic_alignment", "coverage_completeness"]
  },
  "metrics_count": 4,
  "metrics": {
    "relevancy_index": {...},
    "redundancy_index": {...},
    ...
  }
}
```

### 4. 获取单个指标详情
**端点:** `GET /api/metrics/metric/{metric_key}`

返回单个指标的完整定义。

**示例请求:**
```bash
curl http://localhost:8010/api/metrics/metric/relevancy_index
```

**示例响应:**
```json
{
  "success": true,
  "metric_key": "relevancy_index",
  "metric": {
    "name": "相关性指数",
    "description": "度量生成内容与用户查询和文档大纲的相关程度",
    "category": "内容质量",
    "threshold": 0.75,
    "feature": "SBERT 语义相似度检测",
    "pass_criteria": "相关性 ≥ 0.75",
    "importance": "高",
    "feature_highlight": "多维质量保证"
  }
}
```

### 5. 获取 FlowerNet 核心特性
**端点:** `GET /api/metrics/features`

返回系统的六大核心能力说明。

**示例请求:**
```bash
curl http://localhost:8010/api/metrics/features
```

**示例响应:**
```json
{
  "success": true,
  "features_count": 6,
  "features": {
    "multi_dimensional_quality": {
      "title": "多维质量保证",
      "description": "从 6 个维度全面评估文档质量",
      "advantage": "相比单一质量指标，提供了全面的质量评估框架"
    },
    ...更多特性
  }
}
```

### 6. 获取仪表板概览
**端点:** `GET /api/metrics/dashboard-summary`

返回适合在前端仪表板展示的完整指标体系概览。

**示例请求:**
```bash
curl http://localhost:8010/api/metrics/dashboard-summary
```

**示例响应:**
```json
{
  "success": true,
  "summary": {
    "total_metrics": 16,
    "total_categories": 6,
    "total_features": 6,
    "categories": {
      "内容质量": {
        "name": "内容质量",
        "icon": "📝",
        "description": "评估内容的相关性、清晰度和完整性",
        "metrics_count": 4
      },
      ...
    },
    "features": [...]
  },
  "quick_access": {
    "all_metrics_count": 16,
    "categories_list": ["内容质量", "逻辑证据", "结构表达", "引用质量", "文档质量", "生成效率"],
    "top_features": ["multi_dimensional_quality", "domain_aware_filtering", "redundancy_detection"]
  }
}
```

### 7. 获取指标对比分析
**端点:** `GET /api/metrics/comparison`

返回指标的对比分析数据。

**示例请求:**
```bash
curl http://localhost:8010/api/metrics/comparison
```

**示例响应:**
```json
{
  "success": true,
  "comparison": {
    "metrics_by_category": {
      "内容质量": [
        {
          "key": "relevancy_index",
          "name": "相关性指数",
          "threshold": 0.75,
          "feature": "SBERT 语义相似度检测"
        },
        ...
      ],
      ...
    }
  },
  "statistics": {
    "total_metrics": 16,
    "metrics_with_thresholds": 14,
    "categories_covered": ["内容质量", "逻辑证据", "结构表达", "引用质量", "文档质量", "生成效率"]
  }
}
```

### 8. 获取完整文档
**端点:** `GET /api/metrics/documentation`

返回完整的指标文档和使用指南。

**示例请求:**
```bash
curl http://localhost:8010/api/metrics/documentation
```

**示例响应:**
```json
{
  "success": true,
  "documentation": {
    "system_overview": {
      "title": "FlowerNet 质量检测体系",
      "description": "FlowerNet 采用多层次、多维度的质量检测体系...",
      "layers": [
        "第一层：内容相关性检测",
        "第二层：冗余度检测",
        ...
      ]
    },
    "metrics": {...所有指标...},
    "categories": {...所有分类...},
    "features": {...所有特性...},
    "usage_guide": {
      "understanding_thresholds": "...",
      "interpreting_scores": "...",
      "pass_criteria": "...",
      "improvement_tips": "..."
    }
  }
}
```

## 前端集成示例

### React 示例

```typescript
// 获取所有指标
async function fetchAllMetrics() {
  const response = await fetch('http://localhost:8010/api/metrics/all');
  const data = await response.json();
  
  if (data.success) {
    console.log(`获取了 ${data.metrics_count} 个指标`);
    console.log(data.metrics);
  }
}

// 获取仪表板数据
async function fetchDashboardData() {
  const response = await fetch('http://localhost:8010/api/metrics/dashboard-summary');
  const data = await response.json();
  
  if (data.success) {
    // 展示指标统计
    console.log(`总指标数: ${data.summary.total_metrics}`);
    console.log(`分类数: ${data.summary.total_categories}`);
    console.log(`核心特性数: ${data.summary.total_features}`);
    
    // 遍历分类
    Object.entries(data.summary.categories).forEach(([name, info]) => {
      console.log(`${name}: ${info.metrics_count} 个指标`);
    });
  }
}

// 获取特定分类
async function fetchCategoryMetrics(categoryName: string) {
  const response = await fetch(`http://localhost:8010/api/metrics/category/${encodeURIComponent(categoryName)}`);
  const data = await response.json();
  
  if (data.success) {
    console.log(`分类 "${categoryName}" 的指标:`);
    Object.entries(data.metrics).forEach(([key, metric]) => {
      console.log(`  - ${metric.name}: ${metric.description}`);
    });
  }
}
```

### Vue 3 示例

```vue
<template>
  <div class="metrics-dashboard">
    <h1>FlowerNet 质量检测指标</h1>
    
    <!-- 统计卡片 -->
    <div class="stats">
      <div class="stat-card">
        <div class="stat-value">{{ summary.total_metrics }}</div>
        <div class="stat-label">质量指标</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ summary.total_categories }}</div>
        <div class="stat-label">指标分类</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ summary.total_features }}</div>
        <div class="stat-label">核心特性</div>
      </div>
    </div>
    
    <!-- 分类标签页 -->
    <div class="tabs">
      <button 
        v-for="(cat, name) in summary.categories" 
        :key="name"
        @click="selectedCategory = name"
        :class="{ active: selectedCategory === name }"
      >
        {{ cat.icon }} {{ name }}
      </button>
    </div>
    
    <!-- 指标列表 -->
    <div class="metrics-list">
      <div 
        v-for="(metric, key) in categoryMetrics" 
        :key="key" 
        class="metric-card"
      >
        <h3>{{ metric.name }}</h3>
        <p>{{ metric.description }}</p>
        <div class="metric-details">
          <span class="threshold">阈值: {{ metric.threshold }}</span>
          <span class="importance" :class="metric.importance">{{ metric.importance }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';

const summary = ref<any>(null);
const allMetrics = ref<any>(null);
const selectedCategory = ref<string>('');

const categoryMetrics = computed(() => {
  if (!allMetrics.value || !summary.value) return {};
  
  const category = summary.value.categories[selectedCategory.value];
  if (!category) return {};
  
  return category.metrics.reduce((acc: any, metricKey: string) => {
    acc[metricKey] = allMetrics.value[metricKey];
    return acc;
  }, {});
});

onMounted(async () => {
  // 获取仪表板数据
  const dashResponse = await fetch('/api/metrics/dashboard-summary');
  const dashData = await dashResponse.json();
  summary.value = dashData.summary;
  selectedCategory.value = Object.keys(summary.value.categories)[0];
  
  // 获取所有指标
  const metricsResponse = await fetch('/api/metrics/all');
  const metricsData = await metricsResponse.json();
  allMetrics.value = metricsData.metrics;
});
</script>

<style scoped>
.metrics-dashboard {
  padding: 20px;
}

.stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
  margin-bottom: 30px;
}

.stat-card {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  padding: 20px;
  border-radius: 8px;
  text-align: center;
}

.stat-value {
  font-size: 32px;
  font-weight: bold;
}

.stat-label {
  font-size: 14px;
  opacity: 0.9;
}

.tabs {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
  border-bottom: 1px solid #eee;
}

.tabs button {
  padding: 10px 15px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: #666;
  border-bottom: 2px solid transparent;
}

.tabs button.active {
  color: #667eea;
  border-bottom-color: #667eea;
}

.metrics-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 20px;
}

.metric-card {
  background: #f8f9fa;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 15px;
}

.metric-card h3 {
  margin-top: 0;
  color: #333;
}

.metric-details {
  display: flex;
  gap: 10px;
  margin-top: 10px;
  font-size: 12px;
}

.threshold {
  background: #e3f2fd;
  padding: 4px 8px;
  border-radius: 4px;
  color: #1976d2;
}

.importance {
  padding: 4px 8px;
  border-radius: 4px;
}

.importance.high {
  background: #ffebee;
  color: #c62828;
}

.importance.medium {
  background: #fff3e0;
  color: #e65100;
}

.importance.low {
  background: #f1f8e9;
  color: #558b2f;
}
</style>
```

## API 认证

如果启用了 API 认证（`API_AUTH_ENABLED=true`），请在请求头中添加认证信息：

```bash
# 使用 API Key
curl -H "X-API-Key: your-api-key" http://localhost:8010/api/metrics/all

# 使用 Bearer Token
curl -H "Authorization: Bearer your-bearer-token" http://localhost:8010/api/metrics/all
```

## 错误处理

所有端点都遵循标准的 HTTP 状态码：

- **200 OK** - 请求成功
- **404 Not Found** - 指定的指标或分类不存在
- **401 Unauthorized** - 认证失败（如果启用了认证）
- **503 Service Unavailable** - Metrics Definition 模块不可用

**错误响应示例:**
```json
{
  "detail": "Metric 'invalid_metric_key' not found"
}
```

## 性能考虑

- 所有指标数据都缓存在内存中，无需数据库查询
- 建议在应用启动时或定期刷新指标数据
- 不需要为频繁的指标查询担心性能问题

## 后续工作

- 添加实时质量评分的查询端点（按 document_id）
- 实现指标的历史趋势跟踪
- 添加指标的国际化支持
- 创建可视化仪表板的开源前端组件库

## 支持与反馈

如有问题或建议，请联系开发团队。
