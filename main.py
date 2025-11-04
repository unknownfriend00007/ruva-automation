import os
import asyncio
import logging
import json
import time
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
import requests

load_dotenv()

# ==================== CONFIG ====================
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_STRING_SESSION = os.getenv("BOT_STRING_SESSION", "").strip()
FLOWISE_URL = os.getenv("FLOWISE_URL", "")
TARGET_CHANNEL = os.getenv("TARGET_CHANNEL", "")
SOURCE_CHANNELS = [ch.strip() for ch in os.getenv("SOURCE_CHANNELS", "").split(",") if ch.strip()]

# Timeout settings (1 hour)
TIMEOUT_SECONDS = 3600  # 1 hour
START_TIME = time.time()

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ==================== TRACKING ====================
STATE_FILE = "/tmp/bot_state.json"

def load_state():
    """Load bot state"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return {}
    return {}

def save_state(state):
    """Save bot state"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")

STATE = load_state()

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

# ==================== CHECK TIMEOUT ====================
def check_timeout():
    """Check if 1 hour has passed"""
    elapsed = time.time() - START_TIME
    if elapsed >= TIMEOUT_SECONDS:
        logger.warning(f"⏰ TIMEOUT REACHED! ({elapsed:.0f}s)")
        logger.info("Exiting to restart...")
        return True
    return False

# ==================== MESSAGE HANDLER ====================
async def handle_new_message(event):
    """Handle incoming messages"""
    global STATE
    
    try:
        # Check timeout before processing
        if check_timeout():
            await bot_client.disconnect()
            return
        
        message = event.message
        channel_name = event.chat.title or f"Channel {event.chat_id}"
        
        logger.info(f"📨 New message from {channel_name}")
        
        # Get caption/text
        caption = message.text or ""
        if message.media and hasattr(message.media, 'caption'):
            caption = message.media.caption or ""
        
        caption = caption.strip()
        
        # Rewrite with AI
        rewritten = None
        if caption:
            logger.info(f"📝 Rewriting: {caption[:50]}...")
            rewritten = call_ai_bot(caption)
            if not rewritten:
                rewritten = caption
        
        # Forward message
        try:
            await bot_client.forward_messages(
                entity=TARGET_CHANNEL,
                messages=message.id,
                from_peer=message.chat_id
            )
            
            # Edit caption if rewritten
            if rewritten and rewritten != caption:
                await asyncio.sleep(0.5)
                try:
                    recent = await bot_client.get_messages(TARGET_CHANNEL, limit=1)
                    if recent and recent[0]:
                        await bot_client.edit_message(
                            TARGET_CHANNEL,
                            recent[0].id,
                            text=rewritten,
                            parse_mode="md"
                        )
                        logger.info(f"✅ Forwarded + edited from {channel_name}")
                except:
                    logger.info(f"✅ Forwarded from {channel_name}")
            else:
                logger.info(f"✅ Forwarded from {channel_name}")
        
        except Exception as e:
            logger.error(f"Failed to forward: {e}")
    
    except Exception as e:
        logger.exception(f"Error in handle_new_message: {e}")

# ==================== MAIN BOT ====================
async def main():
    """Main bot function - runs for 1 hour then exits"""
    await bot_client.start()
    
    logger.info("=" * 70)
    logger.info("🚀 TELEGRAM BOT FORWARDER (EVENT-BASED, 1-HOUR MODE)")
    logger.info("=" * 70)
    logger.info(f"📍 Source Channels: {SOURCE_CHANNELS}")
    logger.info(f"📤 Target Channel: {TARGET_CHANNEL}")
    logger.info(f"🤖 AI Rewriting: {FLOWISE_URL[:50] if FLOWISE_URL else 'Disabled'}...")
    logger.info(f"⏰ Timeout: {TIMEOUT_SECONDS // 60} minutes")
    logger.info("=" * 70)
    logger.info("✅ Listening for new messages (will timeout in 1 hour)...")
    logger.info("=" * 70)
    
    # Register event handler
    @bot_client.on(events.NewMessage(chats=SOURCE_CHANNELS))
    async def handler(event):
        await handle_new_message(event)
    
    # Run with timeout check
    try:
        while True:
            # Check timeout every 10 seconds
            if check_timeout():
                logger.info("⏰ Time limit reached - disconnecting...")
                break
            
            await asyncio.sleep(10)
            
    except KeyboardInterrupt:
        logger.info("⛔ Interrupted by user")
    except Exception as e:
        logger.exception(f"❌ Error: {e}")
    
    finally:
        await bot_client.disconnect()
        logger.info("🔌 Disconnected")
        logger.info("✅ Exiting - GitHub Actions will restart in 1 hour")

# ==================== ENTRY POINT ====================
if __name__ == "__main__":
    try:
        logger.info(f"Starting bot... (will run for {TIMEOUT_SECONDS // 60} minutes)")
        bot_client.loop.run_until_complete(main())
        logger.info("Bot finished - exiting")
    except KeyboardInterrupt:
        logger.info("Interrupted")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
