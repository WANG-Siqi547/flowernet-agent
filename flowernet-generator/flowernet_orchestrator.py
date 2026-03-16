"""
FlowerNet 编排器 - 从根目录导入正式实现，保持向后兼容。
Docker 环境中使用同目录下的 flowernet_orchestrator_impl.py 副本。
"""
import sys
import os

# 先尝试同目录副本（Docker 环境），再尝试项目根目录（本地开发）
_this_dir = os.path.dirname(os.path.abspath(__file__))
_impl_path = os.path.join(_this_dir, "flowernet_orchestrator_impl.py")

if os.path.exists(_impl_path):
    # Docker / standalone: import from local copy
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("_fo_impl", _impl_path)
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    DocumentGenerationOrchestrator = _mod.DocumentGenerationOrchestrator
else:
    # Local dev: add project root to path and import root module
    _project_root = os.path.dirname(_this_dir)
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)
    from flowernet_orchestrator import DocumentGenerationOrchestrator  # noqa: F401
