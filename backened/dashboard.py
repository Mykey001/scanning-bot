# dashboard.py
import os
import json
import sqlite3
import datetime
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from flask_caching import Cache
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

app = Flask(__name__)
app.config['SECRET_KEY'] = 'crypto_sentiment_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# Setup caching
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 60})

# Database connection with connection pooling
engine = create_engine('sqlite:///crypto_data.db', poolclass=QueuePool, connect_args={"check_same_thread": False})

def get_db_connection():
    return engine.connect()

# Convert row to dict
def row_to_dict(row):
    return {key: value for key, value in row.items()}

# Route for main dashboard
@app.route('/')
def index():
    return render_template('index.html')

# API route to get initial data with caching
@app.route('/api/initial-data')
@cache.cached(timeout=30)  # Cache for 30 seconds
def initial_data():
    with get_db_connection() as conn:
        # Get latest messages - using more efficient query
        result = conn.execute(text('''
            SELECT platform, content, sentiment, datetime(timestamp, 'unixepoch') as datetime_str
            FROM (
                SELECT * FROM messages ORDER BY timestamp DESC LIMIT 100
            ) sub
            ORDER BY timestamp ASC
        '''))


        
        # Convert to list of dicts
        message_list = []
        for row in result:
            row_dict = row_to_dict(row)
            dt_str = row_dict['datetime_str']
            dt = datetime.datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            timestamp = dt.timestamp()
            
            message_list.append({
                'platform': row_dict['platform'],
                'content': row_dict['content'],
                'sentiment': row_dict['sentiment'],
                'timestamp': timestamp
            })
        
        # Get stats for the past day
        now = datetime.datetime.now()
        past_day = now - datetime.timedelta(days=1)
        past_day_timestamp = past_day.timestamp()
        
        result = conn.execute(text('''
            SELECT 
                AVG(sentiment) as avg_sentiment,
                MAX(sentiment) as max_sentiment,
                MIN(sentiment) as min_sentiment,
                COUNT(*) as total_messages
            FROM messages
            WHERE timestamp > :past_day
        '''), {"past_day": past_day_timestamp})
        
        stats_row = row_to_dict(result.fetchone())
        
        # Platform counts
        telegram_count = conn.execute(text('SELECT COUNT(*) as count FROM messages WHERE platform = "Telegram"')).fetchone()['count']
        discord_count = conn.execute(text('SELECT COUNT(*) as count FROM messages WHERE platform = "Discord"')).fetchone()['count']
        
        # Get hourly trend data for visualization
        result = conn.execute(text('''
            SELECT 
                strftime('%Y-%m-%d %H:00:00', datetime(timestamp, 'unixepoch')) as hour,
                AVG(sentiment) as avg_sentiment,
                COUNT(*) as message_count
            FROM messages
            WHERE timestamp > :past_day
            GROUP BY hour
            ORDER BY hour ASC
        '''), {"past_day": past_day_timestamp})
        
        hourly_data = []
        for row in result:
            row_dict = row_to_dict(row)
            hour_dt = datetime.datetime.strptime(row_dict['hour'], '%Y-%m-%d %H:%M:%S')
            hourly_data.append({
                'hour': hour_dt.timestamp(),
                'avg_sentiment': row_dict['avg_sentiment'],
                'message_count': row_dict['message_count']
            })
    
    return jsonify({
        'messages': message_list,
        'stats': {
            'avgSentiment': stats_row['avg_sentiment'] if stats_row['avg_sentiment'] is not None else 0,
            'maxSentiment': stats_row['max_sentiment'] if stats_row['max_sentiment'] is not None else 0,
            'minSentiment': stats_row['min_sentiment'] if stats_row['min_sentiment'] is not None else 0,
            'totalMessages': stats_row['total_messages'],
            'telegramCount': telegram_count,
            'discordCount': discord_count
        },
        'hourlyData': hourly_data
    })

# Route for paginated messages
@app.route('/api/messages')
def get_messages():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    platform = request.args.get('platform', None)
    sentiment = request.args.get('sentiment', None)
    
    offset = (page - 1) * per_page
    
    with get_db_connection() as conn:
        # Build the query with optional filters
        query = '''
            SELECT platform, content, sentiment, datetime(timestamp, 'unixepoch') as datetime_str
            FROM messages 
            WHERE 1=1
        '''
        params = {}
        
        if platform:
            query += " AND platform = :platform"
            params['platform'] = platform
            
        if sentiment:
            if sentiment == 'positive':
                query += " AND sentiment > 0.2"
            elif sentiment == 'negative':
                query += " AND sentiment < -0.2"
            elif sentiment == 'neutral':
                query += " AND sentiment BETWEEN -0.2 AND 0.2"
        
        # Get total count
        count_query = f"SELECT COUNT(*) as count FROM messages WHERE 1=1"
        if platform:
            count_query += " AND platform = :platform"
        if sentiment:
            if sentiment == 'positive':
                count_query += " AND sentiment > 0.2"
            elif sentiment == 'negative':
                count_query += " AND sentiment < -0.2"
            elif sentiment == 'neutral':
                count_query += " AND sentiment BETWEEN -0.2 AND 0.2"
                
        total_result = conn.execute(text(count_query), params)
        total = total_result.fetchone()['count']
        
        # Finalize query for pagination
        query += " ORDER BY timestamp DESC LIMIT :per_page OFFSET :offset"
        params['per_page'] = per_page
        params['offset'] = offset
        
        # Execute the query
        result = conn.execute(text(query), params)
        
        # Process results
        message_list = []
        for row in result:
            row_dict = row_to_dict

# Add these routes to your dashboard.py file

@app.route('/api/emit-message', methods=['POST'])
def emit_message():
    message_data = request.json
    socketio.emit('new_message', message_data)
    return jsonify({'status': 'success'})

@app.route('/api/emit-trend', methods=['POST'])
def emit_trend():
    trend_data = request.json
    
    