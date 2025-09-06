from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_mysqldb import MySQL
import MySQLdb.cursors
import re
from datetime import datetime, date, timedelta
from collections import defaultdict
import random
import time

app = Flask(__name__)

# MySQL Config
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = ''
app.config['MYSQL_PORT'] = 3306
app.config['MYSQL_DB'] = 'GottaCatchemAll'
app.config['MYSQL_CURSORCLASS'] = 'DictCursor'

mysql = MySQL(app)

# Battle queue and ongoing battles (in-memory storage for simplicity)
battle_queue = {}
ongoing_battles = {}

# Root route
@app.route('/')
def home():
    return redirect(url_for('login'))

# Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST' and 'email' in request.form and 'password' in request.form:
        email = request.form['email']
        password = request.form['password']

        cursor = mysql.connection.cursor()
        cursor.execute('SELECT * FROM Users WHERE email = %s AND password = %s', (email, password))
        account = cursor.fetchone()

        if account:
            session['user_id'] = account['user_id']
            session['name'] = account['name']

            # --- Daily reward logic ---
            today = date.today()
            if not account.get('last_login') or account['last_login'] < today:
                daily_reward = 100.00
                new_balance = float(account['balance']) + daily_reward

                # Update balance and last_login in DB
                cursor.execute(
                    'UPDATE Users SET balance = %s, last_login = %s WHERE user_id = %s',
                    (new_balance, today, account['user_id'])
                )
                mysql.connection.commit()
                flash(f'You received {daily_reward} coins as a daily login reward!', 'success')

            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email/password!', 'danger')

    return render_template('login.html')

# Register route
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST' and 'name' in request.form and 'email' in request.form and 'password' in request.form:
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        cursor = mysql.connection.cursor()
        cursor.execute('SELECT * FROM Users WHERE email = %s', (email,))
        account = cursor.fetchone()

        if account:
            flash('Account already exists!', 'warning')
        elif not re.match(r'[^@]+@[^@]+\.[^@]+', email):
            flash('Invalid email address!', 'danger')
        elif password != confirm_password:
            flash('Passwords do not match!', 'danger')
        else:
            cursor.execute('INSERT INTO Users (name, email, password) VALUES (%s, %s, %s)', (name, email, password))
            mysql.connection.commit()
            flash('You have successfully registered!', 'success')
            return redirect(url_for('login'))
    return render_template('register.html')

# Logout route
@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out successfully!', 'success')
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    if 'user_id' in session:
        cursor = mysql.connection.cursor()

        # Fetch user info
        cursor.execute('SELECT * FROM Users WHERE user_id = %s', (session['user_id'],))
        user = cursor.fetchone()

        # Example: count cards, trades, auctions, battles
        cursor.execute('SELECT COUNT(*) AS card_count FROM Card WHERE owner_id = %s', (session['user_id'],))
        cards = cursor.fetchone()['card_count']

        cursor.execute('SELECT COUNT(*) AS trade_count FROM participates_in WHERE user_id = %s', (session['user_id'],))
        trades = cursor.fetchone()['trade_count']


        cursor.execute('SELECT COUNT(*) AS auction_count FROM Auction WHERE user_id = %s', (session['user_id'],))
        auctions = cursor.fetchone()['auction_count']
        
        cursor.execute('''
            SELECT COUNT(*) AS live_auctions 
            FROM Auction 
            WHERE user_id = %s 
            AND end_time > NOW()
        ''', (session['user_id'],))
        result = cursor.fetchone()
        live_auctions = result['live_auctions'] if result else 0
        


        # Fetch latest 5 battles for the dashboard
        cursor.execute("""
            SELECT 
                b.battle_id,
                b.date,
                CASE
                    WHEN b.winner = %s THEN u_loser.name
                    ELSE u_winner.name
                END AS opponent,
                CASE
                    WHEN b.winner = %s THEN 'Win'
                    ELSE 'Loss'
                END AS result,
                b.amount AS prize
            FROM Battle b
            JOIN Users u_winner ON u_winner.user_id = b.winner
            JOIN Users u_loser ON u_loser.user_id = b.loser
            WHERE %s IN (b.winner, b.loser)
            ORDER BY b.date DESC
            LIMIT 5
        """, (session['user_id'], session['user_id'], session['user_id']))
        battles = cursor.fetchall()

        # Total battles count (separate query)
        cursor.execute("""
            SELECT COUNT(*) AS battle_count
            FROM Battle
            WHERE %s IN (winner, loser)
        """, (session['user_id'],))
        row = cursor.fetchone()
        battles_count = row['battle_count'] if row else 0

        # Fetch user info
        cursor.execute('SELECT * FROM Users')
        allusers = cursor.fetchall()

        # Fetch user's notifications (latest 5 for dashboard)
        cursor.execute("""
            SELECT notification_id, type, title, message, related_id, is_read, created_at
            FROM notifications
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 5
        """, (session['user_id'],))
        notifications = cursor.fetchall()

        # Get unread notifications count
        cursor.execute("""
            SELECT COUNT(*) as unread_count
            FROM notifications
            WHERE user_id = %s AND is_read = 0
        """, (session['user_id'],))
        unread_count = cursor.fetchone()['unread_count']

        # Fetch real active trades (first 2) for dashboard display - available trades from other users
        cursor.execute("""
            SELECT t.trade_id, t.description, t.start_time,
                   c.name as card_name, u.name as trader_name,
                   (SELECT CONCAT(offered_card.name, ' + $', COALESCE(MAX(to2.additional_money), 0))
                    FROM trade_offers to2 
                    JOIN card offered_card ON to2.offered_card_id = offered_card.card_id
                    WHERE to2.trade_id = t.trade_id
                    ORDER BY to2.additional_money DESC, to2.timestamp DESC LIMIT 1) as best_offer
            FROM trade t
            JOIN card c ON t.card_id = c.card_id
            JOIN users u ON t.user_id = u.user_id
            WHERE t.user_id != %s AND t.end_time > NOW() AND c.owner_id = u.user_id
            ORDER BY t.start_time DESC
            LIMIT 2
        """, (session['user_id'],))
        dashboard_trades = cursor.fetchall()

        # Fetch real live auctions (first 2) for dashboard display
        cursor.execute("""
            SELECT a.auction_id, a.starting_bid, a.end_time,
                   c.name as card_name,
                   (SELECT MAX(bid_amount) FROM bids_in WHERE auction_id = a.auction_id) as current_bid,
                   (SELECT COUNT(*) FROM bids_in WHERE auction_id = a.auction_id AND user_id = %s) as user_bid_count
            FROM auction a
            JOIN card c ON a.card_id = c.card_id
            WHERE a.end_time > NOW()
            ORDER BY a.end_time ASC
            LIMIT 2
        """, (session['user_id'],))
        dashboard_auctions = cursor.fetchall()

        # Check for new wishlist notifications for existing active listings
        notify_wishlist_users_for_existing_listings()

        return render_template(
            'dashboard.html',
            allusers=allusers,
            user=user,
            cards=cards,
            trades=trades,
            auctions=auctions,
            live_auctions=live_auctions,
            battles=battles,
            battles_count=battles_count,
            notifications=notifications,
            unread_count=unread_count,
            dashboard_trades=dashboard_trades,
            dashboard_auctions=dashboard_auctions
        )
    else:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))


