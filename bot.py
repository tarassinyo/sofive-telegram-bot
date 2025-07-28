import logging
import datetime
import pytz
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, PollHandler, CallbackContext

# --- CONFIGURATION ---
# These will be read from your Railway variables
TARGET_CHAT_ID_STR = os.getenv("TARGET_CHAT_ID")
TARGET_CHAT_ID = int(TARGET_CHAT_ID_STR) if TARGET_CHAT_ID_STR else 0
FORWARD_CHAT_ID_STR = os.getenv("FORWARD_CHAT_ID")
FORWARD_CHAT_ID = int(FORWARD_CHAT_ID_STR) if FORWARD_CHAT_ID_STR else 0

TIMEZONE = "America/New_York"
# ---------------------

# Standard Logging Setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# In-memory dictionary to store poll data
poll_data = {}


# --- BOT FUNCTIONS ---

async def start(update: Update, context: CallbackContext) -> None:
    """Sends a welcome message."""
    await update.message.reply_text('Привіт! Я бот для щотижневих опитувань.')

async def chatid(update: Update, context: CallbackContext) -> None:
    """A helper command to get the chat ID of the current group."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title
    text = f"The Chat ID for the group '{chat_title}' is: `{chat_id}`"
    try:
        await context.bot.send_message(chat_id=user.id, text=text, parse_mode='Markdown')
        await update.message.reply_text("I've sent you the Chat ID in a private message.", quote=True)
    except Exception:
        await update.message.reply_text("I couldn't send you a private message. Please start a chat with me first.", quote=True)

async def check_and_forward_poll(context: CallbackContext) -> None:
    """Checks poll votes on Saturday and forwards if needed."""
    job = context.job
    poll_id = job.data['poll_id']

    if poll_id in poll_data:
        if FORWARD_CHAT_ID == 0:
            logger.warning("FORWARD_CHAT_ID not set, skipping forward.")
            return

        current_votes = poll_data[poll_id].get("plus_votes", 0)
        if current_votes < 15:
            logger.info(f"Poll {poll_id} has only {current_votes} votes. Forwarding to {FORWARD_CHAT_ID}.")
            try:
                await context.bot.forward_message(
                    chat_id=FORWARD_CHAT_ID,
                    from_chat_id=job.data['chat_id'],
                    message_id=job.data['message_id']
                )
            except Exception as e:
                logger.error(f"Failed to forward message: {e}")
        else:
            logger.info(f"Poll {poll_id} has {current_votes} votes. No need to forward.")

async def create_and_send_poll(context: CallbackContext, chat_id: int):
    """Creates, sends, and schedules closing/forwarding for a poll."""
    today = datetime.date.today()
    
    days_until_sunday = (6 - today.weekday() + 7) % 7
    if days_until_sunday == 0: days_until_sunday = 7
    next_sunday_date = today + datetime.timedelta(days=days_until_sunday)
    
    days_until_saturday = (5 - today.weekday() + 7) % 7
    if days_until_saturday == 0: days_until_saturday = 7
    next_saturday_date = today + datetime.timedelta(days=days_until_saturday)

    date_str = next_sunday_date.strftime("%m/%d")
    poll_title = f"Футбол. Неділя {date_str} 09:00am. Sofive - Поле #6"
    
    questions = ["+", "-"]
    message = await context.bot.send_poll(
        chat_id=chat_id, question=poll_title, options=questions,
        is_anonymous=False, allows_multiple_answers=False,
    )
    
    poll_id = message.poll.id
    poll_data[poll_id] = {"chat_id": chat_id, "message_id": message.message_id, "plus_votes": 0}

    local_tz = pytz.timezone(TIMEZONE) 
    
    close_time_naive = datetime.datetime.combine(next_sunday_date, datetime.time(10, 0))
    close_time_aware = local_tz.localize(close_time_naive)
    context.job_queue.run_once(auto_close_poll, when=close_time_aware, data={'chat_id': chat_id, 'message_id': message.message_id, 'poll_id': poll_id}, name=f"close_{poll_id}")

    forward_time_naive = datetime.datetime.combine(next_saturday_date, datetime.time(10, 0))
    forward_time_aware = local_tz.localize(forward_time_naive)
    context.job_queue.run_once(check_and_forward_poll, when=forward_time_aware, data={'chat_id': chat_id, 'message_id': message.message_id, 'poll_id': poll_id}, name=f"forward_{poll_id}")

    logger.info(f"Poll {poll_id} scheduled for closing and forwarding checks.")

async def poll_command(update: Update, context: CallbackContext) -> None:
    """Handler for the /poll command to manually start a poll."""
    await create_and_send_poll(context, update.effective_chat.id)

async def send_weekly_poll(context: CallbackContext) -> None:
    """Checks if it's Thursday and sends the weekly poll if it is."""
    if datetime.date.today().weekday() != 3: # 3 = Thursday
        return
    
    if TARGET_CHAT_ID == 0:
        logger.warning("TARGET_CHAT_ID is not set. Skipping scheduled poll.")
        return
    logger.info("It's Thursday! Running scheduled job to send weekly poll.")
    await create_and_send_poll(context, TARGET_CHAT_ID)

async def receive_poll_update(update: Update, context: CallbackContext) -> None:
    """Handles poll updates, tracks votes, and closes poll if count is met."""
    if not update.poll or not update.poll.id in poll_data:
        return

    poll_id = update.poll.id
    plus_votes = update.poll.options[0].voter_count
    
    if poll_id in poll_data:
        poll_data[poll_id]["plus_votes"] = plus_votes

    if plus_votes >= 15:
        logger.info(f"Closing poll {poll_id} as '+' has reached 15 votes.")
        
        # Cancel both scheduled jobs
        jobs_to_cancel = context.job_queue.get_jobs_by_name(f"close_{poll_id}") + context.job_queue.get_jobs_by_name(f"forward_{poll_id}")
        for job in jobs_to_cancel:
            job.schedule_removal()
        
        await context.bot.stop_poll(poll_data[poll_id]['chat_id'], poll_data[poll_id]['message_id'])
        del poll_data[poll_id]

async def auto_close_poll(context: CallbackContext) -> None:
    """Closes the poll automatically at the scheduled time."""
    job = context.job
    poll_id = job.data['poll_id']

    if poll_id in poll_data:
        chat_id = job.data['chat_id']
        message_id = job.data['message_id']
        await context.bot.stop_poll(chat_id, message_id)
        del poll_data[poll_id]

# --- MAIN EXECUTION ---
def main() -> None:
    """Sets up and runs the Sofive poll bot."""
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    if not TOKEN:
        raise ValueError("No TELEGRAM_TOKEN found in environment variables")

    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("poll", poll_command))
    application.add_handler(CommandHandler("chatid", chatid))
    application.add_handler(PollHandler(receive_poll_update))

    job_queue = application.job_queue
    job_queue.run_daily(
        callback=send_weekly_poll,
        time=datetime.time(hour=9, minute=0, second=0, tzinfo=pytz.timezone(TIMEZONE)),
        name="daily_poll_check"
    )

    application.run_polling()


if __name__ == '__main__':
    main()
