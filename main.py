import os
import asyncio
import logging
import json
import time
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
import requests

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_STRING_SESSION = os.getenv("BOT_STRING_SESSION", "").strip()
FLOWISE_URL = os.getenv("FLOWISE_URL", "")
TARGET_CHANNEL = os.getenv("TARGET_CHANNEL", "")
SOURCE_CHANNELS = [ch.strip() for ch in os.getenv("SOURCE_CHANNELS", "").split(",") if ch.strip()]

TIMEOUT_SECONDS = 3600
START_TIME = time.time()

MEDIA_BUFFER = {}
BUFFER_DELAY = 2

STATE_FILE = "/tmp/bot_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except:
        pass

SEEN_MESSAGES = load_state()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

if BOT_STRING_SESSION:
    bot_client = TelegramClient(StringSession(BOT_STRING_SESSION), API_ID, API_HASH)
else:
    raise ValueError("BOT_STRING_SESSION required!")

def call_ai_bot(text: str) -> str:
    if not text or not text.strip():
        return None
    
    payload = {"question": text, "streaming": False, "overrideConfig": {}}
    
    try:
        response = requests.post(FLOWISE_URL, json=payload, timeout=45)
        data = response.json()
        result = data.get("text") or data.get("data") or ""
        return result.strip() if result else None
    except Exception as e:
        logger.error(f"AI failed: {e}")
        return None

def check_timeout():
    elapsed = time.time() - START_TIME
    return elapsed >= TIMEOUT_SECONDS

def is_valid_media(media):
    """
    Check if IMAGES, GIFs, or VIDEOS
    SKIP: documents, audio, etc
    """
    
    # ✅ PHOTO
    if isinstance(media, MessageMediaPhoto):
        return True, "IMAGE"
    
    # Document check
    if isinstance(media, MessageMediaDocument):
        doc = media.document
        mime_type = doc.mime_type if doc else ""
        
        # ✅ GIF (animation)
        if "image/gif" in mime_type:
            return True, "GIF"
        
        # ✅ VIDEO (treat like GIF - stream, no chunking!)
        if "video" in mime_type:
            return True, "VIDEO"
        
        # ❌ Everything else (audio, documents, etc)
        return False, "OTHER"
    
    return False, "UNKNOWN"

async def send_buffered_media(channel_id, chat_title):
    global MEDIA_BUFFER, SEEN_MESSAGES
    
    if channel_id not in MEDIA_BUFFER:
        return
    
    try:
        buffer = MEDIA_BUFFER[channel_id]
        media_list = buffer['media']
        caption = buffer['caption']
        msg_ids = buffer['msg_ids']
        
        if not media_list:
            return
        
        logger.info(f"📦 Sending {len(media_list)} media")
        
        # Mark as seen
        for msg_id in msg_ids:
            key = f"{channel_id}_{msg_id}"
            SEEN_MESSAGES[key] = True
        save_state(SEEN_MESSAGES)
        
        # Rewrite caption
        rewritten_caption = None
        if caption:
            logger.info(f"📝 Rewriting: {caption[:60]}...")
            rewritten_caption = call_ai_bot(caption)
            if not rewritten_caption:
                rewritten_caption = caption
        
        # Send (NO chunking - Telethon handles it!)
        try:
            if len(media_list) == 1:
                await bot_client.send_file(
                    TARGET_CHANNEL,
                    media_list[0],
                    caption=rewritten_caption,
                    parse_mode="md" if rewritten_caption else None
                )
                logger.info(f"✅ Sent 1 media")
            else:
                await bot_client.send_file(
                    TARGET_CHANNEL,
                    media_list,
                    caption=rewritten_caption,
                    parse_mode="md" if rewritten_caption else None
                )
                logger.info(f"✅ Sent {len(media_list)} as album")
        except Exception as e:
            logger.error(f"Send failed: {e}")
        
        del MEDIA_BUFFER[channel_id]
    
    except Exception as e:
        logger.exception(f"Error: {e}")