@app.route('/battle-history')
def battle_history():
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))

    user_id = session['user_id']
    cursor = mysql.connection.cursor()

    # Check if user is in queue by looking at challenge table
    cursor.execute("""
        SELECT COUNT(*) as in_queue 
        FROM challenge 
        WHERE user_id = %s AND status = 'queued'
    """, (user_id,))
    in_queue_result = cursor.fetchone()
    in_queue = in_queue_result['in_queue'] > 0 if in_queue_result else False

    # Check for ongoing battle in database
    cursor.execute("""
        SELECT b.battle_id, b.winner, b.loser, b.date, b.status,
               b.player1_score, b.player2_score, b.current_turn, b.current_move,
               u1.name as winner_name, u2.name as loser_name,
               c1.card_id as winner_card_id, c1.name as winner_pokemon,
               c2.card_id as loser_card_id, c2.name as loser_pokemon
        FROM Battle b
        JOIN Users u1 ON b.winner = u1.user_id
        JOIN Users u2 ON b.loser = u2.user_id
        JOIN challenge ch1 ON b.battle_id = ch1.battle_id AND ch1.user_id = b.winner
        JOIN challenge ch2 ON b.battle_id = ch2.battle_id AND ch2.user_id = b.loser
        JOIN card c1 ON ch1.card_id = c1.card_id
        JOIN card c2 ON ch2.card_id = c2.card_id
        WHERE b.status = 'ongoing' AND %s IN (b.winner, b.loser)
    """, (user_id,))
    
    db_battle = cursor.fetchone()
    
    current_battle = None
    if db_battle:
        # Create battle data structure similar to our in-memory battles
        battle_id = db_battle['battle_id']
        
        # Determine if current user is winner or loser
        if db_battle['winner'] == user_id:
            user_pokemon = db_battle['winner_pokemon']
            opponent_pokemon = db_battle['loser_pokemon']
            opponent_id = db_battle['loser']
            opponent_name = db_battle['loser_name']
            user_score = db_battle['player1_score']
            opponent_score = db_battle['player2_score']
        else:
            user_pokemon = db_battle['loser_pokemon']
            opponent_pokemon = db_battle['winner_pokemon']
            opponent_id = db_battle['winner']
            opponent_name = db_battle['winner_name']
            user_score = db_battle['player2_score']
            opponent_score = db_battle['player1_score']
        
        # Get current turn username
        cursor.execute("SELECT name FROM users WHERE user_id = %s", (db_battle['current_turn'],))
        current_turn_user = cursor.fetchone()
        current_turn_name = current_turn_user['name'] if current_turn_user else "Unknown"
        
        # Create battle data structure
        current_battle = {
            'battle_id': battle_id,
            'user_pokemon': user_pokemon,
            'opponent_pokemon': opponent_pokemon,
            'username': session['name'],
            'opponent': opponent_name,
            'user_score': user_score,
            'opponent_score': opponent_score,
            'current_turn': current_turn_name,
            'current_move': db_battle['current_move'] or 'None',
            'is_users_turn': db_battle['current_turn'] == user_id  # ADDED THIS LINE
        }
        
        # Also add to ongoing_battles for the Flask app to handle moves
        if battle_id not in ongoing_battles:
            ongoing_battles[battle_id] = {
                'player1': {
                    'user_id': db_battle['winner'],
                    'username': db_battle['winner_name'],
                    'pokemon': db_battle['winner_pokemon'],
                    'score': db_battle['player1_score']
                },
                'player2': {
                    'user_id': db_battle['loser'],
                    'username': db_battle['loser_name'],
                    'pokemon': db_battle['loser_pokemon'],
                    'score': db_battle['player2_score']
                },
                'current_turn': db_battle['current_turn'],
                'current_move': db_battle['current_move'] or 'None',
                'start_time': db_battle['date']
            }

    # Fetch user's cards for selection modal
    cursor.execute("SELECT name FROM Card WHERE owner_id = %s", (user_id,))
    user_cards = cursor.fetchall()

    # Fetch battle history
    cursor.execute("""
        SELECT b.battle_id AS id,
               CASE WHEN b.winner = %s THEN u_loser.name ELSE u_winner.name END AS opponent,
               b.date,
               CASE WHEN b.winner = %s THEN 'Win' ELSE 'Loss' END AS result
        FROM Battle b
        JOIN Users u_winner ON b.winner = u_winner.user_id
        JOIN Users u_loser ON b.loser = u_loser.user_id
        WHERE %s IN (b.winner, b.loser) AND b.status = 'finished'
        ORDER BY b.date DESC
    """, (user_id, user_id, user_id))
    battles = cursor.fetchall()

    return render_template('battle.html', 
                         battles=battles, 
                         current_battle=current_battle,
                         in_queue=in_queue,
                         user_cards=user_cards)

@app.route("/start-battle", methods=["POST"])
def start_battle():
    if "user_id" not in session:
        return redirect("/login")
    
    user_id = session["user_id"]
    username = session["name"]
    pokemon = request.form.get("pokemon")
    
    if not pokemon:
        print("Please select a Pokémon to battle with!", "danger")
        return redirect(url_for("battle_history"))
    
    cursor = mysql.connection.cursor()
    
    # Check if user is already in a battle or queue
    cursor.execute("""
        SELECT b.status 
        FROM battle b
        JOIN challenge c ON b.battle_id = c.battle_id
        WHERE c.user_id = %s AND b.status IN ('ongoing', 'queued')
    """, (user_id,))
    existing_battle = cursor.fetchone()
    
    if existing_battle:
        print("You are already in a battle or queue!", "warning")
        return redirect(url_for("battle_history"))
    
    # Get the card ID for the selected Pokémon
    cursor.execute("SELECT card_id FROM card WHERE owner_id = %s AND name = %s", (user_id, pokemon))
    card = cursor.fetchone()
    
    if not card:
        print("Invalid Pokémon selection!", "danger")
        return redirect(url_for("battle_history"))
    
    card_id = card['card_id']
    
    # Check if there's someone waiting in the queue
    cursor.execute("""
        SELECT c.user_id, u.name, c.card_id, card.name as pokemon_name
        FROM challenge c
        JOIN users u ON c.user_id = u.user_id
        JOIN card ON c.card_id = card.card_id
        WHERE c.status = 'queued' AND c.user_id != %s
        LIMIT 1
    """, (user_id,))
    waiting_challenge = cursor.fetchone()
    
    if waiting_challenge:
        # Match with the waiting player
        opponent_id = waiting_challenge['user_id']
        opponent_name = waiting_challenge['name']
        opponent_pokemon = waiting_challenge['pokemon_name']
        opponent_card_id = waiting_challenge['card_id']
        
        try:
            # Create a new battle
            cursor.execute("""
                INSERT INTO battle (winner, loser, status, current_turn, current_move, amount)
                VALUES (%s, %s, 'ongoing', %s, 'None', 0.00)
            """, (user_id, opponent_id, user_id))
            battle_id = cursor.lastrowid
            
            # Insert new challenge for current user
            cursor.execute("""
                INSERT INTO challenge (battle_id, user_id, card_id, status)
                VALUES (%s, %s, %s, 'match_found')
            """, (battle_id, user_id, card_id))
            
            # Insert new challenge for opponent
            cursor.execute("""
                INSERT INTO challenge (battle_id, user_id, card_id, status)
                VALUES (%s, %s, %s, 'match_found')
            """, (battle_id, opponent_id, opponent_card_id))
            
            mysql.connection.commit()
            
            print(f"Battle started! You are battling against {opponent_name}", "success")
            
        except Exception as e:
            mysql.connection.rollback()
            print(f"Error creating battle: {e}")
            print("Error starting battle. Please try again.", "danger")
            return redirect(url_for("battle_history"))
    
    else:
        
        #dd to queue
        try:
            # Create a new battle row with status 'queued'
            cursor.execute("""
                INSERT INTO battle (winner, loser, status, current_turn, current_move, amount)
                VALUES (%s, NULL, 'queued', %s, 'None', 0.00)
            """, (user_id, user_id))
            battle_id = cursor.lastrowid

            # Insert challenge with battle_id
            cursor.execute("""
                INSERT INTO challenge (battle_id, user_id, card_id, status)
                VALUES (%s, %s, %s, 'queued')
            """, (battle_id, user_id, card_id))
            mysql.connection.commit()
            
            print("You've been added to the battle queue. Waiting for an opponent...", "info")
            
        except Exception as e:
            mysql.connection.rollback()
            print(f"Error adding to queue: {e}")
            print("Error joining queue. Please try again.", "danger")
            return redirect(url_for("battle_history"))
    
    return redirect(url_for("battle_history"))

@app.route("/cancel-queue", methods=["POST"])
def cancel_queue():
    if "user_id" not in session:
        return redirect("/login")
    
    user_id = session["user_id"]
    cursor = mysql.connection.cursor()
    
    try:
        # Check if user is in the queue (has a challenge with status 'queued')
        cursor.execute("""
            SELECT battle_id FROM challenge 
            WHERE user_id = %s AND status = 'queued'
        """, (user_id,))
        queue_entry = cursor.fetchone()
        
        if queue_entry:
            battle_id = queue_entry['battle_id']
            
            # Delete the challenge entry
            cursor.execute("""
                DELETE FROM challenge 
                WHERE user_id = %s AND status = 'queued'
            """, (user_id,))
            
            # Also delete the battle entry if it exists and has status 'queued'
            cursor.execute("""
                DELETE FROM battle 
                WHERE battle_id = %s AND status IN ('queued', ' ')
            """, (battle_id,))
            
            mysql.connection.commit()
            flash("You've been removed from the battle queue.", "info")
        else:
            flash("You are not in the battle queue.", "warning")
            
    except Exception as e:
        mysql.connection.rollback()
        print(f"Error removing from queue: {e}")
        flash("Error removing from queue. Please try again.", "danger")
    
    return redirect(url_for("battle_history"))

