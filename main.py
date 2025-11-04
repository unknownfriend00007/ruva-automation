import os
import asyncio
import logging
import json
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, ChatAdminRequiredError
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

# ==================== TRACKING ====================
STATE_FILE = "/tmp/bot_state.json"

def load_state():
    """Load bot state (last processed message IDs)"""
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

# ==================== MESSAGE FORWARDING ====================
async def rewrite_and_forward_message(message, channel_name):
    """Rewrite message caption and forward"""
    try:
        # Get caption or text
        caption = ""
        if message.media and hasattr(message.media, 'caption'):
            caption = message.media.caption or ""
        elif message.text:
            caption = message.text
        
        caption = caption.strip()
        
        # Rewrite with AI if has caption
        rewritten_caption = None
        if caption:
            logger.info(f"📝 Rewriting: {caption[:50]}...")
            rewritten_caption = call_ai_bot(caption)
            if not rewritten_caption:
                rewritten_caption = caption
        
        # Forward message
        try:
            await bot_client.forward_messages(
                entity=TARGET_CHANNEL,
                messages=message.id,
                from_peer=message.chat_id
            )
            
            # If we have rewritten caption, edit it
            if rewritten_caption and rewritten_caption != caption:
                # Get the forwarded message to edit it
                await asyncio.sleep(0.5)
                
                # Find the just-forwarded message
                recent = await bot_client.get_messages(TARGET_CHANNEL, limit=1)
                if recent and recent[0]:
                    try:
                        await bot_client.edit_message(
                            TARGET_CHANNEL,
                            recent[0].id,
                            text=rewritten_caption,
                            parse_mode="md"
                        )
                        logger.info(f"✅ Forwarded + edited from {channel_name}")
                    except Exception as e:
                        logger.warning(f"Could not edit caption: {e}")
                        logger.info(f"✅ Forwarded (caption not edited)")
            else:
                logger.info(f"✅ Forwarded from {channel_name}")
            
            return True
            
        except ChatAdminRequiredError:
            logger.error(f"Bot not admin in target channel!")
            return False
        except FloodWaitError as e:
            logger.warning(f"Flood wait: {e.seconds}s")
            await asyncio.sleep(e.seconds + 1)
            return False
        except Exception as e:
            logger.error(f"Failed to forward: {e}")
            return False
    
    except Exception as e:
        logger.exception(f"Error in rewrite_and_forward: {e}")
        return False

# ==================== MAIN PROCESSING ====================
async def process_channels():
    """Process all source channels for new messages"""
    global STATE
    
    logger.info("=" * 70)
    logger.info("🔍 CHECKING CHANNELS FOR NEW MESSAGES")
    logger.info("=" * 70)
    
    total_forwarded = 0
    
    for channel in SOURCE_CHANNELS:
        try:
            logger.info(f"\n📍 Channel: {channel}")
            
            # Get last processed ID for this channel
            last_id = STATE.get(channel, 0)
            logger.info(f"   Last processed ID: {last_id}")
            
            # Try to get recent messages
            try:
                # This might work - getting recent messages without full history
                messages = await bot_client.get_messages(
                    channel,
                    limit=100,
                    min_id=last_id
                )
                
                logger.info(f"   Found {len(messages)} messages after ID {last_id}")
                
                if messages:
                    # Sort by ID (newest last)
                    messages = sorted(messages, key=lambda m: m.id)
                    
                    for message in messages:
                        if message and message.id > last_id:
                            logger.info(f"   Processing message {message.id}")
                            
                            # Forward and rewrite
                            if await rewrite_and_forward_message(message, channel):
                                total_forwarded += 1
                                STATE[channel] = message.id
                                await asyncio.sleep(1)
                
            except Exception as e:
                # If min_id doesn't work, try limit-based approach
                logger.warning(f"   GetMessages failed: {e}")
                logger.info(f"   Trying alternative method...")
                
                try:
                    # Get last 50 messages without specifying min_id
                    messages = await bot_client.get_messages(channel, limit=50)
                    
                    if messages:
                        messages = sorted(messages, key=lambda m: m.id)
                        
                        for message in messages:
                            if message and message.id > last_id:
                                logger.info(f"   Processing message {message.id}")
                                
                                if await rewrite_and_forward_message(message, channel):
                                    total_forwarded += 1
                                    STATE[channel] = message.id
                                    await asyncio.sleep(1)
                
                except Exception as e2:
                    logger.error(f"   Alternative method also failed: {e2}")
                    logger.info(f"   ⚠️ Skipping {channel}")
                    continue
        
        except Exception as e:
            logger.exception(f"Error processing {channel}: {e}")
            continue
    
    # Save state
    save_state(STATE)
    
    logger.info("\n" + "=" * 70)
    logger.info(f"✅ PROCESSING COMPLETE")
    logger.info(f"   Forwarded: {total_forwarded} messages")
    logger.info(f"   Tracked channels: {len(STATE)}")
    logger.info("=" * 70)
    
    return total_forwarded

# ==================== MAIN BOT ====================
async def main():
    """Main bot function"""
    await bot_client.start()
    
    logger.info("=" * 70)
    logger.info("🚀 TELEGRAM BOT FORWARDER (FORWARD_MESSAGES MODE)")
    logger.info("=" * 70)
    logger.info(f"📍 Source Channels: {SOURCE_CHANNELS}")
    logger.info(f"📤 Target Channel: {TARGET_CHANNEL}")
    logger.info(f"🤖 AI Rewriting: {FLOWISE_URL[:50] if FLOWISE_URL else 'Disabled'}...")
    logger.info("=" * 70)
    
    try:
        # Process channels
        await process_channels()
        logger.info("\n✅ Bot run completed successfully!")
    
    except Exception as e:
        logger.exception(f"❌ Fatal error: {e}")
    
    finally:
        await bot_client.disconnect()
        logger.info("🔌 Disconnected from Telegram")

# ==================== ENTRY POINT ====================
if __name__ == "__main__":
    try:
        logger.info("Starting bot...")
        bot_client.loop.run_until_complete(main())
        logger.info("Bot finished - exiting")
    except KeyboardInterrupt:
        logger.info("⛔ Interrupted by user")
    except Exception as e:
        logger.exception(f"❌ Fatal: {e}")
