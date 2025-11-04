import os
import asyncio
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    MessageMediaPhoto, 
    MessageMediaDocument
)
import requests
import json

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

# ==================== TRACKING ====================
SEEN_FILE = "/tmp/forwarded_messages.json"

def load_seen_messages():
    """Load previously forwarded message IDs"""
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, 'r') as f:
                data = json.load(f)
                return set(data.get('message_ids', []))
        except Exception as e:
            logger.error(f"Failed to load seen messages: {e}")
            return set()
    return set()

def save_seen_messages(seen):
    """Save forwarded message IDs"""
    try:
        with open(SEEN_FILE, 'w') as f:
            json.dump({'message_ids': list(seen)}, f)
    except Exception as e:
        logger.error(f"Failed to save seen messages: {e}")

SEEN_MESSAGES = load_seen_messages()

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
async def process_message(message, channel_name):
    """Process and forward a single message"""
    global SEEN_MESSAGES
    
    try:
        msg_key = f"{channel_name}_{message.id}"
        
        # Skip if already forwarded
        if msg_key in SEEN_MESSAGES:
            logger.info(f"⏭️ Already forwarded: {msg_key}")
            return False
        
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
            if not rewritten_text:
                rewritten_text = text
        
        # ================ CASE 1: TEXT ONLY ================
        if text and not message.media:
            logger.info(f"📝 Text only from {channel_name}")
            await bot_client.send_message(TARGET_CHANNEL, rewritten_text, parse_mode="md")
            logger.info(f"✅ Forwarded text")
            SEEN_MESSAGES.add(msg_key)
            return True
        
        # ================ CASE 2: PHOTO ================
        if message.media and isinstance(message.media, MessageMediaPhoto):
            logger.info(f"🖼️ Photo from {channel_name}")
            try:
                await bot_client.send_file(
                    TARGET_CHANNEL,
                    message.media.photo,
                    caption=rewritten_text,
                    parse_mode="md" if rewritten_text else None
                )
                logger.info(f"✅ Forwarded photo")
                SEEN_MESSAGES.add(msg_key)
                return True
            except Exception as e:
                logger.error(f"Failed to forward photo: {e}")
                if rewritten_text:
                    await bot_client.send_message(TARGET_CHANNEL, rewritten_text, parse_mode="md")
                    SEEN_MESSAGES.add(msg_key)
                    return True
                return False
        
        # ================ CASE 3: VIDEO ================
        if message.media and isinstance(message.media, MessageMediaDocument):
            document = message.media.document
            mime_type = document.mime_type if document else ""
            
            # Check if video
            if "video" in mime_type:
                logger.info(f"🎥 Video from {channel_name} (mime: {mime_type})")
                try:
                    await bot_client.send_file(
                        TARGET_CHANNEL,
                        message.media.document,
                        caption=rewritten_text,
                        parse_mode="md" if rewritten_text else None
                    )
                    logger.info(f"✅ Forwarded video")
                    SEEN_MESSAGES.add(msg_key)
                    return True
                except Exception as e:
                    logger.error(f"Failed to forward video: {e}")
                    if rewritten_text:
                        await bot_client.send_message(TARGET_CHANNEL, rewritten_text, parse_mode="md")
                        SEEN_MESSAGES.add(msg_key)
                        return True
                    return False
            
            # Check if GIF/animated
            if "image/gif" in mime_type or "video/mp4" in mime_type:
                logger.info(f"🎬 GIF/Animation from {channel_name} (mime: {mime_type})")
                try:
                    await bot_client.send_file(
                        TARGET_CHANNEL,
                        message.media.document,
                        caption=rewritten_text,
                        parse_mode="md" if rewritten_text else None
                    )
                    logger.info(f"✅ Forwarded GIF")
                    SEEN_MESSAGES.add(msg_key)
                    return True
                except Exception as e:
                    logger.error(f"Failed to forward GIF: {e}")
                    if rewritten_text:
                        await bot_client.send_message(TARGET_CHANNEL, rewritten_text, parse_mode="md")
                        SEEN_MESSAGES.add(msg_key)
                        return True
                    return False
            
            # Other documents - skip
            logger.info(f"⏭️ Skipped document type: {mime_type}")
            return False
        
        # ================ CASE 4: OTHER MEDIA ================
        if message.media:
            logger.info(f"⏭️ Skipped unsupported media type")
            return False
        
        # ================ CASE 5: EMPTY MESSAGE ================
        logger.info(f"⏭️ Empty message (no text/media)")
        return False
        
    except Exception as e:
        logger.exception(f"❌ Error processing message: {e}")
        return False

# ==================== POLLING FUNCTION ====================
async def poll_channels():
    """Poll all source channels for new messages"""
    global SEEN_MESSAGES
    
    logger.info("=" * 60)
    logger.info("🔍 POLLING FOR NEW MESSAGES")
    logger.info("=" * 60)
    
    total_forwarded = 0
    
    for channel in SOURCE_CHANNELS:
        try:
            logger.info(f"\n📍 Checking channel: {channel}")
            
            # Get last 50 messages from channel
            messages = await bot_client.get_messages(channel, limit=50)
            
            logger.info(f"📊 Found {len(messages)} messages in {channel}")
            
            # Process messages in reverse order (oldest first)
            for message in reversed(messages):
                if message:
                    forwarded = await process_message(message, channel)
                    if forwarded:
                        total_forwarded += 1
                    await asyncio.sleep(0.5)  # Small delay between messages
        
        except Exception as e:
            logger.error(f"❌ Error checking channel {channel}: {e}")
            continue
    
    # Save seen messages
    save_seen_messages(SEEN_MESSAGES)
    
    logger.info("\n" + "=" * 60)
    logger.info(f"✅ POLLING COMPLETE - Forwarded {total_forwarded} messages")
    logger.info(f"📊 Total tracked messages: {len(SEEN_MESSAGES)}")
    logger.info("=" * 60)
    
    return total_forwarded

# ==================== MAIN BOT ====================
async def main():
    """Main bot function - runs once and exits"""
    await bot_client.start()
    
    logger.info("=" * 60)
    logger.info("🚀 CHANNEL FORWARDER BOT (POLLING MODE)")
    logger.info("=" * 60)
    logger.info(f"📍 Monitoring: {SOURCE_CHANNELS}")
    logger.info(f"📤 Target Channel: {TARGET_CHANNEL}")
    logger.info(f"🤖 AI Rewriting: {FLOWISE_URL[:50]}...")
    logger.info("=" * 60)
    
    try:
        # Poll channels once
        forwarded = await poll_channels()
        logger.info(f"\n✅ Run completed successfully. Forwarded: {forwarded}")
        
    except Exception as e:
        logger.exception(f"❌ Bot error: {e}")
    
    finally:
        await bot_client.disconnect()
        logger.info("\n🔌 Disconnected")

# ==================== ENTRY POINT ====================
if __name__ == "__main__":
    try:
        logger.info("Starting bot...")
        bot_client.loop.run_until_complete(main())
        logger.info("Bot finished - exiting (GitHub Actions will handle scheduling)")
    except KeyboardInterrupt:
        logger.info("Shutdown signal received")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
