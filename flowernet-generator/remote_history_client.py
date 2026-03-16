import requests
from typing import Any, Dict, List, Optional


class RemoteHistoryManager:
    """通过 outliner 服务访问共享数据库。"""

    def __init__(self, base_url: str, timeout: int = 60):
        self.base_url = (base_url or "http://localhost:8003").rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self.session.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout)
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict) and body.get("success") is False:
            raise Exception(body.get("error") or body.get("detail") or f"RemoteHistoryManager request failed: {path}")
        return body

    def add_entry(
        self,
        document_id: str,
        section_id: str,
        subsection_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self._post(
            "/history/add",
            {
                "document_id": document_id,
                "section_id": section_id,
                "subsection_id": subsection_id,
                "content": content,
                "metadata": metadata or {},
            },
        )

    def get_history(self, document_id: str) -> List[Dict[str, Any]]:
        return self._post("/history/get", {"document_id": document_id}).get("history", [])

    def get_history_text(self, document_id: str, separator: str = "\n\n---\n\n") -> str:
        return self._post("/history/get-text", {"document_id": document_id}).get("history_text", "")

    def clear_history(self, document_id: str):
        self._post("/history/clear", {"document_id": document_id})

    def save_outline(
        self,
        document_id: str,
        outline_content: str,
        outline_type: str = "document",
        section_id: Optional[str] = None,
        subsection_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self._post(
            "/outline/save",
            {
                "document_id": document_id,
                "outline_content": outline_content,
                "outline_type": outline_type,
                "section_id": section_id,
                "subsection_id": subsection_id,
                "metadata": metadata or {},
            },
        )

    def get_outline(
        self,
        document_id: str,
        outline_type: str = "document",
        section_id: Optional[str] = None,
        subsection_id: Optional[str] = None,
    ) -> Optional[str]:
        return self._post(
            "/outline/get",
            {
                "document_id": document_id,
                "outline_type": outline_type,
                "section_id": section_id,
                "subsection_id": subsection_id,
            },
        ).get("outline")

    def create_subsection_tracking(
        self,
        document_id: str,
        section_id: str,
        subsection_id: str,
        outline: str,
    ):
        self._post(
            "/subsection-tracking/create",
            {
                "document_id": document_id,
                "section_id": section_id,
                "subsection_id": subsection_id,
                "outline": outline,
            },
        )

    def update_subsection_content(
        self,
        document_id: str,
        section_id: str,
        subsection_id: str,
        generated_content: Optional[str] = None,
        relevancy_index: Optional[float] = None,
        redundancy_index: Optional[float] = None,
        is_passed: Optional[bool] = None,
        iteration_count: Optional[int] = None,
        outline: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self._post(
            "/subsection-tracking/update",
            {
                "document_id": document_id,
                "section_id": section_id,
                "subsection_id": subsection_id,
                "generated_content": generated_content,
                "relevancy_index": relevancy_index,
                "redundancy_index": redundancy_index,
                "is_passed": is_passed,
                "iteration_count": iteration_count,
                "outline": outline,
                "metadata": metadata,
            },
        )

    def get_subsection_tracking(
        self,
        document_id: str,
        section_id: str,
        subsection_id: str,
    ) -> Optional[Dict[str, Any]]:
        return self._post(
            "/subsection-tracking/get",
            {
                "document_id": document_id,
                "section_id": section_id,
                "subsection_id": subsection_id,
            },
        ).get("tracking")

    def add_passed_history(
        self,
        document_id: str,
        section_id: str,
        subsection_id: str,
        content: str,
        order_index: int,
    ):
        self._post(
            "/passed-history/add",
            {
                "document_id": document_id,
                "section_id": section_id,
                "subsection_id": subsection_id,
                "content": content,
                "order_index": order_index,
            },
        )

    def get_passed_history(self, document_id: str) -> List[Dict[str, Any]]:
        return self._post("/passed-history/get", {"document_id": document_id}).get("history", [])

    def get_passed_history_text(self, document_id: str, separator: str = "\n\n") -> str:
        return self._post("/passed-history/get-text", {"document_id": document_id}).get("history_text", "")

    def clear_passed_history(self, document_id: str):
        self._post("/passed-history/clear", {"document_id": document_id})

    def add_progress_event(
        self,
        document_id: str,
        stage: str,
        message: str,
        section_id: Optional[str] = None,
        subsection_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self._post(
            "/progress/add",
            {
                "document_id": document_id,
                "stage": stage,
                "message": message,
                "section_id": section_id,
                "subsection_id": subsection_id,
                "metadata": metadata or {},
            },
        )

    def get_progress_events(self, document_id: str, after_id: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        return self._post(
            "/history/progress",
            {"document_id": document_id, "after_id": after_id, "limit": limit},
        ).get("events", [])
