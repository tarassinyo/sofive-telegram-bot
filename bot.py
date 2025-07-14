import logging
import datetime
import pytz
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, PollHandler, CallbackContext

# --- CONFIGURATION ---
# The bot will now read the Chat ID from a variable on the hosting service.
TARGET_CHAT_ID_STR = os.getenv("TARGET_CHAT_ID")
# Set a default value if the variable isn't found
TARGET_CHAT_ID = int(TARGET_CHAT_ID_STR) if TARGET_CHAT_ID_STR else 0

# Set your timezone. This correctly handles EST/EDT.
TIMEZONE = "America/New_York"
# ---------------------

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# In-memory dictionary to store poll data
poll_data = {}


# --- BOT FUNCTIONS ---

async def start(update: Update, context: CallbackContext) -> None:
    """Sends a welcome message."""
    await update.message.reply_text('Привіт! Я бот для щотижневих опитувань.')


async def chatid(update: Update, context: CallbackContext) -> None:
    """A helper command to get the chat ID of the current group."""
    await update.message.reply_text(f"The Chat ID for this group is: {update.effective_chat.id}")


async def create_and_send_poll(context: CallbackContext, chat_id: int):
    """A helper function to create, send, and schedule the closing of a poll."""
    today = datetime.date.today()
    # Find the date of the upcoming Sunday
    days_until_sunday = (6 - today.weekday() + 7) % 7
    if days_until_sunday == 0:
        days_until_sunday = 7
    
    next_sunday_date = today + datetime.timedelta(days=days_until_sunday)
    date_str = next_sunday_date.strftime("%m/%d")
    poll_title = f"Футбол. Неділя {date_str} 09:00am. Sofive - Поле #6"
    
    questions = ["+", "-"]
    message = await context.bot.send_poll(
        chat_id=chat_id,
        question=poll_title,
        options=questions,
        is_anonymous=False,
        allows_multiple_answers=False,
    )
    
    # Store poll info to track it
    poll_id = message.poll.id
    poll_data[poll_id] = {"chat_id": chat_id, "message_id": message.message_id}

    # Schedule the poll to auto-close on Sunday at 10 AM
    try:
        local_tz = pytz.timezone(TIMEZONE) 
        scheduled_time_naive = datetime.datetime.combine(next_sunday_date, datetime.time(10, 0))
        scheduled_time_aware = local_tz.localize(scheduled_time_naive)

        context.job_queue.run_once(
            auto_close_poll, 
            when=scheduled_time_aware, 
            data={'chat_id': chat_id, 'message_id': message.message_id, 'poll_id': poll_id},
            name=str(poll_id)
        )
        logger.info(f"Poll {poll_id} scheduled to auto-close at {scheduled_time_aware}")
    except Exception as e:
        logger.error(f"Failed to schedule poll closing for {poll_id}: {e}")


async def poll_command(update: Update, context: CallbackContext) -> None:
    """Handler for the /poll command to manually start a poll."""
    await create_and_send_poll(context, update.effective_chat.id)


async def send_weekly_poll(context: CallbackContext) -> None:
    """Checks if it's Thursday and sends the weekly poll if it is."""
    # Day check: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    if datetime.date.today().weekday() != 3:
        # If it's not Thursday, do nothing.
        logger.info("Daily job ran, but it's not Thursday. Skipping poll.")
        return
    
    # If it IS Thursday, send the poll.
    logger.info(f"It's Thursday! Running scheduled job to send weekly poll.")
    if TARGET_CHAT_ID == 0:
        logger.warning("TARGET_CHAT_ID is not set in the code. Skipping scheduled poll.")
        return
    await create_and_send_poll(context, TARGET_CHAT_ID)


async def receive_poll_update(update: Update, context: CallbackContext) -> None:
    """Handles poll updates and closes the poll if the vote count is met."""
    if not update.poll or not update.poll.id in poll_data:
        return

    poll_id = update.poll.id
    plus_votes = update.poll.options[0].voter_count

    # Close the poll if the "+" option reaches 15 votes
    if plus_votes >= 15:
        logger.info(f"Closing poll {poll_id} as '+' has reached 15 votes.")
        
        # Cancel the scheduled auto-close job since this is closing early
        current_jobs = context.job_queue.get_jobs_by_name(str(poll_id))
        for job in current_jobs:
            job.schedule_removal()
        
        await context.bot.stop_poll(
            poll_data[poll_id]['chat_id'],
            poll_data[poll_id]['message_id']
        )
        del poll_data[poll_id]


async def auto_close_poll(context: CallbackContext) -> None:
    """Closes the poll automatically at the scheduled time."""
    job = context.job
    poll_id = job.data['poll_id']

    # Only close if the poll is still being tracked (i.e., not already closed)
    if poll_id in poll_data:
        chat_id = job.data['chat_id']
        message_id = job.data['message_id']
        logger.info(f"Auto-closing poll {poll_id} in chat {chat_id} as scheduled.")
        
        try:
            await context.bot.stop_poll(chat_id, message_id)
            del poll_data[poll_id]
        except Exception as e:
            logger.error(f"Failed to auto-close poll {poll_id}: {e}")


# --- MAIN EXECUTION ---

def main() -> None:
    """Sets up and runs the bot."""
    # Get the token from the environment variable for security
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    if not TOKEN:
        raise ValueError("No TELEGRAM_TOKEN found in environment variables")

    application = Application.builder().token(TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("poll", poll_command))
    application.add_handler(CommandHandler("chatid", chatid))
    application.add_handler(PollHandler(receive_poll_update))

    # Schedule the job to run daily to check if it should send the poll
    job_queue = application.job_queue
    job_queue.run_daily(
        callback=send_weekly_poll,
        # Time is set to 9:00 AM EST/EDT
        time=datetime.time(hour=9, minute=0, second=0, tzinfo=pytz.timezone(TIMEZONE)),
        name="daily_poll_check"
    )

    # Start the bot
    application.run_polling()


if __name__ == '__main__':
    main()
