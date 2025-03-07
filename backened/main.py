import os
import asyncio
import sqlite3
import numpy as np
import matplotlib.pyplot as plt
from collections import deque
from telethon import TelegramClient, events
import discord
from discord.ext import commands
from textblob import TextBlob
from sklearn.linear_model import Ridge
import datetime
import re
from scipy import stats
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from sqlalchemy import create_engine, Column, Integer, Float, String, Text, DateTime, text
from sqlalchemy.orm import declarative_base  # Updated import for SQLAlchemy 2.0
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

# Environment variables
from dotenv import load_dotenv
load_dotenv()

# Credential section
TELEGRAM_API_ID = int(os.getenv('TELEGRAM_API_ID'))
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# Database setup with SQLAlchemy - updated for SQLAlchemy 2.0
Base = declarative_base()  # Using updated import
engine = create_engine('sqlite:///crypto_data.db', poolclass=QueuePool, connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine)

class Message(Base):
    __tablename__ = 'messages'
    
    id = Column(Integer, primary_key=True)
    platform = Column(String(50))
    content = Column(Text)
    sentiment = Column(Float)
    timestamp = Column(DateTime, default=datetime.datetime.now)

Base.metadata.create_all(engine)

# Initialize the clients
tg_client = TelegramClient('crypto_bot_session', TELEGRAM_API_ID, TELEGRAM_API_HASH)
discord_bot = commands.Bot(command_prefix='!', intents=discord.Intents.all())

# Sentiment analysis setup
TREND_WINDOW = 30
sentiment_history = deque(maxlen=TREND_WINDOW)
time_stamps = deque(maxlen=TREND_WINDOW)

# Pre-compile keyword regex for efficiency
keyword_pattern = re.compile(r'\b(100x|whale|liquidity|memecoin|tokenomics|bullish|bearish|moon|dump|rug|scam|gem|pump)\b', re.IGNORECASE)

# Initialize sentiment analyzers
vader = SentimentIntensityAnalyzer()

# Add crypto-specific lexicon to VADER
crypto_lexicon = {
    "whale": 3.0,
    "moon": 3.0,
    "dump": -3.0,
    "fud": -2.5,
    "bullish": 3.0,
    "bearish": -3.0,
    "100x": 4.0,
    "rug": -4.0,
    "scam": -4.0,
    "gem": 3.5,
    "pump": 3.0,
    "memecoin": 2.0,
    "tokenomics": 1.0,
    "liquidity": 1.5
}
vader.lexicon.update(crypto_lexicon)

# Initialize ML Model
model = Ridge(alpha=1.0)

# Message queue for batch processing
message_queue = []
MAX_QUEUE_SIZE = 10
queue_lock = asyncio.Lock()

# Enhanced sentiment analysis
def analyze_message(message):
    if keyword_pattern.search(message.lower()):
        # Use both TextBlob and VADER for better accuracy
        textblob_sentiment = TextBlob(message).sentiment.polarity
        vader_sentiment = vader.polarity_scores(message)['compound']
        
        # Weight VADER higher for crypto content
        combined_sentiment = (textblob_sentiment + (vader_sentiment * 2)) / 3
        return True, combined_sentiment
    return False, None

# Batch message processing
async def queue_message(platform, message, sentiment):
    timestamp = datetime.datetime.now()
    async with queue_lock:
        message_queue.append((platform, message, sentiment, timestamp))
        
        # Emit to Socket.IO for real-time dashboard updates
        try:
            import requests
            requests.post('http://localhost:5000/api/emit-message', json={
                'platform': platform,
                'content': message[:150] + ('...' if len(message) > 150 else ''),
                'sentiment': sentiment,
                'timestamp': timestamp.timestamp()
            })
        except Exception as e:
            print(f"Error emitting message: {e}")
            
        if len(message_queue) >= MAX_QUEUE_SIZE:
            await process_message_queue()