@app.route("/make-move", methods=["POST"])
def make_move():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Not logged in"})
    
    user_id = session["user_id"]
    username = session["name"]
    data = request.get_json()
    battle_id = data.get("battle_id")
    move = data.get("move")
    
    if not battle_id or not move:
        return jsonify({"success": False, "message": "Invalid request"})
    
    battle_id = int(battle_id)
    
    # Check if battle exists in database
    cursor = mysql.connection.cursor()
    cursor.execute("""
        SELECT b.*, u1.name as winner_name, u2.name as loser_name
        FROM battle b
        JOIN users u1 ON b.winner = u1.user_id
        JOIN users u2 ON b.loser = u2.user_id
        WHERE b.battle_id = %s AND b.status = 'ongoing'
    """, (battle_id,))
    db_battle = cursor.fetchone()
    
    if not db_battle:
        return jsonify({"success": False, "message": "Battle not found"})
    
    # Check if user is in this battle
    if user_id not in [db_battle['winner'], db_battle['loser']]:
        return jsonify({"success": False, "message": "You are not in this battle"})
    
    # Check if it's the user's turn
    if db_battle['current_turn'] != user_id:
        return jsonify({"success": False, "message": "It's not your turn"})
    
    # Determine opponent ID
    opponent_id = db_battle['loser'] if db_battle['winner'] == user_id else db_battle['winner']
    
    # Process move and calculate damage
    damage = 0
    if move == "attack":
        damage = random.randint(10, 20)
    elif move == "special":
        damage = random.randint(15, 30)
    # Defend move doesn't do damage
    elif move == "defend":
        damage = 5

    # Announce winner if anyone's score reaches 100
    if (db_battle['player1_score'] + (damage if db_battle['winner'] == user_id else 0)) >= 100:
        winner_id = db_battle['winner']
        loser_id = db_battle['loser']
    elif (db_battle['player2_score'] + (damage if db_battle['loser'] == user_id else 0)) >= 100:
        winner_id = db_battle['loser']
        loser_id = db_battle['winner']
    else:
        winner_id = None
        loser_id = None
    # Update scores in database
    # Check opponent's last move
    cursor.execute("SELECT current_move FROM battle WHERE battle_id = %s", (battle_id,))
    last_move_row = cursor.fetchone()
    if last_move_row and last_move_row['current_move'] == "defend":
        damage = 0
    if db_battle['winner'] == user_id:
        new_score = db_battle['player1_score'] + damage
        cursor.execute("""
            UPDATE battle SET player1_score = %s, current_move = %s 
            WHERE battle_id = %s
        """, (new_score, move, battle_id))
    else:
        new_score = db_battle['player2_score'] + damage
        cursor.execute("""
            UPDATE battle SET player2_score = %s, current_move = %s 
            WHERE battle_id = %s
        """, (new_score, move, battle_id))
    
    # Switch turns to opponent
    cursor.execute("""
        UPDATE battle SET current_turn = %s WHERE battle_id = %s
    """, (opponent_id, battle_id))
    
    mysql.connection.commit()
    
    # Check for win condition
    cursor.execute("SELECT player1_score, player2_score FROM battle WHERE battle_id = %s", (battle_id,))
    scores = cursor.fetchone()
    
    if scores['player1_score'] >= 100 or scores['player2_score'] >= 100:
        # Determine winner and loser
        if scores['player1_score'] >= 100:
            winner_id = db_battle['winner']
            loser_id = db_battle['loser']
        else:
            winner_id = db_battle['loser']
            loser_id = db_battle['winner']
        
        # Update battle status to finished
        cursor.execute("""
            UPDATE battle SET status = 'finished', winner = %s, loser = %s 
            WHERE battle_id = %s
        """, (winner_id, loser_id, battle_id))
        
        # Update challenge status
        cursor.execute("""
            UPDATE challenge SET status = 'completed' WHERE battle_id = %s
        """, (battle_id,))
        
        # Get the card IDs for both players
        cursor.execute("""
            SELECT c.user_id, c.card_id 
            FROM challenge c 
            JOIN card ON c.card_id = card.card_id 
            WHERE c.battle_id = %s
        """, (battle_id,))
        
        battle_cards = cursor.fetchall()
        winner_card_id = None
        loser_card_id = None
        
        for card_info in battle_cards:
            if card_info['user_id'] == winner_id:
                winner_card_id = card_info['card_id']
            elif card_info['user_id'] == loser_id:
                loser_card_id = card_info['card_id']
        
        # Directly transfer rewards instead of using accepted_battles table
        # Award coins to winner and deduct from loser
        cursor.execute("""
            UPDATE users 
            SET balance = balance + 200 
            WHERE user_id = %s
        """, (winner_id,))
        
        cursor.execute("""
            UPDATE users 
            SET balance = GREATEST(0, balance - 50) 
            WHERE user_id = %s
        """, (loser_id,))
        
        # Transfer loser's card to the winner
        cursor.execute("""
            UPDATE card 
            SET owner_id = %s 
            WHERE card_id = %s
        """, (winner_id, loser_card_id))
        
        mysql.connection.commit()
        
        # Remove from ongoing battles if it exists there
        if battle_id in ongoing_battles:
            del ongoing_battles[battle_id]
        
        # Determine if current user won or lost
        if winner_id == user_id:
            flash("You won the battle and earned 200 coins!", "success")
        else:
            flash("You lost the battle and lost 50 coins.", "warning")
            
        return jsonify({"success": True, "battle_over": True})
    
    return jsonify({"success": True, "battle_over": False, "damage": damage})

# in-memory messages (reset on server restart)
live_chats = defaultdict(list)  # key: frozenset({user_id, recipient_id}), value: list of messages

@app.route('/chatbox', methods=['GET', 'POST'])
def chatbox():
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))

    user_id = session['user_id']
    recipient_id = request.args.get('recipient_id', type=int)

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Fetch current user info
    cursor.execute('SELECT * FROM Users WHERE user_id = %s', (user_id,))
    user = cursor.fetchone()

    recipient = None
    messages = []
    
    print('recipient_id: ', recipient_id)
    print('user_id: ', user_id)
    if recipient_id and recipient_id != user_id:  # Prevent self-chat
        # Fetch recipient info
        cursor.execute('SELECT * FROM Users WHERE user_id = %s', (recipient_id,))
        recipient = cursor.fetchone()

        if recipient:
            # Update current user's chatuser_id and timestamp
            cursor.execute(
                'UPDATE Users SET chatuser_id = %s, timestamp = %s WHERE user_id = %s',
                (recipient_id, datetime.now(), user_id)
            )
            mysql.connection.commit()

            # Check if recipient is also chatting with current user
            if recipient.get('chatuser_id') == user_id:  # Use .get() to avoid KeyError
                # Both are connected → retrieve messages
                chat_key = frozenset({user_id, recipient_id})
                messages = live_chats.get(chat_key, [])
    else:
        print("Cannot chat with yourself!", "warning")
        return redirect(url_for('dashboard'))

    # Fetch all users for sidebar (excluding current user)
    cursor.execute('SELECT user_id, name FROM Users WHERE user_id != %s', (user_id,))
    all_users = cursor.fetchall()

    cursor.close()

    return render_template(
        'chatbox.html',
        user=user,
        recipient=recipient,
        messages=messages,
        all_users=all_users,
        recipient_id=recipient_id
    )

@app.route('/send_message', methods=['POST'])
def send_message():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    sender_id = session['user_id']
    recipient_id = request.form.get('recipient_id')
    content = request.form['content'].strip()

    # Validate input
    if not recipient_id or not content:
        flash("Missing recipient or message content!", "warning")
        return redirect(url_for('chatbox'))
    
    try:
        recipient_id = int(recipient_id)
    except ValueError:
        flash("Invalid recipient ID.", "danger")
        return redirect(url_for('chatbox'))
    
    # Prevent self-messaging
    if sender_id == recipient_id:
        flash("Cannot message yourself!", "warning")
        return redirect(url_for('chatbox', recipient_id=recipient_id))

    # Store message in memory
    chat_key = frozenset({sender_id, recipient_id})
    
    if chat_key not in live_chats:
        live_chats[chat_key] = []
    
    live_chats[chat_key].append({
        'sender_id': sender_id,
        'content': content,
        'timestamp': datetime.now()
    })

    return redirect(url_for('chatbox', recipient_id=recipient_id))


@app.route('/stop_chat')
def stop_chat():
    """Clear current user's chat session and go back to dashboard"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    cursor = mysql.connection.cursor()
    cursor.execute('UPDATE Users SET chatuser_id=NULL WHERE user_id=%s', (user_id,))
    mysql.connection.commit()

    flash("You have left the chat.", "info")
    return redirect(url_for('dashboard'))


@app.route('/my_cards')
def my_cards():
    if "user_id" not in session:
        flash("Please log in first!", "danger")
        return redirect(url_for("login"))

    user_id = session['user_id']

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("""
        SELECT card_id, name, value, normal, golden, holographic
        FROM card
        WHERE owner_id = %s
    """, (user_id,))
    user_cards = cursor.fetchall()
    cursor.close()

    return render_template("cards.html", user_cards=user_cards)

@app.route('/add_card', methods=['POST'])
def add_card():
    if "user_id" not in session:
        flash("Please log in first!", "danger")
        return redirect(url_for("login"))
    
    user_id = session['user_id']
    name = request.form.get('name')
    value = request.form.get('value')
    card_type = request.form.get('type')
    
    try:
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        # Fetch the last card_id and increment by 1
        cursor.execute("SELECT MAX(card_id) AS last_id FROM card")
        last_id_row = cursor.fetchone()
        next_card_id = (last_id_row['last_id'] or 0) + 1

        cursor.execute("""
            INSERT INTO card (card_id, owner_id, name, value, normal, golden, holographic)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (next_card_id, user_id, name, value,
              1 if card_type == 'normal' else 0,
              1 if card_type == 'golden' else 0,
              1 if card_type == 'holographic' else 0))

        mysql.connection.commit()
        cursor.close()
        
        print('Card added successfully!', 'success')
    except Exception as e:
        print(f'Error adding card: {str(e)}', 'danger')
    
    return redirect(url_for('my_cards'))

@app.route('/market')
def market():
    return redirect(url_for('auctions'))

