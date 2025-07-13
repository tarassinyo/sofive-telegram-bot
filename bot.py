import logging
import datetime
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, PollHandler, CallbackContext

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Store poll data
poll_data = {}

async def auto_close_poll(context: CallbackContext) -> None:
    """Closes the poll automatically at the scheduled time."""
    job = context.job
    poll_id = job.data['poll_id']

    if poll_id in poll_data:
        chat_id = job.data['chat_id']
        message_id = job.data['message_id']
        logger.info(f"Auto-closing poll {poll_id} in chat {chat_id} as scheduled.")
        
        try:
            await context.bot.stop_poll(chat_id, message_id)
            del poll_data[poll_id]
        except Exception as e:
            logger.error(f"Failed to auto-close poll {poll_id}: {e}")
    else:
        logger.info(f"Scheduled job for poll {poll_id} ran, but poll was already closed.")


async def start(update: Update, context: CallbackContext) -> None:
    """Sends a welcome message."""
    await update.message.reply_text('Привіт! Використовуйте /poll, щоб розпочати опитування.')

async def poll(update: Update, context: CallbackContext) -> None:
    """Starts a new poll and schedules it to be closed automatically."""
    today = datetime.date.today()
    days_until_sunday = (6 - today.weekday() + 7) % 7
    if days_until_sunday == 0:
        days_until_sunday = 7
    
    next_sunday_date = today + datetime.timedelta(days=days_until_sunday)
    date_str = next_sunday_date.strftime("%m/%d")
    poll_title = f"Футбол. Неділя {date_str} 10:00am. Sofive"
    
    questions = ["+", "-"]
    message = await context.bot.send_poll(
        update.effective_chat.id,
        poll_title,
        questions,
        is_anonymous=False,
        allows_multiple_answers=False,
    )
    
    poll_id = message.poll.id
    chat_id = message.chat.id
    message_id = message.message_id

    poll_data[poll_id] = {
        "chat_id": chat_id,
        "message_id": message_id,
    }

    try:
        local_tz = pytz.timezone("America/New_York") 
        scheduled_time_naive = datetime.datetime.combine(next_sunday_date, datetime.time(10, 0))
        scheduled_time_aware = local_tz.localize(scheduled_time_naive)

        context.job_queue.run_once(
            auto_close_poll, 
            when=scheduled_time_aware, 
            data={'chat_id': chat_id, 'message_id': message_id, 'poll_id': poll_id},
            name=str(poll_id)
        )
        logger.info(f"Poll {poll_id} is scheduled to auto-close at {scheduled_time_aware}")
    except Exception as e:
        logger.error(f"Failed to schedule closing for poll {poll_id}: {e}")

async def receive_poll_update(update: Update, context: CallbackContext) -> None:
    """Handles poll updates and closes the poll if the vote count is met."""
    if not update.poll or not update.poll.id in poll_data:
        return

    poll_id = update.poll.id
    plus_votes = update.poll.options[0].voter_count

    logger.info(f"Poll {poll_id} in chat {poll_data[poll_id]['chat_id']} has {plus_votes} '+' votes.")

    if plus_votes >= 15:
        logger.info(f"Closing poll {poll_id} as '+' has reached 15 votes.")
        
        current_jobs = context.job_queue.get_jobs_by_name(str(poll_id))
        for job in current_jobs:
            job.schedule_removal()
            logger.info(f"Canceled scheduled auto-close for poll {poll_id}.")

        await context.bot.stop_poll(
            poll_data[poll_id]['chat_id'],
            poll_data[poll_id]['message_id']
        )
        del poll_data[poll_id]

def main() -> None:
    """Run the bot."""
    # IMPORTANT: Replace this with your actual bot token
    application = Application.builder().token("8019094318:AAEmddZML-7377C5LZFAhuof4LPTOjUHdzM").build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("poll", poll))
    application.add_handler(PollHandler(receive_poll_update))

    application.run_polling()

if __name__ == '__main__':
    main()