async def process_message_queue():
    global message_queue
    if not message_queue:
        return
        
    async with queue_lock:
        session = Session()
        try:
            for platform, content, sentiment_value, timestamp in message_queue:
                message = Message(
                    platform=platform,
                    content=content,
                    sentiment=sentiment_value,
                    timestamp=timestamp
                )
                session.add(message)
                
                # Add to the trend tracking collections
                sentiment_history.append(sentiment_value)
                time_stamps.append(timestamp.timestamp())
                
            session.commit()
            message_queue = []
            
            # Check for trends after batch processing
            if len(sentiment_history) == TREND_WINDOW:
                await check_trend()
                
        except Exception as e:
            session.rollback()
            print(f"Error processing message queue: {e}")
        finally:
            session.close()

# Enhanced trend detection
async def check_trend():
    if len(sentiment_history) < TREND_WINDOW:
        return
        
    # Convert deques to numpy arrays for faster processing
    y = np.array(sentiment_history)
    X = np.array(time_stamps).reshape(-1, 1)
    
    # Fit linear model for trend slope
    model.fit(X, y)
    trend_slope = model.coef_[0]
    
    # Statistical significance testing
    correlation, p_value = stats.pearsonr(np.squeeze(X), y)
    avg_sentiment = np.mean(y)
    std_dev = np.std(y)
    
    # Only consider statistically significant trends (p < 0.05)
    significant_trend = p_value < 0.05 and abs(correlation) > 0.3
    
    # Determine if trend is strong enough to alert
    trend_strength = abs(trend_slope) * (1 - p_value)
    
    if significant_trend and trend_strength > 0.01:
        if trend_slope > 0 and avg_sentiment > 0.2:
            message = (f"🚀 POSITIVE TREND DETECTED!\n"
                       f"Confidence: {(1-p_value)*100:.1f}%\n"
                       f"Avg Sentiment: {avg_sentiment:.2f}\n"
                       f"Trend Slope: {trend_slope:.4f}\n"
                       f"Volatility: {std_dev:.2f}")
            await alert_users(message, "positive")
        elif trend_slope < 0 and avg_sentiment < -0.2:
            message = (f"⚠️ NEGATIVE TREND DETECTED!\n"
                       f"Confidence: {(1-p_value)*100:.1f}%\n"
                       f"Avg Sentiment: {avg_sentiment:.2f}\n"
                       f"Trend Slope: {trend_slope:.4f}\n"
                       f"Volatility: {std_dev:.2f}")
            await alert_users(message, "negative")
        
        visualize_trend(trend_slope, avg_sentiment, std_dev)

# Enhanced visualization
def visualize_trend(slope, avg_sentiment, std_dev):
    timestamps_list = list(time_stamps)
    sentiment_list = list(sentiment_history)
    
    # Convert timestamps to datetime for better x-axis
    dates = [datetime.datetime.fromtimestamp(ts) for ts in timestamps_list]
    
    plt.figure(figsize=(12, 6))
    
    # Plot the data points
    plt.plot(dates, sentiment_list, marker='o', linestyle='-', color='b', alpha=0.7, label='Sentiment')
    
    # Plot the trend line
    x_line = np.array([dates[0], dates[-1]])
    y_line = model.predict(np.array([timestamps_list[0], timestamps_list[-1]]).reshape(-1, 1))
    plt.plot(x_line, y_line, 'r--', linewidth=2, label=f'Trend (slope={slope:.4f})')
    
    # Plot the average sentiment
    plt.axhline(y=avg_sentiment, color='g', linestyle=':', label=f'Avg={avg_sentiment:.2f}')
    
    # Plot confidence bands (±1 std dev)
    plt.fill_between(dates, 
                     np.array(sentiment_list) - std_dev,
                     np.array(sentiment_list) + std_dev,
                     color='gray', alpha=0.2, label=f'±1 StdDev ({std_dev:.2f})')
    
    plt.xlabel('Time')
    plt.ylabel('Sentiment Score')
    plt.title('Crypto Sentiment Analysis Trend')
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    # Format x-axis to show time properly
    plt.gcf().autofmt_xdate()
    
    plt.tight_layout()
    plt.savefig('trend_plot.png', dpi=300)
    plt.close()

