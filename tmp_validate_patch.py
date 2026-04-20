import importlib.util
import pathlib
import requests

root = pathlib.Path('.').resolve()
mod_path = root / 'flowernet-generator' / 'rag_search.py'
spec = importlib.util.spec_from_file_location('rag_search_local', mod_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
engine = mod.RAGSearchEngine(max_results=5, timeout=10)
res = engine.search('机器人 医疗 手术 辅助')
print('rag_success', res.get('success'))
for i, it in enumerate(res.get('results', [])[:3], 1):
    print('rag_item', i, it.get('source'), it.get('domain_score'), it.get('semantic_score'), it.get('quality_score'))

s = requests.Session()
s.trust_env = False
payload = {
    'draft': '该技术已广泛应用（来源：示例条目，链接：https://en.wikipedia.org/wiki/9-1-1:_Nashville）',
    'outline': '机器人在医疗手术辅助中的关键技术与应用',
    'history': [],
    'rel_threshold': 0.1,
    'red_threshold': 1.0,
    'source_results': [
        {
            'title': '9-1-1: Nashville',
            'body': 'American procedural drama television series',
            'href': 'https://en.wikipedia.org/wiki/9-1-1:_Nashville',
        }
    ],
    'require_source_citations': True,
    'min_source_citations': 1,
}
r = s.post('http://127.0.0.1:8000/verify', json=payload, timeout=120)
obj = r.json()
print('verify_status', r.status_code)
print('verify_reason', (obj.get('source_check') or {}).get('reason'))
print('low_semantic_urls', (obj.get('source_check') or {}).get('low_semantic_urls'))
