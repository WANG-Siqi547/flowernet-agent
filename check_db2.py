#!/usr/bin/env python3
import sqlite3
import json

db_path = 'flowernet_history.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 先查看表结构
print('=== history 表结构 ===')
cursor.execute("PRAGMA table_info(history)")
for row in cursor.fetchall():
    print(f'  {row}')

# 查询web_20260305_194939的记录
print('\n=== 文档 web_20260305_194939 的生成情况 ===\n')
cursor.execute('''
    SELECT section_id, subsection_id, content, metadata
    FROM history
    WHERE document_id = 'web_20260305_194939'
''')

rows = cursor.fetchall()
if not rows:
    print('数据库中没有该文档的记录')
    print('\n可用的文档ID:')
    cursor.execute("SELECT DISTINCT document_id FROM history WHERE document_id LIKE 'web_%' ORDER BY document_id DESC LIMIT 5")
    for row in cursor.fetchall():
        print(f'  - {row[0]}')
else:
    for idx, row in enumerate(rows, 1):
        section = row[0]
        subsection = row[1]
        content = row[2] or ''
        try:
            metadata = json.loads(row[3]) if row[3] else {}
        except:
            metadata = {}
        
        print(f'[{idx}] {section}::{subsection}')
        print(f'    内容长度: {len(content)} 字符')
        print(f'    内容预览: {content[:100]}...' if content else '    内容: (空)')
        
        status = metadata.get('status', 'unknown')
        print(f'    状态: {status}')
        
        if 'verification' in metadata:
            ver = metadata['verification']
            rel = ver.get('relevancy_score', 'N/A')
            red = ver.get('redundancy_score', 'N/A')
            passed = ver.get('passed', False)
            print(f'    验证: 相关性={rel}, 冗余性={red}, 通过={passed}')
        print()

conn.close()