# Enhanced alert function
async def alert_users(message, trend_type):
    try:
        # Discord alert
        for guild in discord_bot.guilds:
            channel = discord.utils.get(guild.channels, name='alerts')
            if channel:
                # Create rich embed for Discord
                embed = discord.Embed(
                    title="Crypto Sentiment Alert",
                    description=message,
                    color=discord.Color.green() if trend_type == "positive" else discord.Color.red()
                )
                embed.set_image(url="attachment://trend_plot.png")
                embed.set_footer(text=f"Generated at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                
                await channel.send(embed=embed, file=discord.File('trend_plot.png'))
                break
        
        # Telegram alert
        target_group = os.getenv('TELEGRAM_GROUP_ID', 'your_telegram_group')
        await tg_client.send_message(target_group, message)
        await tg_client.send_file(target_group, 'trend_plot.png', caption="Sentiment Trend Visualization")
        
        # Add Socket.IO event for dashboard
        with open('trend_plot.png', 'rb') as f:
            plot_data = f.read()
        
        # Extract trend data from message
        confidence_match = re.search(r'Confidence: ([\d\.]+)%', message)
        avg_sentiment_match = re.search(r'Avg Sentiment: ([-\d\.]+)', message)
        trend_slope_match = re.search(r'Trend Slope: ([-\d\.]+)', message)
        
        trend_data = {
            'type': trend_type,
            'confidence': float(confidence_match.group(1)) if confidence_match else 0,
            'avg_sentiment': float(avg_sentiment_match.group(1)) if avg_sentiment_match else 0,
            'trend_slope': float(trend_slope_match.group(1)) if trend_slope_match else 0,
            'plot': plot_data.hex()  # Convert binary to hex for transmission
        }
        
        # Emit to Socket.IO (using a request to the dashboard server)
        import requests
        requests.post('http://localhost:5000/api/emit-trend', json=trend_data)
        
    except Exception as e:
        print(f"Error sending alert: {e}")

# Telegram message handler
@tg_client.on(events.NewMessage)
async def telegram_handler(event):
    message = event.message.message
    match, sentiment = analyze_message(message)
    if match:
        await queue_message("Telegram", message, sentiment)
        print(f"[Telegram] {message[:50]}... | Sentiment: {sentiment:.2f}")

# Discord message handler
@discord_bot.event
async def on_message(message):
    if message.author == discord_bot.user:
        return
    
    match, sentiment = analyze_message(message.content)
    if match:
        await queue_message("Discord", message.content, sentiment)
        print(f"[Discord] {message.content[:50]}... | Sentiment: {sentiment:.2f}")
    
    await discord_bot.process_commands(message)

# Background task to regularly process any queued messages that didn't reach the batch size
async def process_queued_messages():
    while True:
        await process_message_queue()
        await asyncio.sleep(30)  # Process every 30 seconds regardless of queue size

# Main run function
async def main():
    # Start the background processing task
    asyncio.create_task(process_queued_messages())
    
    # Create tasks for both clients
    task1 = asyncio.create_task(tg_client.start())
    task2 = asyncio.create_task(discord_bot.start(DISCORD_TOKEN))
    
    # Wait for both tasks to complete
    await asyncio.gather(task1, task2)

# Entry point
if __name__ == "__main__":
    try:
        # Create database indices for performance
        with engine.connect() as conn:
            # Fixed: Using text() function for raw SQL execution
            conn.execute(text('CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)'))
            conn.execute(text('CREATE INDEX IF NOT EXISTS idx_platform ON messages(platform)'))
        
        # Run the main async function
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped manually")
    except Exception as e:
        print(f"Error in main: {e}")