@app.route('/accept_bid', methods=['POST'])
def accept_bid():
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    auction_id = request.form.get('auction_id')
    
    cursor = mysql.connection.cursor()
    
    try:
        # 1. Get auction details and verify ownership
        cursor.execute("""
            SELECT a.*, c.owner_id as card_owner_id, c.name as card_name
            FROM auction a
            JOIN card c ON a.card_id = c.card_id
            WHERE a.auction_id = %s AND a.user_id = %s
        """, (auction_id, session['user_id']))
        auction = cursor.fetchone()
        
        if not auction:
            flash('Auction not found or you are not the owner!', 'danger')
            return redirect(url_for('auctions'))
        
        # 2. Get the highest bid
        cursor.execute("""
            SELECT b.user_id, b.bid_amount, u.name as bidder_name
            FROM bids_in b
            JOIN users u ON b.user_id = u.user_id
            WHERE b.auction_id = %s
            ORDER BY b.bid_amount DESC, b.timestamp DESC
            LIMIT 1
        """, (auction_id,))
        highest_bid = cursor.fetchone()
        
        if not highest_bid:
            flash('No bids found for this auction!', 'warning')
            return redirect(url_for('auctions'))
        
        # 3. Store the accepted bid information in a separate table
        # First check if an accepted bid already exists
        cursor.execute("""
            SELECT * FROM accepted_bids WHERE auction_id = %s
        """, (auction_id,))
        existing_accepted_bid = cursor.fetchone()
        
        if existing_accepted_bid:
            # Update existing record
            cursor.execute("""
                UPDATE accepted_bids 
                SET bidder_id = %s, bid_amount = %s 
                WHERE auction_id = %s
            """, (highest_bid['user_id'], highest_bid['bid_amount'], auction_id))
        else:
            # Create new record
            cursor.execute("""
                INSERT INTO accepted_bids (auction_id, bidder_id, bid_amount)
                VALUES (%s, %s, %s)
            """, (auction_id, highest_bid['user_id'], highest_bid['bid_amount']))
        
        # 4. Create notification for the winner
        create_notification(
            highest_bid['user_id'],
            'auction_won',
            'You Won the Auction!',
            f"You won the auction for {auction['card_name']} with a bid of ${highest_bid['bid_amount']:.2f}. Click 'Received' to complete the transaction and receive your card.",
            auction_id
        )
        
        # 5. Create notification for the seller
        create_notification(
            session['user_id'],
            'auction_accepted',
            'Bid Accepted - Waiting for Confirmation',
            f"You accepted {highest_bid['bidder_name']}'s bid of ${highest_bid['bid_amount']:.2f} for {auction['card_name']}. The transaction will complete when the buyer confirms receipt.",
            auction_id
        )
        
        mysql.connection.commit()
        cursor.close()
        
        flash(f'Bid accepted! Waiting for {highest_bid["bidder_name"]} to confirm receipt.', 'success')
        
    except Exception as e:
        mysql.connection.rollback()
        cursor.close()
        flash('An error occurred while accepting the bid.', 'danger')
        print(f"Error in accept_bid: {str(e)}")
    
    return redirect(url_for('auctions'))

# @app.route('/accept_bid', methods=['POST'])
# def accept_bid():
#     if 'user_id' not in session:
#         flash('Please log in first!', 'danger')
#         return redirect(url_for('login'))
    
#     auction_id = request.form.get('auction_id')
    
#     cursor = mysql.connection.cursor()
    
#     try:
#         # 1. Get auction details and verify ownership
#         cursor.execute("""
#             SELECT a.*, c.owner_id as card_owner_id, c.name as card_name
#             FROM auction a
#             JOIN card c ON a.card_id = c.card_id
#             WHERE a.auction_id = %s AND a.user_id = %s
#         """, (auction_id, session['user_id']))
#         auction = cursor.fetchone()
        
#         if not auction:
#             flash('Auction not found or you are not the owner!', 'danger')
#             return redirect(url_for('auctions'))
        
#         # 2. Get the highest bid
#         cursor.execute("""
#             SELECT b.user_id, b.bid_amount, u.name as bidder_name
#             FROM bids_in b
#             JOIN users u ON b.user_id = u.user_id
#             WHERE b.auction_id = %s
#             ORDER BY b.bid_amount DESC, b.timestamp DESC
#             LIMIT 1
#         """, (auction_id,))
#         highest_bid = cursor.fetchone()
        
#         if not highest_bid:
#             flash('No bids found for this auction!', 'warning')
#             return redirect(url_for('auctions'))
        
#         # 3. Transfer money from bidder to seller
#         # Add money to seller's balance
        
#         # cursor.execute("""
#         #     UPDATE users 
#         #     SET balance = balance - %s 
#         #     WHERE user_id = %s
#         # """, (highest_bid['bid_amount'], highest_bid['user_id']))
        
#         # cursor.execute("""
#         #     UPDATE users 
#         #     SET balance = balance + %s 
#         #     WHERE user_id = %s
#         # """, (highest_bid['bid_amount'], session['user_id']))
        
#         # # 4. Transfer card ownership to the highest bidder
#         # cursor.execute("""
#         #     UPDATE card 
#         #     SET owner_id = %s 
#         #     WHERE card_id = %s
#         # """, (highest_bid['user_id'], auction['card_id']))
        
#         # # 5. End the auction (set end time to now)
#         # cursor.execute("""
#         #     UPDATE auction 
#         #     SET end_time = NOW() 
#         #     WHERE auction_id = %s
#         # """, (auction_id,))
        
# # 6. Create notification for the winner
#         create_notification(
#             highest_bid['user_id'],
#             'auction_won',
#             'You Won the Auction!',
#             f"You won the auction for {auction['card_name']} with a bid of ${highest_bid['bid_amount']:.2f}. Click 'Received' to complete the transaction and receive your card.",
#             auction_id
#         )

#         # 7. Create notification for the seller
#         create_notification(
#             session['user_id'],
#             'auction_accepted',
#             'Bid Accepted - Waiting for Confirmation',
#             f"You accepted {highest_bid['bidder_name']}'s bid of ${highest_bid['bid_amount']:.2f} for {auction['card_name']}. The transaction will complete when the buyer confirms receipt.",
#             auction_id
#         )

#         mysql.connection.commit()
#         cursor.close()

#         flash(f'Bid accepted! Waiting for {highest_bid["bidder_name"]} to confirm receipt.', 'success')
        
#     except Exception as e:
#         mysql.connection.rollback()
#         cursor.close()
#         flash('An error occurred while completing the auction.', 'danger')
#         print(f"Error in accept_bid: {str(e)}")
    
#     return redirect(url_for('auctions'))


