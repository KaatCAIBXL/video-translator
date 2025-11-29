"""
Service for managing messages sent to admin.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from .config import settings

logger = logging.getLogger(__name__)


class MessageService:
    """Service for managing admin messages."""
    
    def __init__(self):
        self.messages_dir = settings.PROCESSED_DIR.parent / "messages"
        self.messages_dir.mkdir(parents=True, exist_ok=True)
        self.messages_file = self.messages_dir / "messages.json"
    
    def _load_messages(self) -> List[dict]:
        """Load all messages from file."""
        if not self.messages_file.exists():
            return []
        
        try:
            content = self.messages_file.read_text(encoding="utf-8")
            return json.loads(content)
        except Exception as e:
            logger.exception(f"Error loading messages: {e}")
            return []
    
    def _save_messages(self, messages: List[dict]):
        """Save messages to file."""
        try:
            self.messages_file.write_text(
                json.dumps(messages, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            logger.exception(f"Error saving messages: {e}")
    
    def create_message(self, sender_role: str, message: str, sender_name: Optional[str] = None) -> dict:
        """Create a new message to admin."""
        message_data = {
            "id": str(uuid4()),
            "sender_role": sender_role,
            "sender_name": sender_name or sender_role,
            "message": message,
            "created_at": datetime.now().isoformat(),
            "read": False
        }
        
        messages = self._load_messages()
        messages.append(message_data)
        self._save_messages(messages)
        
        return message_data
    
    def get_messages(self, unread_only: bool = False) -> List[dict]:
        """Get all messages, optionally filtered to unread only."""
        messages = self._load_messages()
        
        # Sort by created_at descending (newest first)
        messages.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        
        if unread_only:
            messages = [m for m in messages if not m.get("read", False)]
        
        return messages
    
    def mark_as_read(self, message_id: str) -> bool:
        """Mark a message as read."""
        messages = self._load_messages()
        
        for message in messages:
            if message.get("id") == message_id:
                message["read"] = True
                self._save_messages(messages)
                return True
        
        return False
    
    def delete_message(self, message_id: str) -> bool:
        """Delete a message."""
        messages = self._load_messages()
        original_count = len(messages)
        
        messages = [m for m in messages if m.get("id") != message_id]
        
        if len(messages) < original_count:
            self._save_messages(messages)
            return True
        
        return False


# Global instance
message_service = MessageService()



