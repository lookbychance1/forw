import os
import asyncio
import logging
from telegram.ext import (
    Application, CommandHandler, ConversationHandler, MessageHandler, filters, CallbackContext
)
from telegram import Update
from flask import Flask
import threading
import aiohttp
from telegram.error import NetworkError

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define states for the conversation
ENTER_SOURCE_CHAT_ID, ENTER_DESTINATION_CHAT_ID, ENTER_MESSAGE_IDS = range(1, 4)

# Source and Destination Supergroup Chat IDs (example values)
SOURCE_GROUP_CHAT_ID = "-1003620670748"
DESTINATION_GROUP_CHAT_ID = "-1003819882418"

# List of user commands
user_commands = [
    '/prepladder', '/btr', '/cerebellum', '/marrow',
    '/bunow', '/livedemo', '/contactus', '/notes',
    '/sharebot', '/offertime'
]

# List of subcommands and their corresponding URLs
subcommands = {
    '/genmedicine': 'https://t.me/btrcerebellumvideos/5',
    '/fmt': 'https://t.me/prepladdernotesfreee/5',
    '/pedia': 'https://t.me/prepladdernotesfreee/6'
}

# Define user command handlers
async def start(update: Update, context: CallbackContext):
    commands_list = '\n'.join(user_commands)
    await update.message.reply_text(f'Hey! Please choose one from the list of commands:\n{commands_list}')

# Unknown command handler
async def unknown_command(update: Update, context: CallbackContext):
    await update.message.reply_text("Command not recognized. Use /start to see available commands.")

# Functions to handle specific commands
async def handle_subcommand(update: Update, context: CallbackContext):
    command = update.message.text
    url = subcommands.get(command)
    if url:
        await forward_message(update, context, url)
    else:
        await update.message.reply_text("Subcommand not found.")

async def forward_message(update: Update, context: CallbackContext, url: str):
    try:
        group_username = url.split('/')[-2]
        message_id = url.split('/')[-1]
        group_chat_id = f"@{group_username}"
        
        await context.bot.copy_message(
            chat_id=update.effective_user.id,
            from_chat_id=SOURCE_GROUP_CHAT_ID,
            message_id=int(message_id),
            protect_content=True
        )
        await update.message.reply_text("Message forwarded successfully.")
    except Exception as e:
        await update.message.reply_text(f"Error forwarding message: {str(e)}")

# Conversation handler for admin command
async def sendmsg(update: Update, context: CallbackContext):
    if update.effective_user.id in [8181528890]:  # Example admin user IDs
        await update.message.reply_text("Please enter the source chat ID:")
        return ENTER_SOURCE_CHAT_ID
    else:
        await update.message.reply_text("You are not authorized to use this command.")
        return ConversationHandler.END

async def process_source_chat_id(update: Update, context: CallbackContext):
    context.user_data['source_chat_id'] = update.message.text.strip()
    await update.message.reply_text("Please enter the destination chat ID:")
    return ENTER_DESTINATION_CHAT_ID

async def process_destination_chat_id(update: Update, context: CallbackContext):
    context.user_data['destination_chat_id'] = update.message.text.strip()
    await update.message.reply_text("Please enter the range of message IDs (e.g., '1-10') to forward:")
    return ENTER_MESSAGE_IDS

async def process_message_ids(update: Update, context: CallbackContext):
    try:
        input_range = update.message.text.strip()
        
        if '-' not in input_range:
            await update.message.reply_text("Invalid format. Please enter the range in the format 'start-end', e.g., '1-10'.")
            return
        
        start_id, end_id = map(int, input_range.split('-'))
        
        if start_id <= 0 or end_id <= 0 or start_id > end_id:
            await update.message.reply_text("Invalid range. Please enter a positive range in the format 'start-end', e.g., '1-10'.")
            return


        # Calculate estimated time based on the number of messages
        message_count = end_id - start_id + 1
        estimated_seconds = message_count * 3  # Estimated 3 seconds per message
        
        # Convert estimated time to hours, minutes, and seconds
        hours = estimated_seconds // 3600
        minutes = (estimated_seconds % 3600) // 60
        seconds = estimated_seconds % 60
        
        estimated_time = f"{hours:02} Hr:{minutes:02} Min:{seconds:02} Sec"  # Format as xx:vv:bb
        await update.message.reply_text(f"Estimated Time to Send {message_count} messages: {estimated_time}")


        source_chat_id = context.user_data['source_chat_id']
        destination_chat_id = context.user_data['destination_chat_id']

        for message_id in range(start_id, end_id + 1):
            try:
                await asyncio.sleep(3)
                await context.bot.copy_message(
                    chat_id=destination_chat_id,
                    from_chat_id=source_chat_id,
                    message_id=message_id,
                    protect_content=false
                )
            except NetworkError as e:
                logger.warning(f'NetworkError occurred: {e}. Retrying in 1 second...')
                await asyncio.sleep(1)
            except Exception as e:
                if "message to copy not found" in str(e).lower():
                    logger.warning(f"Message ID {message_id} not found; skipping.")
                    continue
                else:
                    logger.error(f"Error processing message ID {message_id}: {e}")
                    await update.message.reply_text(f"Unexpected error: {str(e)}")
                    
        await update.message.reply_text(f"Messages {start_id} to {end_id} processed.")
    except ValueError:
        await update.message.reply_text("Invalid format. Please enter the range in the format 'start-end', e.g., '1-10'.")
    except Exception as e:
        logger.error(f"Error processing message IDs: {e}")
        await update.message.reply_text(f"Error processing message IDs: {str(e)}")
    return ConversationHandler.END

# Flask app setup for monitoring
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot is running."

def run_flask():
    app_flask.run(host='0.0.0.0', port=2083)

# Function for pinging the URL
async def ping_url(context: CallbackContext):
    url = 'https://forw-10tm.onrender.com'
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    logger.info(f'Successfully pinged {url} with status {response.status}')
                else:
                    logger.warning(f'Ping to {url} returned non-200 status: {response.status}')
    except Exception as e:
        logger.error(f'Error pinging {url}: {e}')

# Main function to start the bot
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment.")
        return

    application = Application.builder().token(token).build()

    # Update the Conversation Handler
    sendmsg_handler = ConversationHandler(
        entry_points=[CommandHandler('sendmsg', sendmsg)],
        states={
            ENTER_SOURCE_CHAT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_source_chat_id)],
            ENTER_DESTINATION_CHAT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_destination_chat_id)],
            ENTER_MESSAGE_IDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_message_ids)]
        },
        fallbacks=[CommandHandler('cancel', lambda u, c: u.message.reply_text("Canceled."))]
    )

    # Add handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(sendmsg_handler)

    # User command and unknown command handling
    for command in user_commands:
        application.add_handler(CommandHandler(command[1:], lambda u, c: u.message.reply_text(f"{command} triggered.")))
    for command in subcommands.keys():
        application.add_handler(CommandHandler(command[1:], handle_subcommand))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_command))

    # Schedule ping_url to run every 60 seconds in the job queue
    application.job_queue.run_repeating(ping_url, interval=60, first=0)
    logger.info("Scheduled ping_url task in the job queue.")

    # Run Flask app in a separate thread
    threading.Thread(target=run_flask).start()

    # Run the bot
    application.run_polling()

if __name__ == '__main__':
    main()