@app.route('/auctions')
def auctions():
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))

    cursor = mysql.connection.cursor()
    
    # Fetch active auctions with user and card details
    cursor.execute("""
        SELECT a.auction_id, a.start_time, a.end_time, a.starting_bid, 
               u.user_id as seller_id, u.name as seller_name,
               c.card_id, c.name as card_name, c.value, c.normal, c.golden, c.holographic,
               (SELECT MAX(bid_amount) FROM bids_in WHERE auction_id = a.auction_id) as current_bid,
               (SELECT COUNT(*) FROM accepted_bids WHERE auction_id = a.auction_id) as has_accepted_bid
        FROM auction a
        JOIN users u ON a.user_id = u.user_id
        JOIN card c ON a.card_id = c.card_id
        WHERE a.end_time > NOW() AND c.owner_id = u.user_id
        ORDER BY a.end_time ASC
    """)
    active_auctions = cursor.fetchall()
    
    # Fetch active trades with user and card details
    cursor.execute("""
        SELECT t.trade_id, t.start_time, t.end_time, t.description,
               u.user_id as trader_id, u.name as trader_name,
               c.card_id, c.name as card_name, c.value, c.normal, c.golden, c.holographic,
               (SELECT CONCAT(offered_card.name, ' + $', COALESCE(MAX(to2.additional_money), 0))
                FROM trade_offers to2 
                JOIN card offered_card ON to2.offered_card_id = offered_card.card_id
                WHERE to2.trade_id = t.trade_id
                ORDER BY to2.additional_money DESC, to2.timestamp DESC LIMIT 1) as best_offer
        FROM trade t
        JOIN users u ON t.user_id = u.user_id
        JOIN card c ON t.card_id = c.card_id
        WHERE t.end_time > NOW() AND c.owner_id = u.user_id
        ORDER BY t.end_time ASC
    """)
    active_trades = cursor.fetchall()
    
    # Fetch trade offers for user's own trades
    user_trade_offers = {}
    for trade in active_trades:
        if trade['trader_id'] == session['user_id']:
            cursor.execute("""
                SELECT to2.user_id, to2.additional_money, to2.timestamp, to2.status,
                       u.name as offerer_name, c.name as offered_card_name, c.card_id as offered_card_id
                FROM trade_offers to2
                JOIN users u ON to2.user_id = u.user_id
                JOIN card c ON to2.offered_card_id = c.card_id
                WHERE to2.trade_id = %s
                ORDER BY to2.additional_money DESC, to2.timestamp DESC
            """, (trade['trade_id'],))
            user_trade_offers[trade['trade_id']] = cursor.fetchall()
    
    # Fetch user's cards for creating auctions/trades
    cursor.execute("""
        SELECT card_id, name, value, normal, golden, holographic 
        FROM card 
        WHERE owner_id = %s
    """, (session['user_id'],))
    user_cards = cursor.fetchall()
    
    # Fetch accepted auctions where current user is the winning bidder (for "Received" button)
    cursor.execute("""
        SELECT ab.auction_id, ab.bid_amount, 
            c.name as card_name, u.name as seller_name
        FROM accepted_bids ab
        JOIN auction a ON ab.auction_id = a.auction_id
        JOIN card c ON a.card_id = c.card_id
        JOIN users u ON a.user_id = u.user_id
        WHERE ab.bidder_id = %s AND a.end_time > NOW()
    """, (session['user_id'],))
    accepted_auctions = cursor.fetchall()

    # Fetch accepted trades where current user made an offer (for "Received" button)
    cursor.execute("""
        SELECT t.trade_id, t.user_id as trade_owner_id, to2.status,
               c1.name as requested_card_name, c2.name as offered_card_name,
               u.name as trade_owner_name, to2.additional_money
        FROM trade_offers to2
        JOIN trade t ON to2.trade_id = t.trade_id
        JOIN card c1 ON t.card_id = c1.card_id
        JOIN card c2 ON to2.offered_card_id = c2.card_id
        JOIN users u ON t.user_id = u.user_id
        WHERE to2.user_id = %s AND to2.status = 'accepted' AND t.end_time > NOW()
    """, (session['user_id'],))
    accepted_offers = cursor.fetchall()
    
    # Fetch user info including balance
    cursor.execute('SELECT * FROM users WHERE user_id = %s', (session['user_id'],))
    user = cursor.fetchone()
    user_balance = user['balance']
    
    # Fetch user's own active auctions
    cursor.execute("""
        SELECT a.auction_id, a.start_time, a.end_time, a.starting_bid,
               c.card_id, c.name as card_name, c.value, c.normal, c.golden, c.holographic,
               (SELECT MAX(bid_amount) FROM bids_in WHERE auction_id = a.auction_id) as current_bid,
               (SELECT COUNT(*) FROM bids_in WHERE auction_id = a.auction_id) as bid_count
        FROM auction a
        JOIN card c ON a.card_id = c.card_id
        WHERE a.user_id = %s AND a.end_time > NOW()
        ORDER BY a.end_time ASC
    """, (session['user_id'],))
    user_auctions = cursor.fetchall()
    
    # Fetch user's own active trades
    cursor.execute("""
        SELECT t.trade_id, t.start_time, t.end_time, t.description,
               c.card_id, c.name as card_name, c.value, c.normal, c.golden, c.holographic,
               (SELECT COUNT(*) FROM trade_offers WHERE trade_id = t.trade_id) as offer_count
        FROM trade t
        JOIN card c ON t.card_id = c.card_id
        WHERE t.user_id = %s AND t.end_time > NOW()
        ORDER BY t.end_time ASC
    """, (session['user_id'],))
    user_trades = cursor.fetchall()
    
    cursor.close()
    
    return render_template('Auctiontrade.html', 
                         active_auctions=active_auctions,
                         accepted_auctions=accepted_auctions,
                         active_trades=active_trades,
                         user_trade_offers=user_trade_offers,
                         accepted_offers=accepted_offers,
                         user_cards=user_cards,
                         user_balance=user_balance,
                         user=user,
                         user_auctions=user_auctions,
                         user_trades=user_trades)
    
    
@app.route('/complete_auction', methods=['POST'])
def complete_auction():
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    auction_id = request.form.get('auction_id')
    
    cursor = mysql.connection.cursor()
    
    try:
        # 1. Get the accepted bid details from accepted_bids table
        cursor.execute("""
            SELECT ab.*, a.user_id as seller_id, a.card_id, c.name as card_name
            FROM accepted_bids ab
            JOIN auction a ON ab.auction_id = a.auction_id
            JOIN card c ON a.card_id = c.card_id
            WHERE ab.auction_id = %s AND ab.bidder_id = %s
        """, (auction_id, session['user_id']))
        accepted_bid = cursor.fetchone()
        
        if not accepted_bid:
            flash('Accepted bid not found or you are not the winning bidder!', 'danger')
            return redirect(url_for('auctions'))
        
        # 2. Transfer money from bidder to seller
        cursor.execute("""
            UPDATE users 
            SET balance = balance - %s 
            WHERE user_id = %s
        """, (accepted_bid['bid_amount'], session['user_id']))
        
        cursor.execute("""
            UPDATE users 
            SET balance = balance + %s 
            WHERE user_id = %s
        """, (accepted_bid['bid_amount'], accepted_bid['seller_id']))
        
        # 3. Transfer card ownership to the bidder
        cursor.execute("""
            UPDATE card 
            SET owner_id = %s 
            WHERE card_id = %s
        """, (session['user_id'], accepted_bid['card_id']))
        
        # 4. End the auction and remove the accepted bid record
        cursor.execute("""
            UPDATE auction 
            SET end_time = NOW() 
            WHERE auction_id = %s
        """, (auction_id,))
        
        cursor.execute("""
            DELETE FROM accepted_bids 
            WHERE auction_id = %s
        """, (auction_id,))
        
        mysql.connection.commit()
        cursor.close()
        
        flash(f'Auction completed! You received {accepted_bid["card_name"]} for ${accepted_bid["bid_amount"]:.2f}', 'success')
        
    except Exception as e:
        mysql.connection.rollback()
        cursor.close()
        flash('An error occurred while completing the auction.', 'danger')
        print(f"Error in complete_auction: {str(e)}")
    
    return redirect(url_for('auctions'))

   


@app.route('/create_auction', methods=['POST'])
def create_auction():
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    card_id = request.form.get('card_id')
    starting_bid = request.form.get('starting_bid')
    duration_hours = request.form.get('duration', 24)  # Default 24 hours
    
    try:
        starting_bid = float(starting_bid)
        duration_hours = int(duration_hours)
    except (ValueError, TypeError):
        flash('Invalid input values!', 'danger')
        return redirect(url_for('auctions'))
    
    if starting_bid <= 0:
        flash('Starting bid must be greater than 0!', 'danger')
        return redirect(url_for('auctions'))
    
    cursor = mysql.connection.cursor()
    
    # Verify user owns the card
    cursor.execute('SELECT owner_id FROM card WHERE card_id = %s', (card_id,))
    card = cursor.fetchone()
    
    if not card or card['owner_id'] != session['user_id']:
        flash('You do not own this card!', 'danger')
        return redirect(url_for('auctions'))
    
    # Check if card is already in an active auction
    cursor.execute("""
        SELECT auction_id FROM auction 
        WHERE card_id = %s AND end_time > NOW()
    """, (card_id,))
    existing_auction = cursor.fetchone()
    
    if existing_auction:
        flash('This card is already listed in an active auction!', 'warning')
        return redirect(url_for('auctions'))
    
    # Create the auction
    start_time = datetime.now()
    end_time = start_time + timedelta(hours=duration_hours)
    
    cursor.execute("""
        INSERT INTO auction (start_time, end_time, starting_bid, user_id, card_id)
        VALUES (%s, %s, %s, %s, %s)
    """, (start_time, end_time, starting_bid, session['user_id'], card_id))
    
    auction_id = cursor.lastrowid
    mysql.connection.commit()
    
    # Check if any users have this card in their wishlist and notify them
    cursor.execute("""
        SELECT w.user_id, u.name, c.name as card_name
        FROM wishlist w
        JOIN users u ON w.user_id = u.user_id
        JOIN card c ON w.card_id = c.card_id
        WHERE w.card_id = %s AND w.user_id != %s
    """, (card_id, session['user_id']))
    
    wishlist_users = cursor.fetchall()
    
    for wishlist_user in wishlist_users:
        create_notification(
            wishlist_user['user_id'],
            'wishlist_auction',
            'Wishlist Alert: Card Available at Auction!',
            f"Your wishlisted card '{wishlist_user['card_name']}' is now available at auction by {session['name']}. Starting bid: ${starting_bid}",
            auction_id
        )
    
    cursor.close()
    
    flash('Auction created successfully!', 'success')
    return redirect(url_for('auctions'))

@app.route('/place_bid', methods=['POST'])
def place_bid():
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    auction_id = request.form.get('auction_id')
    bid_amount = request.form.get('bid_amount')
    
    try:
        bid_amount = float(bid_amount)
        auction_id = int(auction_id)
    except (ValueError, TypeError):
        flash('Invalid bid amount!', 'danger')
        return redirect(url_for('auctions'))
    
    cursor = mysql.connection.cursor()
    
    # Get auction details
    cursor.execute("""
        SELECT a.*, c.name as card_name, u.name as seller_name,
               (SELECT MAX(bid_amount) FROM bids_in WHERE auction_id = a.auction_id) as current_bid
        FROM auction a
        JOIN card c ON a.card_id = c.card_id
        JOIN users u ON a.user_id = u.user_id
        WHERE a.auction_id = %s AND a.end_time > NOW()
    """, (auction_id,))
    auction = cursor.fetchone()
    
    if not auction:
        flash('Auction not found or has ended!', 'danger')
        return redirect(url_for('auctions'))
    
    # Check if user is the seller
    if auction['user_id'] == session['user_id']:
        flash('You cannot bid on your own auction!', 'warning')
        return redirect(url_for('auctions'))
    
    # Get user balance
    cursor.execute('SELECT balance FROM users WHERE user_id = %s', (session['user_id'],))
    user_balance = cursor.fetchone()['balance']
    
    # Determine minimum bid
    current_bid = auction['current_bid'] if auction['current_bid'] else auction['starting_bid']
    min_bid = current_bid + 1 if current_bid else auction['starting_bid']
    
    # Validate bid
    if bid_amount < min_bid:
        flash(f'Bid must be at least ${min_bid:.2f}!', 'danger')
        return redirect(url_for('auctions'))
    
    if bid_amount > user_balance:
        flash('You do not have enough balance to place this bid!', 'danger')
        return redirect(url_for('auctions'))
    
    cursor.execute("""
                    SELECT user_id, bid_amount FROM bids_in 
                    WHERE user_id = %s AND auction_id = %s
                """, (session['user_id'], auction_id))
    existing_bid = cursor.fetchone()

