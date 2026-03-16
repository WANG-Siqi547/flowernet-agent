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

        # 旧 history 表（保留兼容性）
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

        # 新表：大纲存储（支持整篇文章和每个 section/subsection 的大纲）
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS outlines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL,
                section_id TEXT,  -- NULL for document-level outline
                subsection_id TEXT,  -- NULL for section-level outline
                outline_content TEXT NOT NULL,
                outline_type TEXT,  -- 'document', 'section', 'subsection'
                created_at TEXT NOT NULL,
                metadata TEXT
            )
            """
        )

        # 新表：Subsection 内容来源追踪（记录每个 subsection 的大纲、生成和验证）
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS subsection_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL,
                section_id TEXT NOT NULL,
                subsection_id TEXT NOT NULL,
                outline TEXT NOT NULL,  -- subsection 的大纲
                generated_content TEXT,  -- 生成的内容
                is_passed BOOLEAN DEFAULT 0,  -- 是否通过验证
                relevancy_index REAL,  -- 相关性分数
                redundancy_index REAL,  -- 冗余度分数
                iteration_count INTEGER DEFAULT 0,  -- 迭代次数
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT
            )
            """
        )

        # 新表：历史链（用于记录已通过的 subsection 作为下一个的历史上下文）
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS passed_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL,
                section_id TEXT NOT NULL,
                subsection_id TEXT NOT NULL,
                content TEXT NOT NULL,
                order_index INTEGER,  -- subsection 的顺序
                created_at TEXT NOT NULL
            )
            """
        )

        # 新表：流程事件（用于前端展示生成细节）
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL,
                section_id TEXT,
                subsection_id TEXT,
                stage TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                metadata TEXT
            )
            """
        )

        # 创建索引
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_document_id
            ON history(document_id)
            """
        )
        
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_outlines_document
            ON outlines(document_id)
            """
        )
        
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tracking_document
            ON subsection_tracking(document_id, section_id, subsection_id)
            """
        )
        
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_passed_history
            ON passed_history(document_id)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_progress_document
            ON progress_events(document_id, id)
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
                SELECT document_id, section_id, subsection_id, content, timestamp, metadata
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
                    "document_id": row[0],
                    "section_id": row[1],
                    "subsection_id": row[2],
                    "content": row[3],
                    "timestamp": row[4],
                    "metadata": json.loads(row[5]) if row[5] else {},
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

        if not history:
            return {
                "record_count": 0,
                "total_characters": 0,
                "avg_relevancy_index": 0,
                "avg_redundancy_index": 0,
                "sections": [],
                "subsections": [],
            }

        total_chars = sum(len(entry["content"]) for entry in history)
        sections = list(set(entry["section_id"] for entry in history))
        subsections = list(set(entry["subsection_id"] for entry in history))
        
        # 计算平均相关性和冗余度
        relevancy_values = [
            entry.get("metadata", {}).get("relevancy_index", 0) 
            for entry in history
        ]
        redundancy_values = [
            entry.get("metadata", {}).get("redundancy_index", 0) 
            for entry in history
        ]
        
        avg_relevancy = sum(relevancy_values) / len(relevancy_values) if relevancy_values else 0
        avg_redundancy = sum(redundancy_values) / len(redundancy_values) if redundancy_values else 0

        return {
            "record_count": len(history),
            "total_characters": total_chars,
            "avg_relevancy_index": avg_relevancy,
            "avg_redundancy_index": avg_redundancy,
            "sections": sections,
            "subsections": subsections,
        }
    # ============ 新增方法：大纲管理 ============

    def save_outline(
        self,
        document_id: str,
        outline_content: str,
        outline_type: str = "document",
        section_id: Optional[str] = None,
        subsection_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        保存大纲（支持整篇文章、section 级别、subsection 级别）
        
        Args:
            document_id: 文档ID
            outline_content: 大纲内容
            outline_type: 大纲类型 ('document', 'section', 'subsection')
            section_id: section ID（仅 section/subsection 级别需要）
            subsection_id: subsection ID（仅 subsection 级别需要）
            metadata: 额外元数据
        """
        timestamp = datetime.now().isoformat()
        
        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute(
                """
                INSERT INTO outlines (document_id, section_id, subsection_id, outline_content, outline_type, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    section_id,
                    subsection_id,
                    outline_content,
                    outline_type,
                    timestamp,
                    json.dumps(metadata or {}),
                ),
            )
            
            conn.commit()
            conn.close()

    def get_outline(
        self,
        document_id: str,
        outline_type: str = "document",
        section_id: Optional[str] = None,
        subsection_id: Optional[str] = None,
    ) -> Optional[str]:
        """获取特定类型的大纲"""
        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if outline_type == "document":
                cursor.execute(
                    "SELECT outline_content FROM outlines WHERE document_id = ? AND outline_type = 'document' ORDER BY created_at DESC LIMIT 1",
                    (document_id,),
                )
            elif outline_type == "section":
                cursor.execute(
                    "SELECT outline_content FROM outlines WHERE document_id = ? AND section_id = ? AND outline_type = 'section' ORDER BY created_at DESC LIMIT 1",
                    (document_id, section_id),
                )
            elif outline_type == "subsection":
                cursor.execute(
                    "SELECT outline_content FROM outlines WHERE document_id = ? AND section_id = ? AND subsection_id = ? AND outline_type = 'subsection' ORDER BY created_at DESC LIMIT 1",
                    (document_id, section_id, subsection_id),
                )
            
            row = cursor.fetchone()
            conn.close()
            
            return row[0] if row else None
        
        return None

    # ============ 新增方法：Subsection 追踪 ============

    def create_subsection_tracking(
        self,
        document_id: str,
        section_id: str,
        subsection_id: str,
        outline: str,
    ):
        """为新的 subsection 创建或重置追踪记录"""
        timestamp = datetime.now().isoformat()
        
        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute(
                """
                DELETE FROM subsection_tracking
                WHERE document_id = ? AND section_id = ? AND subsection_id = ?
                """,
                (document_id, section_id, subsection_id),
            )
            cursor.execute(
                """
                INSERT INTO subsection_tracking (document_id, section_id, subsection_id, outline, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (document_id, section_id, subsection_id, outline, timestamp, timestamp),
            )
            
            conn.commit()
            conn.close()

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
        """更新 subsection 的大纲、生成内容和验证结果"""
        timestamp = datetime.now().isoformat()
        
        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            fields = []
            values = []

            if outline is not None:
                fields.append("outline = ?")
                values.append(outline)
            if generated_content is not None:
                fields.append("generated_content = ?")
                values.append(generated_content)
            if relevancy_index is not None:
                fields.append("relevancy_index = ?")
                values.append(relevancy_index)
            if redundancy_index is not None:
                fields.append("redundancy_index = ?")
                values.append(redundancy_index)
            if is_passed is not None:
                fields.append("is_passed = ?")
                values.append(1 if is_passed else 0)
            if iteration_count is not None:
                fields.append("iteration_count = ?")
                values.append(iteration_count)
            if metadata is not None:
                fields.append("metadata = ?")
                values.append(json.dumps(metadata))

            fields.append("updated_at = ?")
            values.append(timestamp)
            values.extend([document_id, section_id, subsection_id])

            cursor.execute(
                f"""
                UPDATE subsection_tracking
                SET {', '.join(fields)}
                WHERE document_id = ? AND section_id = ? AND subsection_id = ?
                """,
                values,
            )
            
            conn.commit()
            conn.close()

    def get_subsection_tracking(
        self,
        document_id: str,
        section_id: str,
        subsection_id: str,
    ) -> Optional[Dict[str, Any]]:
        """获取 subsection 追踪信息"""
        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT id, outline, generated_content, is_passed, relevancy_index, 
                       redundancy_index, iteration_count, created_at, updated_at, metadata
                FROM subsection_tracking
                WHERE document_id = ? AND section_id = ? AND subsection_id = ?
                """,
                (document_id, section_id, subsection_id),
            )
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    "id": row[0],
                    "outline": row[1],
                    "generated_content": row[2],
                    "is_passed": bool(row[3]),
                    "relevancy_index": row[4],
                    "redundancy_index": row[5],
                    "iteration_count": row[6],
                    "created_at": row[7],
                    "updated_at": row[8],
                    "metadata": json.loads(row[9]) if row[9] else {},
                }
        
        return None

    # ============ 新增方法：历史链管理 ============

    def add_passed_history(
        self,
        document_id: str,
        section_id: str,
        subsection_id: str,
        content: str,
        order_index: int,
    ):
        """添加已通过的 subsection 到历史链中"""
        timestamp = datetime.now().isoformat()
        
        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute(
                """
                INSERT INTO passed_history (document_id, section_id, subsection_id, content, order_index, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (document_id, section_id, subsection_id, content, order_index, timestamp),
            )
            
            conn.commit()
            conn.close()

    def get_passed_history(self, document_id: str) -> List[Dict[str, Any]]:
        """获取某个文档的所有已通过的 subsection（有序）"""
        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT document_id, section_id, subsection_id, content, order_index, created_at
                FROM passed_history
                WHERE document_id = ?
                ORDER BY order_index ASC
                """,
                (document_id,),
            )
            
            rows = cursor.fetchall()
            conn.close()
            
            return [
                {
                    "document_id": row[0],
                    "section_id": row[1],
                    "subsection_id": row[2],
                    "content": row[3],
                    "order_index": row[4],
                    "created_at": row[5],
                }
                for row in rows
            ]
        
        return []

    def get_passed_history_text(self, document_id: str, separator: str = "\n\n") -> str:
        """获取已通过的所有 subsection 作为文本"""
        history = self.get_passed_history(document_id)
        return separator.join([entry["content"] for entry in history])

    def clear_passed_history(self, document_id: str):
        """清空某个文档的历史链"""
        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM passed_history WHERE document_id = ?", (document_id,))
            
            conn.commit()
            conn.close()
            print(f"✅ 已清空文档 {document_id} 的已通过历史链 (Database)")

    # ============ 新增方法：流程事件管理 ============

    def add_progress_event(
        self,
        document_id: str,
        stage: str,
        message: str,
        section_id: Optional[str] = None,
        subsection_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """记录一条流程事件，用于前端展示详细生成过程。"""
        timestamp = datetime.now().isoformat()

        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO progress_events (document_id, section_id, subsection_id, stage, message, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    section_id,
                    subsection_id,
                    stage,
                    message,
                    timestamp,
                    json.dumps(metadata or {}),
                ),
            )
            event_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return event_id

        return None

    def get_progress_events(
        self,
        document_id: str,
        after_id: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """获取某文档的流程事件，支持增量拉取。"""
        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, document_id, section_id, subsection_id, stage, message, timestamp, metadata
                FROM progress_events
                WHERE document_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (document_id, max(0, int(after_id)), max(1, int(limit))),
            )
            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    "id": row[0],
                    "document_id": row[1],
                    "section_id": row[2],
                    "subsection_id": row[3],
                    "stage": row[4],
                    "message": row[5],
                    "timestamp": row[6],
                    "metadata": json.loads(row[7]) if row[7] else {},
                }
                for row in rows
            ]

        return []

    def clear_progress_events(self, document_id: str):
        """清空某文档的流程事件。"""
        if self.use_database:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM progress_events WHERE document_id = ?", (document_id,))
            conn.commit()
            conn.close()