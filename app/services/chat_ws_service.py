from datetime import datetime, timezone
from typing import List, Optional
from bson import ObjectId


async def broadcast_new_message(db, conversation_id: str, message_doc: dict):
    """
    Broadcasts a newly sent message to all participants in the conversation.
    """
    from app.api.chat import ws_manager
    from app.services.chat_service import format_message

    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not conv_doc:
        return

    participant_uids = [str(p.get("user_id")) for p in conv_doc.get("participants", []) if p.get("user_id")]
    if not participant_uids:
        return

    formatted = format_message(message_doc).model_dump(mode="json")
    payload = {
        "type": "new_message",
        "conversation_id": conversation_id,
        "message": formatted
    }
    await ws_manager.broadcast_to_users(payload, participant_uids)


async def broadcast_message_edited(db, conversation_id: str, message_doc: dict):
    """
    Broadcasts an edited message to all participants in the conversation.
    """
    from app.api.chat import ws_manager
    from app.services.chat_service import format_message

    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not conv_doc:
        return

    participant_uids = [str(p.get("user_id")) for p in conv_doc.get("participants", []) if p.get("user_id")]
    if not participant_uids:
        return

    formatted = format_message(message_doc).model_dump(mode="json")
    payload = {
        "type": "message_edited",
        "conversation_id": conversation_id,
        "message": formatted
    }
    await ws_manager.broadcast_to_users(payload, participant_uids)


async def broadcast_message_deleted(db, conversation_id: str, message_id: str):
    """
    Broadcasts message deletion to all participants in the conversation.
    """
    from app.api.chat import ws_manager

    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not conv_doc:
        return

    participant_uids = [str(p.get("user_id")) for p in conv_doc.get("participants", []) if p.get("user_id")]
    if not participant_uids:
        return

    payload = {
        "type": "message_deleted",
        "conversation_id": conversation_id,
        "message_id": message_id
    }
    await ws_manager.broadcast_to_users(payload, participant_uids)


async def broadcast_messages_read(db, conversation_id: str, reader_user_id: str, read_at_iso: str):
    """
    Broadcasts read receipt notification to all participants in the conversation.
    """
    from app.api.chat import ws_manager

    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not conv_doc:
        return

    participant_uids = [str(p.get("user_id")) for p in conv_doc.get("participants", []) if p.get("user_id")]
    if not participant_uids:
        return

    payload = {
        "type": "messages_read",
        "conversation_id": conversation_id,
        "user_id": reader_user_id,
        "read_at": read_at_iso
    }
    await ws_manager.broadcast_to_users(payload, participant_uids)


async def broadcast_typing_status(db, conversation_id: str, sender_user_id: str, sender_name: str, is_typing: bool):
    """
    Broadcasts typing indicator to all participants except the sender.
    """
    from app.api.chat import ws_manager

    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not conv_doc:
        return

    # Broadcast to other participants only
    target_uids = [str(p.get("user_id")) for p in conv_doc.get("participants", []) if str(p.get("user_id")) != str(sender_user_id)]
    if not target_uids:
        return

    payload = {
        "type": "typing",
        "conversation_id": conversation_id,
        "user_id": sender_user_id,
        "name": sender_name,
        "is_typing": is_typing
    }
    await ws_manager.broadcast_to_users(payload, target_uids)