# If user has already bid, update instead of insert
    if existing_bid:
        # Calculate the difference between new and old bid
        bid_difference = bid_amount - float(existing_bid['bid_amount'])
        
        # Check if user has enough balance for the increased bid
        if bid_difference > user_balance:
            flash('You do not have enough balance to increase your bid!', 'danger')
            return redirect(url_for('auctions'))
        else:
            cursor.execute("""
                UPDATE bids_in 
                SET bid_amount = %s 
                WHERE user_id = %s and auction_id = %s
            """, (bid_amount, existing_bid['user_id'], auction_id))
    
    else:
    # Place the bid
        cursor.execute("""
            INSERT INTO bids_in (user_id, auction_id, bid_amount)
            VALUES (%s, %s, %s)
        """, (session['user_id'], auction_id, bid_amount))
    
    mysql.connection.commit()
    cursor.close()
    
    flash(f'Bid of ${bid_amount:.2f} placed successfully!', 'success')
    return redirect(url_for('auctions'))


@app.route('/create_trade', methods=['POST'])
def create_trade():
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    card_id = request.form.get('card_id')
    description = request.form.get('description')
    duration_hours = request.form.get('duration', 24)  # Default 24 hours
    
    try:
        duration_hours = int(duration_hours)
    except (ValueError, TypeError):
        flash('Invalid duration value!', 'danger')
        return redirect(url_for('auctions'))
    
    if not description or not description.strip():
        flash('Description is required!', 'danger')
        return redirect(url_for('auctions'))
    
    cursor = mysql.connection.cursor()
    
    # Verify user owns the card
    cursor.execute('SELECT owner_id FROM card WHERE card_id = %s', (card_id,))
    card = cursor.fetchone()
    
    if not card or card['owner_id'] != session['user_id']:
        flash('You do not own this card!', 'danger')
        return redirect(url_for('auctions'))
    
    # Check if card is already in an active trade or auction
    cursor.execute("""
        SELECT trade_id FROM trade 
        WHERE card_id = %s AND end_time > NOW()
    """, (card_id,))
    existing_trade = cursor.fetchone()
    
    cursor.execute("""
        SELECT auction_id FROM auction 
        WHERE card_id = %s AND end_time > NOW()
    """, (card_id,))
    existing_auction = cursor.fetchone()
    
    if existing_trade or existing_auction:
        flash('This card is already listed in an active trade or auction!', 'warning')
        return redirect(url_for('auctions'))
    
    # Create the trade
    start_time = datetime.now()
    end_time = start_time + timedelta(hours=duration_hours)
    
    cursor.execute("""
        INSERT INTO trade (start_time, end_time, description, user_id, card_id)
        VALUES (%s, %s, %s, %s, %s)
    """, (start_time, end_time, description.strip(), session['user_id'], card_id))
    
    trade_id = cursor.lastrowid
    mysql.connection.commit()
    
    # Check if any users have this card in their wishlist and notify them
    cursor.execute("""
        SELECT w.user_id, u.name, c.name as card_name
        FROM wishlist w
        JOIN users u ON w.user_id = u.user_id
        JOIN card c ON w.card_id = c.card_id
        WHERE w.card_id = %s AND w.user_id != %s
    """, (card_id, session['user_id']))
    
    wishlist_users = cursor.fetchall()
    print(f"DEBUG: Found {len(wishlist_users)} users with card {card_id} in their wishlist")
    
    for wishlist_user in wishlist_users:
        print(f"DEBUG: Creating notification for user {wishlist_user['user_id']} ({wishlist_user['name']}) for card '{wishlist_user['card_name']}'")
        create_notification(
            wishlist_user['user_id'],
            'wishlist_trade',
            'Wishlist Alert: Card Available for Trade!',
            f"Your wishlisted card '{wishlist_user['card_name']}' is now available for trade by {session['name']}.",
            trade_id
        )
    
    cursor.close()
    
    flash('Trade created successfully!', 'success')
    return redirect(url_for('auctions'))

@app.route('/place_trade_offer', methods=['POST'])
def place_trade_offer():
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    trade_id = request.form.get('trade_id')
    offered_card_id = request.form.get('offered_card_id')
    additional_money = request.form.get('additional_money', 0)
    
    try:
        additional_money = float(additional_money) if additional_money else 0.0
        trade_id = int(trade_id)
        offered_card_id = int(offered_card_id)
    except (ValueError, TypeError):
        flash('Invalid input values!', 'danger')
        return redirect(url_for('auctions'))
    
    cursor = mysql.connection.cursor()
    
    # Get trade details
    cursor.execute("""
        SELECT t.*, c.name as card_name, u.name as trader_name
        FROM trade t
        JOIN card c ON t.card_id = c.card_id
        JOIN users u ON t.user_id = u.user_id
        WHERE t.trade_id = %s AND t.end_time > NOW()
    """, (trade_id,))
    trade = cursor.fetchone()
    
    if not trade:
        flash('Trade not found or has ended!', 'danger')
        return redirect(url_for('auctions'))
    
    # Check if user is the trade owner
    if trade['user_id'] == session['user_id']:
        flash('You cannot make an offer on your own trade!', 'warning')
        return redirect(url_for('auctions'))
    
    # Verify user owns the offered card
    cursor.execute('SELECT owner_id FROM card WHERE card_id = %s', (offered_card_id,))
    offered_card = cursor.fetchone()
    
    if not offered_card or offered_card['owner_id'] != session['user_id']:
        flash('You do not own the offered card!', 'danger')
        return redirect(url_for('auctions'))
    
    # Get user balance if additional money is offered
    if additional_money > 0:
        cursor.execute('SELECT balance FROM users WHERE user_id = %s', (session['user_id'],))
        user_balance = cursor.fetchone()['balance']
        
        if additional_money > user_balance:
            flash('You do not have enough balance to offer this amount!', 'danger')
            return redirect(url_for('auctions'))
    
    # Check if user already made an offer on this trade
    cursor.execute("""
        SELECT user_id FROM trade_offers 
        WHERE trade_id = %s AND user_id = %s
    """, (trade_id, session['user_id']))
    existing_offer = cursor.fetchone()
    
    if existing_offer:
        # Update existing offer
        cursor.execute("""
            UPDATE trade_offers 
            SET offered_card_id = %s, additional_money = %s, timestamp = NOW()
            WHERE trade_id = %s AND user_id = %s
        """, (offered_card_id, additional_money, trade_id, session['user_id']))
        flash('Your offer has been updated!', 'success')
    else:
        # Create new offer
        cursor.execute("""
            INSERT INTO trade_offers (user_id, trade_id, offered_card_id, additional_money)
            VALUES (%s, %s, %s, %s)
        """, (session['user_id'], trade_id, offered_card_id, additional_money))
        flash('Your trade offer has been placed!', 'success')
    
    mysql.connection.commit()
    cursor.close()
    
    return redirect(url_for('auctions'))

@app.route('/respond_to_trade_offer', methods=['POST'])
def respond_to_trade_offer():
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    trade_id = request.form.get('trade_id')
    offerer_id = request.form.get('offerer_id')
    action = request.form.get('action')  # 'accept' or 'decline'
    
    try:
        trade_id = int(trade_id)
        offerer_id = int(offerer_id)
    except (ValueError, TypeError):
        flash('Invalid input values!', 'danger')
        return redirect(url_for('auctions'))
    
    if action not in ['accept', 'decline']:
        flash('Invalid action!', 'danger')
        return redirect(url_for('auctions'))
    
    cursor = mysql.connection.cursor()
    
    # Verify user owns the trade
    cursor.execute("""
        SELECT t.*, c.name as card_name
        FROM trade t
        JOIN card c ON t.card_id = c.card_id
        WHERE t.trade_id = %s AND t.user_id = %s AND t.end_time > NOW()
    """, (trade_id, session['user_id']))
    trade = cursor.fetchone()
    
    if not trade:
        flash('Trade not found or you do not own this trade!', 'danger')
        return redirect(url_for('auctions'))
    
    # Update the offer status
    cursor.execute("""
        UPDATE trade_offers 
        SET status = %s
        WHERE trade_id = %s AND user_id = %s
    """, (action + 'ed', trade_id, offerer_id))
    
    if action == 'accept':
        # When accepting, decline all other offers for this trade
        cursor.execute("""
            UPDATE trade_offers 
            SET status = 'declined'
            WHERE trade_id = %s AND user_id != %s
        """, (trade_id, offerer_id))
        flash('Trade offer accepted! Waiting for the offerer to confirm receipt.', 'success')
    else:
        flash('Trade offer declined.', 'info')
    
    mysql.connection.commit()
    cursor.close()
    
    return redirect(url_for('auctions'))

