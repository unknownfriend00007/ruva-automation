import os, asyncio, logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaPhoto
import requests

# Environment variables
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_STRING_SESSION = os.getenv("BOT_STRING_SESSION", "").strip()
FLOWISE_URL = os.getenv("FLOWISE_URL", "")
TARGET_CHANNEL = os.getenv("TARGET_CHANNEL", "")
SOURCE_CHANNELS = [ch.strip() for ch in os.getenv("SOURCE_CHANNELS", "").split(",") if ch.strip()]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Bot session setup
if BOT_STRING_SESSION:
    bot_client = TelegramClient(StringSession(BOT_STRING_SESSION), API_ID, API_HASH)
else:
    raise ValueError("BOT_STRING_SESSION is required!")

def call_ai_bot(message_text: str) -> str:
    """Call Flowise AI to rewrite message"""
    payload = {
        "question": message_text,
        "streaming": False,
        "overrideConfig": {}
    }
    try:
        r = requests.post(FLOWISE_URL, json=payload, timeout=45)
        r.raise_for_status()
        data = r.json()
        response = data.get("text") or data.get("data") or ""
        return response.strip()
    except Exception:
        logging.exception("AI bot failed")
        return None

async def forward_message(event):
    """Handle new messages from source channels"""
    try:
        message = event.message
        source_name = event.chat.title or f"Channel {event.chat_id}"
        
        # Skip messages with only videos, documents, etc.
        has_text = message.text is not None and len(message.text.strip()) > 0
        has_photo = message.media and isinstance(message.media, MessageMediaPhoto)
        has_gif = message.media and hasattr(message.media, 'document') and message.media.document and 'video/mp4' in str(message.media.document.mime_type)
        has_video = message.media and hasattr(message.media, 'document') and message.media.document and 'video' in str(message.media.document.mime_type)
        has_caption = message.media and hasattr(message.media, 'caption') and message.media.caption
        
        # Case 1: Text only
        if has_text and not message.media:
            logging.info(f"📝 Text from {source_name}: {message.text[:60]}...")
            rewritten = call_ai_bot(message.text)
            
            if rewritten:
                await bot_client.send_message(TARGET_CHANNEL, rewritten, parse_mode="md")
                logging.info(f"✅ Forwarded text from {source_name}")
            return
        
        # Case 2: Image (with or without caption)
        if has_photo:
            caption = message.media.caption if has_caption else message.text or ""
            
            if caption:
                logging.info(f"🖼️ Image with caption from {source_name}: {caption[:60]}...")
                rewritten = call_ai_bot(caption)
                if not rewritten:
                    rewritten = caption
            else:
                rewritten = f"[Image from {source_name}]"
                logging.info(f"🖼️ Image from {source_name} (no caption)")
            
            # Forward image with rewritten caption
            try:
                await bot_client.send_file(
                    TARGET_CHANNEL,
                    message.media.photo,
                    caption=rewritten,
                    parse_mode="md"
                )
                logging.info(f"✅ Forwarded image from {source_name}")
            except Exception as e:
                logging.error(f"Failed to forward image: {e}")
                # Fallback: send text only
                await bot_client.send_message(TARGET_CHANNEL, rewritten, parse_mode="md")
            return
        
        # Case 3: GIF (with or without caption)
        if has_gif:
            caption = message.media.caption if has_caption else message.text or ""
            
            if caption:
                logging.info(f"🎬 GIF with caption from {source_name}: {caption[:60]}...")
                rewritten = call_ai_bot(caption)
                if not rewritten:
                    rewritten = caption
            else:
                rewritten = f"[GIF from {source_name}]"
                logging.info(f"🎬 GIF from {source_name} (no caption)")
            
            # Forward GIF with rewritten caption
            try:
                await bot_client.send_file(
                    TARGET_CHANNEL,
                    message.media.document,
                    caption=rewritten,
                    parse_mode="md"
                )
                logging.info(f"✅ Forwarded GIF from {source_name}")
            except Exception as e:
                logging.error(f"Failed to forward GIF: {e}")
                # Fallback: send text only
                await bot_client.send_message(TARGET_CHANNEL, rewritten, parse_mode="md")
            return
        
        # Case 4: Video (only caption, skip video)
        if has_video:
            caption = message.media.caption if has_caption else message.text or ""
            
            if caption:
                logging.info(f"🎥 Video with caption from {source_name}: {caption[:60]}...")
                rewritten = call_ai_bot(caption)
                
                if rewritten:
                    await bot_client.send_message(TARGET_CHANNEL, rewritten, parse_mode="md")
                    logging.info(f"✅ Forwarded video caption from {source_name} (video skipped)")
            else:
                logging.info(f"⏭️ Video from {source_name} with no caption (skipped)")
            return
        
        # Case 5: Other media (documents, etc.) - skip
        if message.media:
            logging.info(f"⏭️ Skipped media type from {source_name}")
            return
        
        # Case 6: Empty message - skip
        logging.info(f"⏭️ Empty message from {source_name}")
        
    except Exception:
        logging.exception("Error in forward_message")

async def main():
    """Main bot function"""
    await bot_client.start()
    
    logging.info("🚀 Channel Forwarder Bot started")
    logging.info(f"📍 Monitoring: {SOURCE_CHANNELS}")
    logging.info(f"📤 Posting to: {TARGET_CHANNEL}")
    logging.info(f"📋 Features: Text + Images + GIFs (rewritten). Videos & Docs skipped.")
    
    # Register event handler for each source channel
    @bot_client.on(events.NewMessage(chats=SOURCE_CHANNELS))
    async def handler(event):
        await forward_message(event)
    
    logging.info("✅ Event handlers registered. Listening for messages...")
    
    try:
        await bot_client.run_until_disconnected()
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    finally:
        await bot_client.disconnect()

if __name__ == "__main__":
    bot_client.loop.run_until_complete(main())
