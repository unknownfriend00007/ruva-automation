import os
import asyncio
import logging
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import (
    MessageMediaPhoto, 
    MessageMediaDocument,
    TypeInputMedia,
    InputMediaPhoto,
    InputMediaDocument
)
import requests

load_dotenv()

# ==================== CONFIG ====================
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_STRING_SESSION = os.getenv("BOT_STRING_SESSION", "").strip()
FLOWISE_URL = os.getenv("FLOWISE_URL", "")
TARGET_CHANNEL = os.getenv("TARGET_CHANNEL", "")
SOURCE_CHANNELS = [ch.strip() for ch in os.getenv("SOURCE_CHANNELS", "").split(",") if ch.strip()]

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ==================== BOT CLIENT ====================
if BOT_STRING_SESSION:
    bot_client = TelegramClient(StringSession(BOT_STRING_SESSION), API_ID, API_HASH)
else:
    raise ValueError("❌ BOT_STRING_SESSION is required!")

# ==================== AI REWRITING ====================
def call_ai_bot(text: str) -> str:
    """Rewrite text using Flowise AI"""
    if not text or not text.strip():
        return None
    
    payload = {
        "question": text,
        "streaming": False,
        "overrideConfig": {}
    }
    
    try:
        response = requests.post(FLOWISE_URL, json=payload, timeout=45)
        response.raise_for_status()
        data = response.json()
        result = data.get("text") or data.get("data") or ""
        return result.strip() if result else None
    except Exception as e:
        logger.error(f"❌ AI rewriting failed: {e}")
        return None

# ==================== FORWARD LOGIC ====================
async def forward_message(event):
    """Forward message preserving media grouping"""
    try:
        message = event.message
        
        # Get text/caption
        text = message.text or ""
        if message.media and hasattr(message.media, 'caption'):
            text = message.media.caption or ""
        
        text = text.strip()
        
        # Rewrite text with AI
        rewritten_text = None
        if text:
            logger.info(f"📝 Rewriting: {text[:50]}...")
            rewritten_text = call_ai_bot(text)
            # If AI fails, use original
            if not rewritten_text:
                rewritten_text = text
        
        # Handle grouped messages (albums)
        if message.grouped_id:
            logger.info(f"📦 Album detected (grouped_id: {message.grouped_id})")
            # Get all messages in group
            group_messages = await bot_client.get_messages(
                event.chat_id,
                ids=message.id,
                limit=10
            )
            
            # This requires special handling - see Case 1 below
        
        # ================ CASE 1: GROUPED MEDIA (ALBUM) ================
        if message.grouped_id:
            logger.info(f"🖼️ Processing grouped media...")
            await handle_grouped_media(event, rewritten_text)
            return
        
        # ================ CASE 2: TEXT ONLY ================
        if text and not message.media:
            logger.info(f"📝 Text only")
            await bot_client.send_message(TARGET_CHANNEL, rewritten_text, parse_mode="md")
            logger.info(f"✅ Forwarded text")
            return
        
        # ================ CASE 3: PHOTO ================
        if message.media and isinstance(message.media, MessageMediaPhoto):
            logger.info(f"🖼️ Photo")
            try:
                await bot_client.send_file(
                    TARGET_CHANNEL,
                    message.media.photo,
                    caption=rewritten_text,
                    parse_mode="md" if rewritten_text else None
                )
                logger.info(f"✅ Forwarded photo")
            except Exception as e:
                logger.error(f"Failed to forward photo: {e}")
                if rewritten_text:
                    await bot_client.send_message(TARGET_CHANNEL, rewritten_text, parse_mode="md")
            return
        
        # ================ CASE 4: VIDEO ================
        if message.media and isinstance(message.media, MessageMediaDocument):
            document = message.media.document
            mime_type = document.mime_type if document else ""
            
            # Check if video
            if "video" in mime_type:
                logger.info(f"🎥 Video (mime: {mime_type})")
                try:
                    await bot_client.send_file(
                        TARGET_CHANNEL,
                        message.media.document,
                        caption=rewritten_text,
                        parse_mode="md" if rewritten_text else None
                    )
                    logger.info(f"✅ Forwarded video")
                except Exception as e:
                    logger.error(f"Failed to forward video: {e}")
                    if rewritten_text:
                        await bot_client.send_message(TARGET_CHANNEL, rewritten_text, parse_mode="md")
                return
            
            # Check if GIF/animated
            if "image/gif" in mime_type or "video/mp4" in mime_type:
                logger.info(f"🎬 GIF/Animation (mime: {mime_type})")
                try:
                    await bot_client.send_file(
                        TARGET_CHANNEL,
                        message.media.document,
                        caption=rewritten_text,
                        parse_mode="md" if rewritten_text else None
                    )
                    logger.info(f"✅ Forwarded GIF")
                except Exception as e:
                    logger.error(f"Failed to forward GIF: {e}")
                    if rewritten_text:
                        await bot_client.send_message(TARGET_CHANNEL, rewritten_text, parse_mode="md")
                return
            
            # Other documents - skip
            logger.info(f"⏭️ Skipped document type: {mime_type}")
            return
        
        # ================ CASE 5: OTHER MEDIA ================
        if message.media:
            logger.info(f"⏭️ Skipped unsupported media type")
            return
        
        # ================ CASE 6: EMPTY MESSAGE ================
        logger.info(f"⏭️ Empty message (no text/media)")
        
    except Exception as e:
        logger.exception(f"❌ Error in forward_message: {e}")

# ==================== GROUPED MEDIA HANDLER ====================
async def handle_grouped_media(event, rewritten_text):
    """Handle grouped media (albums) - forward as-is"""
    try:
        message = event.message
        chat_id = event.chat_id
        
        # Forward the media directly (preserves grouping)
        await bot_client.send_file(
            TARGET_CHANNEL,
            message.media,
            caption=rewritten_text if rewritten_text else None,
            parse_mode="md" if rewritten_text else None
        )
        logger.info(f"✅ Forwarded grouped media")
        
    except Exception as e:
        logger.error(f"❌ Failed to forward grouped media: {e}")
        if rewritten_text:
            await bot_client.send_message(TARGET_CHANNEL, rewritten_text, parse_mode="md")

# ==================== MAIN BOT ====================
async def main():
    """Main bot function"""
    await bot_client.start()
    
    logger.info("=" * 50)
    logger.info("🚀 CHANNEL FORWARDER BOT STARTED")
    logger.info("=" * 50)
    logger.info(f"📍 Monitoring: {SOURCE_CHANNELS}")
    logger.info(f"📤 Target Channel: {TARGET_CHANNEL}")
    logger.info(f"🤖 AI Rewriting: {FLOWISE_URL[:50]}...")
    logger.info("=" * 50)
    
    # Register event handler
    @bot_client.on(events.NewMessage(chats=SOURCE_CHANNELS))
    async def handler(event):
        await forward_message(event)
    
    logger.info("✅ Event handlers registered. Listening...")
    logger.info("=" * 50)
    
    try:
        await bot_client.run_until_disconnected()
    except KeyboardInterrupt:
        logger.info("⛔ Bot stopped by user")
    except Exception as e:
        logger.exception(f"❌ Bot error: {e}")
    finally:
        await bot_client.disconnect()
        logger.info("🔌 Disconnected")

# ==================== ENTRY POINT ====================
if __name__ == "__main__":
    try:
        bot_client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Shutdown signal received")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