@app.route('/complete_trade', methods=['POST'])
def complete_trade():
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    trade_id = request.form.get('trade_id')
    
    try:
        trade_id = int(trade_id)
    except (ValueError, TypeError):
        flash('Invalid trade ID!', 'danger')
        return redirect(url_for('auctions'))
    
    cursor = mysql.connection.cursor()
    
    # Get the accepted offer details
    cursor.execute("""
        SELECT t.*, to2.*, c1.name as trade_card_name, c2.name as offered_card_name,
               u.name as trade_owner_name
        FROM trade_offers to2
        JOIN trade t ON to2.trade_id = t.trade_id
        JOIN card c1 ON t.card_id = c1.card_id
        JOIN card c2 ON to2.offered_card_id = c2.card_id
        JOIN users u ON t.user_id = u.user_id
        WHERE to2.trade_id = %s AND to2.user_id = %s AND to2.status = 'accepted'
    """, (trade_id, session['user_id']))
    offer_details = cursor.fetchone()
    
    if not offer_details:
        flash('No accepted offer found for this trade!', 'danger')
        return redirect(url_for('auctions'))
    
    try:
        # Start transaction
        mysql.connection.begin()
        
        # Transfer cards
        # Give the trade owner's card to the offerer (current user)
        cursor.execute("""
            UPDATE card SET owner_id = %s WHERE card_id = %s
        """, (session['user_id'], offer_details['card_id']))
        cursor.execute("""UPDATE trade SET end_time = NOW() WHERE trade_id = %s""", (trade_id,))
        
        # Give the offerer's card to the trade owner
        cursor.execute("""
            UPDATE card SET owner_id = %s WHERE card_id = %s
        """, (offer_details['user_id'], offer_details['offered_card_id']))
        
        
        # Handle additional money if any
        if offer_details['additional_money'] > 0:
            # Deduct from offerer (current user)
            cursor.execute("""
                UPDATE users SET balance = balance - %s WHERE user_id = %s
            """, (offer_details['additional_money'], session['user_id']))
            
            # Add to trade owner
            cursor.execute("""
                UPDATE users SET balance = balance + %s WHERE user_id = %s
            """, (offer_details['additional_money'], offer_details['user_id']))
        
        # Remove the completed trade and all its offers
        cursor.execute("DELETE FROM trade_offers WHERE trade_id = %s", (trade_id,))
        cursor.execute("DELETE FROM trade WHERE trade_id = %s", (trade_id,))
        
        mysql.connection.commit()
        flash(f'Trade completed! You received {offer_details["trade_card_name"]} and paid ${offer_details["additional_money"]:.2f}', 'success')
        
    except Exception as e:
        mysql.connection.rollback()
        print(f"Error completing trade: {e}")
        flash('Error completing trade. Please try again.', 'danger')
    
    cursor.close()
    return redirect(url_for('auctions'))



@app.route('/my_trades')
def my_trades():
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    cursor = mysql.connection.cursor()
    
    # Fetch user's active trades with offers
    cursor.execute("""
        SELECT t.trade_id, t.start_time, t.end_time, t.description,
               c.name as card_name, c.value,
               COUNT(to2.user_id) as offer_count,
               (SELECT CONCAT(u.name, ': ', offered_card.name, ' + $', COALESCE(to3.additional_money, 0))
                FROM trade_offers to3 
                JOIN users u ON to3.user_id = u.user_id
                JOIN card offered_card ON to3.offered_card_id = offered_card.card_id
                WHERE to3.trade_id = t.trade_id
                ORDER BY to3.additional_money DESC, to3.timestamp DESC LIMIT 1) as best_offer
        FROM trade t
        JOIN card c ON t.card_id = c.card_id
        LEFT JOIN trade_offers to2 ON t.trade_id = to2.trade_id
        WHERE t.user_id = %s AND t.end_time > NOW()
        GROUP BY t.trade_id
        ORDER BY t.end_time ASC
    """, (session['user_id'],))
    active_trades = cursor.fetchall()
    
    # Fetch offers for each trade
    trade_offers = {}
    for trade in active_trades:
        cursor.execute("""
            SELECT to2.user_id, to2.additional_money, to2.timestamp,
                   u.name as offerer_name, c.name as offered_card_name
            FROM trade_offers to2
            JOIN users u ON to2.user_id = u.user_id
            JOIN card c ON to2.offered_card_id = c.card_id
            WHERE to2.trade_id = %s
            ORDER BY to2.additional_money DESC, to2.timestamp DESC
        """, (trade['trade_id'],))
        trade_offers[trade['trade_id']] = cursor.fetchall()
    
    cursor.close()
    
    return render_template('my_trades.html', 
                         active_trades=active_trades,
                         trade_offers=trade_offers)

@app.route('/my_auctions')
def my_auctions():
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    cursor = mysql.connection.cursor()
    
    # Fetch user's active auctions
    cursor.execute("""
        SELECT a.*, c.name as card_name, c.value,
               (SELECT MAX(bid_amount) FROM bids_in WHERE auction_id = a.auction_id) as current_bid,
               (SELECT COUNT(*) FROM bids_in WHERE auction_id = a.auction_id) as bid_count
        FROM auction a
        JOIN card c ON a.card_id = c.card_id
        WHERE a.user_id = %s AND a.end_time > NOW()
        ORDER BY a.end_time ASC
    """, (session['user_id'],))
    active_auctions = cursor.fetchall()
    
    # Fetch user's ended auctions
    cursor.execute("""
        SELECT a.*, c.name as card_name, c.value,
               (SELECT MAX(bid_amount) FROM bids_in WHERE auction_id = a.auction_id) as winning_bid,
               (SELECT user_id FROM bids_in WHERE auction_id = a.auction_id 
                AND bid_amount = (SELECT MAX(bid_amount) FROM bids_in WHERE auction_id = a.auction_id)) as winner_id
        FROM auction a
        JOIN card c ON a.card_id = c.card_id
        WHERE a.user_id = %s AND a.end_time <= NOW()
        ORDER BY a.end_time DESC
    """, (session['user_id'],))
    ended_auctions = cursor.fetchall()
    
    cursor.close()
    
    return render_template('my_auctions.html', 
                         active_auctions=active_auctions,
                         ended_auctions=ended_auctions)

@app.route('/load-sql')
def load_sql():
    try:
        cursor = mysql.connection.cursor()
        cursor.execute("DROP DATABASE IF EXISTS gottacatchemall")
        cursor.execute("CREATE DATABASE gottacatchemall")
        cursor.execute("USE gottacatchemall")

        with open('./database folder/gottacatchemall.sql', 'r') as f:
            sql_commands = f.read().split(';')  # split commands by semicolon

        for command in sql_commands:
            if command.strip():
                cursor.execute(command)

        mysql.connection.commit()
        cursor.close()
        flash("SQL file loaded successfully!", "success")
    except Exception as e:
        flash(f"Error loading SQL file: {e}", "danger")
    return redirect(url_for('login'))

@app.route("/wishlist")
def wishlist():
    if "user_id" not in session:
        return redirect("/login")
    
    user_id = session["user_id"]
    cursor = mysql.connection.cursor()
    
    # Get user's wishlist items with card details
    cursor.execute("""
        SELECT w.wishlist_id, w.date_created, 
               c.card_id, c.name, c.value, c.normal, c.golden, c.holographic
        FROM wishlist w
        JOIN card c ON w.card_id = c.card_id
        WHERE w.user_id = %s
        ORDER BY w.date_created DESC
    """, (user_id,))
    
    wishlist_items = []
    for row in cursor.fetchall():
        wishlist_items.append({
            'wishlist_id': row['wishlist_id'],
            'wishlist_date': row['date_created'],
            'card': {
                'card_id': row['card_id'],
                'name': row['name'],
                'value': row['value'],
                'normal': row['normal'],
                'golden': row['golden'],
                'holographic': row['holographic']
            }
        })
    
    # Get all available cards for the dropdown
    cursor.execute("SELECT name FROM card ORDER BY name")
    all_cards = cursor.fetchall()
    
    return render_template("wishlist.html", wishlist_items=wishlist_items, all_cards=all_cards)

@app.route("/remove-from-wishlist", methods=["POST"])
def remove_from_wishlist():
    if "user_id" not in session:
        return redirect("/login")
    
    user_id = session["user_id"]
    wishlist_id = request.form.get("wishlist_id")
    
    cursor = mysql.connection.cursor()
    
    try:
        # Verify the wishlist item belongs to the user
        cursor.execute("""
            DELETE FROM wishlist 
            WHERE wishlist_id = %s AND user_id = %s
        """, (wishlist_id, user_id))
        
        mysql.connection.commit()
        flash("Card removed from wishlist!", "success")
        
    except Exception as e:
        mysql.connection.rollback()
        print(f"Error removing from wishlist: {e}")
        flash("Error removing card from wishlist.", "danger")
    
    return redirect(url_for("wishlist"))