async def handle_new_message(event):
    global MEDIA_BUFFER, SEEN_MESSAGES
    
    try:
        if check_timeout():
            await bot_client.disconnect()
            return
        
        message = event.message
        channel_id = event.chat_id
        msg_id = message.id
        chat_title = event.chat.title or f"Channel {channel_id}"
        
        key = f"{channel_id}_{msg_id}"
        if key in SEEN_MESSAGES:
            logger.info(f"⏭️ Already processed: {msg_id}")
            return
        
        logger.info(f"📨 Message #{msg_id} from {chat_title}")
        
        # Get text/caption
        text = message.text or ""
        caption = ""
        if message.media and hasattr(message.media, 'caption'):
            caption = message.media.caption or ""
        
        text = text.strip()
        caption = caption.strip()
        
        # ================ CASE 1: TEXT ONLY (NO MEDIA) ================
        if text and not message.media:
            logger.info(f"📝 TEXT ONLY")
            
            # Rewrite
            rewritten = call_ai_bot(text)
            if not rewritten:
                rewritten = text
            
            # Send
            try:
                await bot_client.send_message(TARGET_CHANNEL, rewritten, parse_mode="md")
                logger.info(f"✅ Sent text")
            except Exception as e:
                logger.error(f"Send failed: {e}")
            
            # Mark seen
            SEEN_MESSAGES[key] = True
            save_state(SEEN_MESSAGES)
            return
        
        # ================ CASE 2: NO TEXT, NO MEDIA ================
        if not text and not message.media:
            logger.info(f"⏭️ Empty message - skip")
            SEEN_MESSAGES[key] = True
            save_state(SEEN_MESSAGES)
            return
        
        # ================ CASE 3: MEDIA (WITH OR WITHOUT CAPTION) ================
        if message.media:
            is_valid, media_type = is_valid_media(message.media)
            logger.info(f"📊 Media: {media_type}")
            
            # Invalid media? Skip
            if not is_valid:
                logger.info(f"❌ {media_type} - skipping")
                SEEN_MESSAGES[key] = True
                save_state(SEEN_MESSAGES)
                return
            
            # ✅ VALID (IMAGE, GIF, or VIDEO)
            logger.info(f"✅ Valid - buffering")
            
            # Use caption if exists, otherwise use text, otherwise empty
            full_caption = caption if caption else text
            
            if channel_id not in MEDIA_BUFFER:
                MEDIA_BUFFER[channel_id] = {
                    'media': [],
                    'caption': full_caption,
                    'msg_ids': [],
                    'timer': None
                }
            
            MEDIA_BUFFER[channel_id]['media'].append(message.media)
            MEDIA_BUFFER[channel_id]['msg_ids'].append(msg_id)
            
            if MEDIA_BUFFER[channel_id]['timer']:
                MEDIA_BUFFER[channel_id]['timer'].cancel()
            
            async def send_after_delay():
                await asyncio.sleep(BUFFER_DELAY)
                await send_buffered_media(channel_id, chat_title)
            
            task = asyncio.create_task(send_after_delay())
            MEDIA_BUFFER[channel_id]['timer'] = task
            
            logger.info(f"📦 Buffering ({len(MEDIA_BUFFER[channel_id]['media'])} item)")
            return
        
    except Exception as e:
        logger.exception(f"Error: {e}")

async def main():
    await bot_client.start()
    
    logger.info("=" * 70)
    logger.info("🚀 TELEGRAM BOT FORWARDER")
    logger.info("=" * 70)
    logger.info(f"📍 Source: {SOURCE_CHANNELS}")
    logger.info(f"📤 Target: {TARGET_CHANNEL}")
    logger.info(f"🤖 AI: {FLOWISE_URL[:50] if FLOWISE_URL else 'None'}...")
    logger.info("=" * 70)
    logger.info("✅ TEXT ONLY → rewrite + send")
    logger.info("✅ IMAGES (+ caption) → rewrite + send")
    logger.info("✅ GIFs (+ caption) → rewrite + send")
    logger.info("✅ VIDEOS (+ caption) → rewrite + send")
    logger.info("✅ Multiple media → group as album")
    logger.info("✅ NO duplicates")
    logger.info("=" * 70)
    
    @bot_client.on(events.NewMessage(chats=SOURCE_CHANNELS))
    async def handler(event):
        await handle_new_message(event)
    
    try:
        while True:
            if check_timeout():
                logger.info("Timeout - exiting")
                break
            await asyncio.sleep(10)
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        await bot_client.disconnect()
        logger.info("Done")

if __name__ == "__main__":
    try:
        logger.info("Starting...")
        bot_client.loop.run_until_complete(main())
    except Exception as e:
        logger.exception(f"Fatal: {e}")
