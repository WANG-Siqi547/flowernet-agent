"""
Shared History store for FlowerNet services.
Provides HistoryManager for memory/SQLite storage.
"""

import json
import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional


class HistoryManager:
    """
    History 管理器
    - 支持内存模式（小规模数据）
    - 支持 SQLite 数据库模式（大规模数据）
    """

    def __init__(self, use_database: bool = False, db_path: str = "flowernet_history.db"):
        self.use_database = use_database
        self.db_path = db_path
        self.memory_history: List[Dict[str, Any]] = []

        if self.use_database:
            self._init_database()
            print(f"✅ History Manager: Database mode ({db_path})")
        else:
            print("✅ History Manager: Memory mode")

    def _init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL,
                section_id TEXT NOT NULL,
                subsection_id TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                metadata TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_document_id
            ON history(document_id)
            """
        )

        conn.commit()
        conn.close()

    def add_entry(
        self,
        document_id: str,
        section_id: str,
        subsection_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        timestamp = datetime.now().isoformat()

        entry = {
            "document_id": document_id,
            "section_id": section_id,
            "subsection_id": subsection_id,
            "content": content,
            "timestamp": timestamp,
            "metadata": metadata or {},
        }

        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO history (document_id, section_id, subsection_id, content, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    section_id,
                    subsection_id,
                    content,
                    timestamp,
                    json.dumps(metadata or {}),
                ),
            )

            conn.commit()
            conn.close()
        else:
            self.memory_history.append(entry)

    def get_history(self, document_id: str) -> List[Dict[str, Any]]:
        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT section_id, subsection_id, content, timestamp, metadata
                FROM history
                WHERE document_id = ?
                ORDER BY id ASC
                """,
                (document_id,),
            )

            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    "section_id": row[0],
                    "subsection_id": row[1],
                    "content": row[2],
                    "timestamp": row[3],
                    "metadata": json.loads(row[4]) if row[4] else {},
                }
                for row in rows
            ]

        return [
            entry for entry in self.memory_history if entry["document_id"] == document_id
        ]

    def get_history_text(self, document_id: str, separator: str = "\n\n---\n\n") -> str:
        history = self.get_history(document_id)
        return separator.join([entry["content"] for entry in history])

    def clear_history(self, document_id: str):
        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("DELETE FROM history WHERE document_id = ?", (document_id,))

            conn.commit()
            conn.close()
            print(f"✅ 已清空文档 {document_id} 的 history (Database)")
        else:
            self.memory_history = [
                entry for entry in self.memory_history if entry["document_id"] != document_id
            ]
            print(f"✅ 已清空文档 {document_id} 的 history (Memory)")

    def get_statistics(self, document_id: str) -> Dict[str, Any]:
        history = self.get_history(document_id)

        total_chars = sum(len(entry["content"]) for entry in history)
        sections = list(set(entry["section_id"] for entry in history))

        return {
            "total_entries": len(history),
            "total_characters": total_chars,
            "sections": sections,
        }