@app.route("/add-to-wishlist", methods=["POST"])
def add_to_wishlist():
    if "user_id" not in session:
        return redirect("/login")
    
    user_id = session["user_id"]
    card_name = request.form.get("name")
    
    if not card_name:
        flash("Please enter a card name!", "danger")
        return redirect(url_for("wishlist"))
    
    cursor = mysql.connection.cursor()
    
    try:
        # First, check if the card exists in the database
        cursor.execute("SELECT card_id FROM card WHERE name = %s", (card_name,))
        card = cursor.fetchone()
        
        if not card:
            flash(f"Card '{card_name}' not found in the database!", "danger")
            return redirect(url_for("wishlist"))
        
        card_id = card['card_id']
        
        # Check if card is already in user's wishlist
        cursor.execute("""
            SELECT wishlist_id FROM wishlist 
            WHERE user_id = %s AND card_id = %s
        """, (user_id, card_id))
        
        if cursor.fetchone():
            flash(f"{card_name} is already in your wishlist!", "warning")
        else:
            # Insert into wishlist
            cursor.execute("""
                INSERT INTO wishlist (user_id, card_id, date_created)
                VALUES (%s, %s, NOW())
            """, (user_id, card_id))
            
            mysql.connection.commit()
            flash(f"{card_name} added to your wishlist!", "success")
            
    except Exception as e:
        mysql.connection.rollback()
        print(f"Error adding to wishlist: {e}")
        flash("Error adding card to wishlist. Please try again.", "danger")
    
    return redirect(url_for("wishlist"))

# Notification functions
def create_notification(user_id, notification_type, title, message, related_id=None):
    """Helper function to create notifications"""
    cursor = mysql.connection.cursor()
    cursor.execute("""
        INSERT INTO notifications (user_id, type, title, message, related_id)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_id, notification_type, title, message, related_id))
    mysql.connection.commit()
    cursor.close()

def notify_wishlist_users_for_existing_listings():
    """Check existing active trades and auctions against all wishlists and create notifications"""
    cursor = mysql.connection.cursor()
    
    try:
        # Check active trades against wishlists
        cursor.execute("""
            SELECT DISTINCT w.user_id, t.trade_id, t.user_id as trader_id, 
                   c.name as card_name, tu.name as trader_name
            FROM wishlist w
            JOIN trade t ON w.card_id = t.card_id
            JOIN card c ON w.card_id = c.card_id
            JOIN users tu ON t.user_id = tu.user_id
            WHERE t.end_time > NOW() 
            AND w.user_id != t.user_id
            AND NOT EXISTS (
                SELECT 1 FROM notifications n 
                WHERE n.user_id = w.user_id 
                AND n.type = 'wishlist_trade' 
                AND n.related_id = t.trade_id
            )
        """)
        
        trade_matches = cursor.fetchall()
        
        for match in trade_matches:
            create_notification(
                match['user_id'],
                'wishlist_trade',
                'Wishlist Alert: Card Available for Trade!',
                f"Your wishlisted card '{match['card_name']}' is available for trade by {match['trader_name']}.",
                match['trade_id']
            )
        
        # Check active auctions against wishlists
        cursor.execute("""
            SELECT DISTINCT w.user_id, a.auction_id, a.user_id as auctioneer_id,
                   c.name as card_name, au.name as auctioneer_name, a.starting_bid
            FROM wishlist w
            JOIN auction a ON w.card_id = a.card_id
            JOIN card c ON w.card_id = c.card_id
            JOIN users au ON a.user_id = au.user_id
            WHERE a.end_time > NOW() 
            AND w.user_id != a.user_id
            AND NOT EXISTS (
                SELECT 1 FROM notifications n 
                WHERE n.user_id = w.user_id 
                AND n.type = 'wishlist_auction' 
                AND n.related_id = a.auction_id
            )
        """)
        
        auction_matches = cursor.fetchall()
        
        for match in auction_matches:
            create_notification(
                match['user_id'],
                'wishlist_auction',
                'Wishlist Alert: Card Available at Auction!',
                f"Your wishlisted card '{match['card_name']}' is available at auction by {match['auctioneer_name']}. Starting bid: ${match['starting_bid']}",
                match['auction_id']
            )
        
        notifications_created = len(trade_matches) + len(auction_matches)
        print(f"Created {notifications_created} wishlist notifications ({len(trade_matches)} for trades, {len(auction_matches)} for auctions)")
        
    except Exception as e:
        print(f"Error creating wishlist notifications: {e}")
    finally:
        cursor.close()
        
    return notifications_created

@app.route('/get_notifications')
def get_notifications():
    if 'user_id' not in session:
        return jsonify({"error": "Not logged in"}), 401
    
    user_id = session['user_id']
    cursor = mysql.connection.cursor()
    
    # Get user's notifications (latest first)
    cursor.execute("""
        SELECT notification_id, type, title, message, related_id, is_read, created_at
        FROM notifications
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 20
    """, (user_id,))
    
    notifications = cursor.fetchall()
    
    # Get unread count
    cursor.execute("""
        SELECT COUNT(*) as unread_count
        FROM notifications
        WHERE user_id = %s AND is_read = 0
    """, (user_id,))
    
    unread_count = cursor.fetchone()['unread_count']
    cursor.close()
    
    return jsonify({
        "notifications": notifications,
        "unread_count": unread_count
    })

@app.route('/mark_notification_read/<int:notification_id>', methods=['POST'])
def mark_notification_read(notification_id):
    if 'user_id' not in session:
        return jsonify({"error": "Not logged in"}), 401
    
    user_id = session['user_id']
    cursor = mysql.connection.cursor()
    
    # Mark notification as read (only if it belongs to the user)
    cursor.execute("""
        UPDATE notifications 
        SET is_read = 1 
        WHERE notification_id = %s AND user_id = %s
    """, (notification_id, user_id))
    
    mysql.connection.commit()
    cursor.close()
    
    return jsonify({"success": True})

@app.route('/mark_all_notifications_read', methods=['POST'])
def mark_all_notifications_read():
    if 'user_id' not in session:
        return jsonify({"error": "Not logged in"}), 401
    
    user_id = session['user_id']
    cursor = mysql.connection.cursor()
    
    # Mark all notifications as read for the user
    cursor.execute("""
        UPDATE notifications 
        SET is_read = 1 
        WHERE user_id = %s
    """, (user_id,))
    
    mysql.connection.commit()
    cursor.close()
    
    return jsonify({"success": True})

@app.route('/check_wishlist_notifications')
def check_wishlist_notifications():
    """Manual route to check and create wishlist notifications for existing active listings"""
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    notifications_created = notify_wishlist_users_for_existing_listings()
    
    if notifications_created > 0:
        flash(f'Created {notifications_created} new wishlist notifications!', 'success')
    else:
        flash('No new wishlist notifications needed.', 'info')
    
    return redirect(url_for('dashboard'))

@app.route('/cancel_auction/<int:auction_id>', methods=['POST'])
def cancel_auction(auction_id):
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    cursor = mysql.connection.cursor()
    
    try:
        # Check if user owns the auction and it hasn't ended
        cursor.execute("""
            SELECT auction_id, end_time FROM auction 
            WHERE auction_id = %s AND user_id = %s AND end_time > NOW()
        """, (auction_id, session['user_id']))
        
        auction = cursor.fetchone()
        if not auction:
            flash('Auction not found or already ended!', 'danger')
            return redirect(url_for('auctions'))
        
        # Check if there are any bids
        cursor.execute("""
            SELECT COUNT(*) as bid_count FROM bids_in WHERE auction_id = %s
        """, (auction_id,))
        
        bid_count = cursor.fetchone()['bid_count']
        if bid_count > 0:
            flash('Cannot cancel auction with existing bids!', 'warning')
            return redirect(url_for('auctions'))
        
        # Delete the auction
        cursor.execute("DELETE FROM auction WHERE auction_id = %s", (auction_id,))
        mysql.connection.commit()
        
        flash('Auction cancelled successfully!', 'success')
        
    except Exception as e:
        mysql.connection.rollback()
        print(f"Error cancelling auction: {e}")
        flash('Error cancelling auction. Please try again.', 'danger')
    finally:
        cursor.close()
    
    return redirect(url_for('auctions'))

@app.route('/cancel_trade/<int:trade_id>', methods=['POST'])
def cancel_trade(trade_id):
    if 'user_id' not in session:
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    cursor = mysql.connection.cursor()
    
    try:
        # Check if user owns the trade and it hasn't ended
        cursor.execute("""
            SELECT trade_id, end_time FROM trade 
            WHERE trade_id = %s AND user_id = %s AND end_time > NOW()
        """, (trade_id, session['user_id']))
        
        trade = cursor.fetchone()
        if not trade:
            flash('Trade not found or already ended!', 'danger')
            return redirect(url_for('auctions'))
        
        # Delete trade offers first (due to foreign key constraint)
        cursor.execute("DELETE FROM trade_offers WHERE trade_id = %s", (trade_id,))
        
        # Delete the trade
        cursor.execute("DELETE FROM trade WHERE trade_id = %s", (trade_id,))
        mysql.connection.commit()
        
        flash('Trade cancelled successfully!', 'success')
        
    except Exception as e:
        mysql.connection.rollback()
        print(f"Error cancelling trade: {e}")
        flash('Error cancelling trade. Please try again.', 'danger')
    finally:
        cursor.close()
    
    return redirect(url_for('auctions'))

if __name__ == '__main__':
    app.secret_key = "your_secret_key"
    app.run(debug=True)