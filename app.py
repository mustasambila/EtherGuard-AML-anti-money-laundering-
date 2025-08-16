import os
import re
import requests
import datetime
import json 
import random
import json
from jinja2 import Undefined
from functools import wraps
from web3 import Web3
from flask import Flask, render_template, jsonify, request, redirect, url_for, session, flash, send_file
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from models import db, User, KYCRequest, Watchlist, ActivityLog, Alert, PDFExport
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from io import BytesIO
from flask_mail import Mail, Message


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fyp.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

@app.cli.command('init-db')
def init_db():
    """Initialize the database."""
    with app.app_context():
        db.create_all()
    print('Initialized the database.')

db.init_app(app)


@app.template_filter('wei_to_eth')
def wei_to_eth_filter(value):
    """Convert wei value to ETH"""
    try:
        return int(value) / 10**18  # 1 ETH = 10^18 wei
    except (ValueError, TypeError):
        return 0.0  # Return 0 if conversion fails

@app.template_filter('timestamp_to_date')
def timestamp_to_date(timestamp):
    """Convert Unix timestamp to readable date format"""
    try:
        return datetime.fromtimestamp(int(timestamp)).strftime('%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        return 'N/A'
    
WATCHLIST_ACTIVITY_FILE = 'watchlist_activity.json'



def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            flash('You do not have permission to access this page', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def save_watchlist_activity(logs):
    with open(WATCHLIST_ACTIVITY_FILE, 'w' ) as f:
        json.dump(logs, f, default=str)
def load_watchlist_activity():
    try:
        with open(WATCHLIST_ACTIVITY_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"Error loading watchlist activity: {e}")
        return []
watchlist = [item['address'] for item in load_watchlist_activity()]

app.secret_key = os.getenv('SECRET_KEY', 'default-secret-key')

ETHERSCAN_API_KEY = os.getenv('ETHERSCAN_API_KEY')
BASE_URL = 'https://api.etherscan.io/api'
# Fake user store for demo
fake_users = {
    'hello': {'password': '@1341'}
}

# Add these near your other utility functions
def get_log_level_stats():
    """Return counts for each log level"""
    return {
        'critical': ActivityLog.query.filter_by(level='critical').count(),
        'error': ActivityLog.query.filter_by(level='error').count(),
        'warning': ActivityLog.query.filter_by(level='warning').count(),
        'info': ActivityLog.query.filter_by(level='info').count(),
        'debug': ActivityLog.query.filter_by(level='debug').count()
    }

def get_activity_stats(days=30):
    """Return activity data for chart"""
    dates = []
    counts = []
    today = datetime.today().date()
    
    for i in range(days, -1, -1):
        day = today - timedelta(days=i)
        count = ActivityLog.query.filter(
            ActivityLog.timestamp >= day,
            ActivityLog.timestamp < day + timedelta(days=1)
        ).count()
        dates.append(day.strftime('%m/%d'))
        counts.append(count)
    
    return {'days': dates, 'counts': counts}

def get_account_balance(address):
    url = f"{BASE_URL}?module=account&action=balance&address={address}&tag=latest&apikey={ETHERSCAN_API_KEY}"
    response = requests.get(url)
    data = response.json()
    if data['status'] == '1':
        return int(data['result']) / (10 ** 18)
    return None

# Add these functions after your other utility functions (around line 200)
def check_and_create_alerts(user_id, wallet_address, transactions, suspicious_flags):
    """Check for various alert conditions and create alerts if needed"""
    try:
        # Check if user has email alerts enabled
        user = User.query.get(user_id)
        if not user or not user.email_alerts_enabled:
            return
        
        # Check for large transactions
        for tx in transactions:
            if is_large_transaction(tx):
                # Check if alert already exists for this transaction
                existing_alert = Alert.query.filter_by(
                    user_id=user_id,
                    transaction_hash=tx['hash'],
                    alert_type='large_transaction'
                ).first()
                
                if not existing_alert:
                    create_alert(
                        user_id=user_id,
                        alert_type='large_transaction',
                        title='Large Transaction Detected',
                        message=f'Large transaction of {int(tx["value"]) / 1e18:.4f} ETH detected on wallet {wallet_address}',
                        wallet_address=wallet_address,
                        transaction_hash=tx['hash']
                    )
        
        # Check for suspicious addresses
        for tx, flags in suspicious_flags:
            if 'Known suspicious address' in flags:
                existing_alert = Alert.query.filter_by(
                    user_id=user_id,
                    transaction_hash=tx['hash'],
                    alert_type='suspicious_address'
                ).first()
                
                if not existing_alert:
                    create_alert(
                        user_id=user_id,
                        alert_type='suspicious_address',
                        title='Suspicious Address Activity',
                        message=f'Transaction involving known suspicious address detected on wallet {wallet_address}',
                        wallet_address=wallet_address,
                        transaction_hash=tx['hash']
                    )
        
        # Check for high frequency transactions
        if is_high_frequency(transactions):
            # Check if alert already exists for this wallet in the last hour
            one_hour_ago = datetime.utcnow() - timedelta(hours=1)
            existing_alert = Alert.query.filter(
                Alert.user_id == user_id,
                Alert.wallet_address == wallet_address,
                Alert.alert_type == 'high_frequency',
                Alert.created_at > one_hour_ago
            ).first()
            
            if not existing_alert:
                create_alert(
                    user_id=user_id,
                    alert_type='high_frequency',
                    title='High Frequency Activity',
                    message=f'High frequency transaction activity detected on wallet {wallet_address}',
                    wallet_address=wallet_address
                )
                
    except Exception as e:
        print(f"Error in check_and_create_alerts: {e}")

def create_alert(user_id, alert_type, title, message, wallet_address=None, transaction_hash=None):
    """Create a new alert and optionally send email"""
    try:
        # Create alert record
        alert = Alert(
            user_id=user_id,
            alert_type=alert_type,
            title=title,
            message=message,
            wallet_address=wallet_address,
            transaction_hash=transaction_hash
        )
        db.session.add(alert)
        db.session.commit()
        
        # Send email if user has email alerts enabled (optional - requires mail setup)
        # user = User.query.get(user_id)
        # if user and user.email_alerts_enabled:
        #     send_alert_email(user, alert)
        #     alert.email_sent = True
        #     db.session.commit()
            
    except Exception as e:
        print(f"Error creating alert: {e}")
        db.session.rollback()

def generate_wallet_pdf(wallet_data, suspicious_flags):
    """Generate PDF report for wallet tracking results"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        spaceAfter=30,
        alignment=1  # Center alignment
    )
    story.append(Paragraph("EtherGuard - Wallet Analysis Report", title_style))
    story.append(Spacer(1, 12))
    
    # Wallet Information - Use dictionary access
    story.append(Paragraph("Wallet Information", styles['Heading2']))
    wallet_info = [
        ['Address:', wallet_data['address']],
        ['ETH Balance:', f"{wallet_data['balance']:.4f} ETH"],
        ['Report Generated:', datetime.now().strftime('%Y-%m-%d %H:%M:%S')]
    ]
    
    wallet_table = Table(wallet_info, colWidths=[2*inch, 4*inch])
    wallet_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    story.append(wallet_table)
    story.append(Spacer(1, 20))
    
    # Recent Transactions
    story.append(Paragraph("Recent Transactions", styles['Heading2']))
    if wallet_data['transactions']:
        tx_data = [['Hash', 'From', 'To', 'Value (ETH)', 'Timestamp']]
        
        for tx in wallet_data['transactions'][:10]:  # Show first 10 transactions
            # Handle both dict and object structures
            if isinstance(tx, dict):
                tx_hash = tx.get('hash', 'N/A')[:20] + '...'
                tx_from = tx.get('from', 'N/A')[:15] + '...' if tx.get('from') else 'N/A'
                tx_to = tx.get('to', 'N/A')[:15] + '...' if tx.get('to') else 'N/A'
                tx_value = f"{float(tx.get('value', 0)) / 1e18:.4f}"
                tx_timestamp = tx.get('timeStamp', 'N/A')
            else:
                tx_hash = getattr(tx, 'hash', 'N/A')[:20] + '...'
                tx_from = getattr(tx, 'from', 'N/A')[:15] + '...' if hasattr(tx, 'from') else 'N/A'
                tx_to = getattr(tx, 'to', 'N/A')[:15] + '...' if hasattr(tx, 'to') else 'N/A'
                tx_value = f"{float(getattr(tx, 'value', 0)) / 1e18:.4f}"
                tx_timestamp = getattr(tx, 'timeStamp', 'N/A')
            
            tx_data.append([tx_hash, tx_from, tx_to, tx_value, tx_timestamp])
        
        tx_table = Table(tx_data, colWidths=[1.5*inch, 1.2*inch, 1.2*inch, 1*inch, 1.1*inch])
        tx_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(tx_table)
    else:
        story.append(Paragraph("No transactions found.", styles['Normal']))
    
    # Suspicious Activity Summary
    if suspicious_flags:
        story.append(Spacer(1, 20))
        story.append(Paragraph("Suspicious Activity Summary", styles['Heading2']))
        story.append(Paragraph(f"Total Flagged Transactions: {len(suspicious_flags)}", styles['Normal']))
        
        for tx_tuple, flags in suspicious_flags[:5]:  # Show first 5 flagged transactions
            tx_hash = getattr(tx_tuple, 'hash', 'N/A') if hasattr(tx_tuple, 'hash') else tx_tuple.get('hash', 'N/A') if isinstance(tx_tuple, dict) else 'N/A'
            story.append(Paragraph(f"• Transaction {tx_hash[:20]}... - Flags: {', '.join(flags)}", styles['Normal']))
    
    doc.build(story)
    buffer.seek(0)
    return buffer
def generate_watchlist_pdf(watchlist_items):
    """Generate comprehensive PDF report for watchlist with 90-day balance history and detailed transactions"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    story = []
    
    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=20,
        spaceAfter=20,
        alignment=1,
        textColor=colors.darkblue
    )
    story.append(Paragraph("EtherGuard - Comprehensive Watchlist Report", title_style))
    story.append(Spacer(1, 12))
    
    # Report info
    info_style = ParagraphStyle('InfoStyle', parent=styles['Normal'], fontSize=10, textColor=colors.grey)
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}", info_style))
    story.append(Paragraph(f"Total Addresses Monitored: {len(watchlist_items)}", info_style))
    story.append(Paragraph("Report includes: 90-day balance history and minimum 100 recent transactions per address", info_style))
    story.append(Spacer(1, 20))
    
    if not watchlist_items:
        story.append(Paragraph("No addresses in watchlist.", styles['Normal']))
        doc.build(story)
        buffer.seek(0)
        return buffer
    
    # Process each watchlist item
    for idx, item in enumerate(watchlist_items, 1):
        # Section header for each address
        addr_style = ParagraphStyle(
            'AddressStyle',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=10,
            textColor=colors.darkgreen
        )
        story.append(Paragraph(f"Address {idx}: {item.address}", addr_style))
        
        # Get current data
        current_balance = get_account_balance(item.address) or 0.0
        transactions = get_extended_transactions(item.address, 200)  # Get 200 transactions
        balance_history = get_balance_history_data(item.address, 90)
        
        # Current status summary
        flagged = False
        flagged_count = 0
        if transactions:
            for tx in transactions:
                if (is_large_transaction(tx) or 
                    is_to_from_suspicious_address(tx) or 
                    is_high_frequency(transactions)):
                    flagged_count += 1
            flagged = flagged_count > 0
        
        status = 'FLAGGED' if flagged else 'NORMAL'
        status_color = colors.red if flagged else colors.green
        
        # Summary table for this address
        summary_data = [
            ['Current Balance', f"{current_balance:.6f} ETH"],
            ['Total Transactions', str(len(transactions))],
            ['Flagged Transactions', str(flagged_count)],
            ['Status', status],
            ['Last Updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S')]
        ]
        
        summary_table = Table(summary_data, colWidths=[2*inch, 2*inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey),
            ('BACKGROUND', (1, 3), (1, 3), status_color),
            ('TEXTCOLOR', (1, 3), (1, 3), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 15))
        
        # 30-Day Balance History
        story.append(Paragraph("30-Day Balance History:", styles['Heading3']))
        if balance_history:
            # Create balance history table (show every 3rd day to fit)
            balance_data = [['Date', 'Balance (ETH)']]
            for hist in balance_history:  # Show all days
                balance_data.append([hist['date'], f"{hist['balance']:.6f}"])
            
            balance_table = Table(balance_data, colWidths=[1.5*inch, 1.5*inch])
            balance_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey])
            ]))
            story.append(balance_table)
        else:
            story.append(Paragraph("Balance history unavailable", styles['Normal']))
        
        story.append(Spacer(1, 15))
        
        # Recent Transactions (minimum 40)
        story.append(Paragraph(f"Recent Transactions (Showing {min(len(transactions), 45)}):", styles['Heading3']))
        
        if transactions:
            # Transaction table with detailed information
            tx_data = [['Hash', 'From', 'To', 'Amount (ETH)', 'Date & Time', 'Status']]
            
            for tx in transactions[:75]:  # Show up to 45 transactions
                # Format transaction data
                tx_hash = tx['hash'][:20] + '...' if len(tx['hash']) > 20 else tx['hash']
                from_addr = tx['from'][:15] + '...' if len(tx['from']) > 15 else tx['from']
                to_addr = tx['to'][:15] + '...' if len(tx['to']) > 15 else tx['to']
                amount = f"{int(tx['value'])/1e18:.6f}"
                
                # Convert timestamp to readable date
                try:
                    tx_date = datetime.fromtimestamp(int(tx['timeStamp'])).strftime('%Y-%m-%d %H:%M')
                except:
                    tx_date = 'Unknown'
                
                # Determine status
                tx_status = 'Normal'
                if is_large_transaction(tx):
                    tx_status = 'Large Amount'
                elif is_to_from_suspicious_address(tx):
                    tx_status = 'Suspicious'
                
                tx_data.append([tx_hash, from_addr, to_addr, amount, tx_date, tx_status])
            
            # Create transaction table
            tx_table = Table(tx_data, colWidths=[1.2*inch, 1.1*inch, 1.1*inch, 0.8*inch, 1*inch, 0.8*inch])
            tx_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')
            ]))
            
            # Highlight flagged transactions
            for i, tx in enumerate(transactions[:45], 1):
                if (is_large_transaction(tx) or is_to_from_suspicious_address(tx)):
                    tx_table.setStyle(TableStyle([
                        ('BACKGROUND', (0, i), (-1, i), colors.lightyellow),
                        ('TEXTCOLOR', (5, i), (5, i), colors.red)
                    ]))
            
            story.append(tx_table)
        else:
            story.append(Paragraph("No transactions found for this address", styles['Normal']))
        
        # Add page break between addresses (except for the last one)
        if idx < len(watchlist_items):
            story.append(PageBreak())
        else:
            story.append(Spacer(1, 20))
    
    # Footer
    footer_style = ParagraphStyle(
        'FooterStyle',
        parent=styles['Normal'],
        fontSize=8,
        alignment=1,
        textColor=colors.grey
    )
    story.append(Paragraph("Generated by EtherGuard AML System | Confidential Report", footer_style))
    
    doc.build(story)
    buffer.seek(0)
    return buffer

def generate_flagged_transactions_pdf(flagged_transactions):
    """Generate PDF report for flagged transactions"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        spaceAfter=30,
        alignment=1
    )
    story.append(Paragraph("EtherGuard - Flagged Transactions Report", title_style))
    story.append(Spacer(1, 12))
    
    # Report info
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
    story.append(Paragraph(f"Total Flagged Transactions: {len(flagged_transactions)}", styles['Normal']))
    story.append(Spacer(1, 20))
    
    if flagged_transactions:
        # Flagged transactions table
        tx_data = [['Hash', 'From', 'To', 'Value (ETH)', 'Flags', 'Timestamp']]
        
        for tx_tuple in flagged_transactions:
            # Extract transaction and flags from tuple
            tx, flags = tx_tuple
            
            # Handle different data structures (dict vs object)
            if isinstance(tx, dict):
                tx_hash = tx.get('hash', 'N/A')[:20] + '...'
                tx_from = tx.get('from', 'N/A')[:15] + '...' if tx.get('from') else 'N/A'
                tx_to = tx.get('to', 'N/A')[:15] + '...' if tx.get('to') else 'N/A'
                tx_value = f"{float(tx.get('value', 0)) / 1e18:.4f}" if tx.get('value') else '0'
                tx_timestamp = tx.get('timeStamp', 'N/A')
            else:
                # Handle object attributes
                tx_hash = getattr(tx, 'hash', 'N/A')[:20] + '...'
                tx_from = getattr(tx, 'from', getattr(tx, 'from_address', 'N/A'))[:15] + '...' if hasattr(tx, 'from') or hasattr(tx, 'from_address') else 'N/A'
                tx_to = getattr(tx, 'to', 'N/A')[:15] + '...' if hasattr(tx, 'to') and getattr(tx, 'to') else 'N/A'
                tx_value = f"{float(getattr(tx, 'value', 0)) / 1e18:.4f}" if hasattr(tx, 'value') and getattr(tx, 'value') else '0'
                tx_timestamp = getattr(tx, 'timeStamp', 'N/A')
            
            tx_data.append([
                tx_hash,
                tx_from,
                tx_to,
                tx_value,
                ', '.join(flags),
                tx_timestamp
            ])
        
        tx_table = Table(tx_data, colWidths=[1.2*inch, 1*inch, 1*inch, 0.8*inch, 1.2*inch, 0.8*inch])
        tx_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.lightcoral),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(tx_table)
    else:
        story.append(Paragraph("No flagged transactions found.", styles['Normal']))
    
    doc.build(story)
    buffer.seek(0)
    return buffer

def is_authenticated():
    return 'user' in session

def is_admin():
    return session.get('is_admin', False)

# Admin required decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_authenticated():
            flash('Please login first', 'danger')
            return redirect(url_for('login'))
        if not is_admin():
            flash('Admin access required', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def is_valid_eth_address(address):
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", address))

def get_transactions(address):
    url = f"{BASE_URL}?module=account&action=txlist&address={address}&sort=desc&apikey={ETHERSCAN_API_KEY}"
    response = requests.get(url)
    return response.json().get('result', [])[:10]
def is_large_transaction(tx, threshold_eth=10):
    """Check if the transaction value exceeds a certain threshold in ETH."""
    try:
        eth_value = int(tx['value']) / 1e18  # Convert from wei to ETH
        return eth_value > threshold_eth
    except Exception:
        return False
# Example: falg if transaction is to/from a known suspicious address
SUSPICIOUS_ADDRESSES = {
    '0x1234567890abcdef1234567890abcdef12345678',  # Example address
    '0xabcdefabcdefabcdefabcdefabcdefabcdefabcd'   # Another example address
}
def is_to_from_suspicious_address(tx):
    """Check if the transaction is to or from a known suspicious address."""
    return tx['to'] in SUSPICIOUS_ADDRESSES or tx['from'] in SUSPICIOUS_ADDRESSES 
def is_high_frequency(transactions, threshold=10, window_seconds=3600):
    """Check if the number of transactions exceeds a threshold within a time window"""
    now = int(datetime.now().timestamp())
    recent_transactions = [tx for tx in transactions if now - int(tx['timeStamp']) < window_seconds]
    return len(recent_transactions) > threshold
def log_activity(user_id, activity, level="info", details=None, ip_address=None):
    """Enhanced logging function with additional details"""
    new_log = ActivityLog(
        user_id=user_id, 
        activity=activity, 
        level=level,
        details=details,
        ip_address=ip_address or request.remote_addr if request else None
    )
    db.session.add(new_log)
    db.session.commit()


def generate_mock_data():
    # Generate mock Ethereum addresses
    def mock_address():
        return '0x' + ''.join(random.choices('0123456789abcdef', k=40))
    
    # Generate mock transactions
    suspicious_txns = []
    for i in range(10):
        suspicious_txns.append({
            'hash': '0x' + ''.join(random.choices('0123456789abcdef', k=64)),
            'from': mock_address(),
            'to': mock_address(),
            'value': random.randint(1, 100) * 1e18,
            'timeStamp': int((datetime.now() - timedelta(hours=random.randint(1, 24))).timestamp())
        })
    
    # Return a single dictionary with all data
    return {
        'suspicious_count': random.randint(5, 50),
        'total_transactions': random.randint(100, 1000),
        'high_risk_wallets': random.randint(10, 100),
        'sars_filed': random.randint(1, 20),
        'suspicious_transactions': suspicious_txns,
        'risk_trend': {
            'weekly': {'labels': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                      'data': [random.randint(10, 100) for _ in range(7)]},
            'monthly': {'labels': [f'Week {i+1}' for i in range(12)],
                       'data': [random.randint(50, 500) for _ in range(12)]},
            'yearly': {'labels': [f'202{9+i}' for i in range(5)],
                      'data': [random.randint(500, 5000) for _ in range(5)]}
        },
        'risk_distribution': {
            'high': random.randint(5, 20),
            'medium': random.randint(20, 40),
            'low': 100 - random.randint(25, 60)
        }
    }

@app.template_filter('eth_value')
def eth_value_filter(value):
    """Convert wei to ETH"""
    try:
        return int(value) / 10**18  # 1 ETH = 10^18 wei
    except (ValueError, TypeError):
        return 0.0

@app.template_filter('datetimeformat')
def datetimeformat_filter(value, format='%Y-%m-%d %H:%M'):
    """Convert timestamp or datetime to formatted string"""
    try:
        if isinstance(value, (int, float, str)):
            # Handle Unix timestamps
            return datetime.fromtimestamp(int(value)).strftime(format)
        elif isinstance(value, datetime):
            # Handle datetime objects directly
            return value.strftime(format)
    except (ValueError, TypeError):
        pass
    return "N/A"


@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            session['user'] = user.username
            session['is_admin'] = user.is_admin
            
            # Enhanced login logging
            log_activity(
                user.id, 
                "User logged in successfully",
                level="info",
                details=f"Login method: form, Admin: {user.is_admin}"
            )
            return redirect(url_for('home'))
        else:
            # Log failed login attempt
            if user:
                log_activity(
                    user.id,
                    "Failed login attempt - incorrect password",
                    level="warning",
                    details=f"Username: {username}"
                )
            else:
                # For non-existent users, log with system user (user_id=1 or create a system user)
                log_activity(
                    1,  # System user ID
                    f"Failed login attempt - user not found",
                    level="warning",
                    details=f"Username: {username}"
                )
            return 'Invalid credentials'

    return render_template('login.html')



@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        email = request.form.get('email')
        if User.query.filter_by(username=username).first():
            return "User already exists"
        hashed_password = generate_password_hash(password)
        user = User(username=username, password=hashed_password, email=email)
        db.session.add(user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('signup.html')

def validate_kyc_submission(full_name, dob, id_number, filename):
    """Basic validation without auto-approval"""
    errors = []
    
    # Basic file type validation
    allowed_extensions = ['pdf', 'jpg', 'jpeg', 'png']
    if not filename.lower().split('.')[-1] in allowed_extensions:
        errors.append("Invalid file type. Please upload PDF, JPG, JPEG, or PNG files only.")
    
    # Basic DOB validation (not in future)
    try:
        dob_date = datetime.strptime(dob, '%Y-%m-%d')
        if dob_date > datetime.now():
            errors.append("Date of birth cannot be in the future.")
    except ValueError:
        errors.append("Invalid date of birth format.")
    
    # Basic name validation
    if len(full_name.strip()) < 2:
        errors.append("Full name must be at least 2 characters long.")
    
    return errors

@app.route('/kyc', methods=['GET', 'POST'])
def kyc():
    if 'user' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['user']).first()

    if request.method == 'POST':
        # Get form data
        full_name = request.form.get('full_name')
        dob = request.form.get('dob')
        address = request.form.get('address')
        id_number = request.form.get('id_number')
        
        # Handle file uploads
        id_document = request.files.get('id_document')
        poa_document = request.files.get('poa_document')
        
        id_doc_path = None
        poa_doc_path = None
        
        # Basic validation
        validation_errors = validate_kyc_submission(full_name, dob, id_number, id_document.filename if id_document else '')
        if validation_errors:
            return f"Validation Error: {'; '.join(validation_errors)}"
        
        # Save ID document
        if id_document and id_document.filename:
            id_filename = secure_filename(f"{user.id}_id_{id_document.filename}")
            id_doc_path = os.path.join('static/kyc_docs', id_filename)
            id_document.save(id_doc_path)
        
        # Save POA document
        if poa_document and poa_document.filename:
            poa_filename = secure_filename(f"{user.id}_poa_{poa_document.filename}")
            poa_doc_path = os.path.join('static/kyc_docs', poa_filename)
            poa_document.save(poa_doc_path)

        kyc_entry = KYCRequest(
            user_id=user.id,
            full_name=full_name,
            dob=dob,
            address=address,
            id_number=id_number,
            id_document_path=id_doc_path,
            poa_document_path=poa_doc_path,
            status='pending',
            submitted_at=datetime.now()
        )

        db.session.add(kyc_entry)
        db.session.commit()
        
        log_activity(
            user.id,
            "KYC documents submitted for admin review",
            level="info",
            details=f"ID Document: {id_doc_path}, POA Document: {poa_doc_path}, Status: pending"
        )
        
        return "KYC Submitted successfully. Your documents are now pending admin review."

    return render_template('kyc.html')

@app.route('/admin/logs')
def admin_logs():
    # Get page number from request args, default to 1
    page = request.args.get('page', 1, type=int)
    per_page = 20  # Items per page

    # Get filter parameters
    level_filter = request.args.get('level', 'all')
    date_filter = request.args.get('date_range', '30days')
    search_query = request.args.get('search', '')

    # Base query with User join to ensure user data is available
    query = ActivityLog.query.join(User, ActivityLog.user_id == User.id, isouter=True)

    # Apply filters
    if level_filter != 'all':
        query = query.filter(ActivityLog.level == level_filter)
    
    # Date range filtering
    if date_filter == '24hours':
        start_date = datetime.utcnow() - timedelta(hours=24)
    elif date_filter == '7days':
        start_date = datetime.utcnow() - timedelta(days=7)
    else:  # 30days
        start_date = datetime.utcnow() - timedelta(days=30)
    
    query = query.filter(ActivityLog.timestamp >= start_date)

    # Search functionality
    if search_query:
        query = query.filter(ActivityLog.activity.ilike(f'%{search_query}%'))

    # Get paginated results
    logs_pagination = query.order_by(ActivityLog.timestamp.desc()).paginate(page=page, per_page=per_page, error_out=False)
    logs = logs_pagination.items

    # Get log level statistics (unfiltered for accurate stats)
    level_stats = {
        'critical': ActivityLog.query.filter(ActivityLog.level == 'critical').count(),
        'error': ActivityLog.query.filter(ActivityLog.level == 'error').count(),
        'warning': ActivityLog.query.filter(ActivityLog.level == 'warning').count(),
        'info': ActivityLog.query.filter(ActivityLog.level == 'info').count()
    }

    # Get user activity count (last 24 hours)
    user_activity = ActivityLog.query.filter(
        ActivityLog.timestamp >= datetime.utcnow() - timedelta(hours=24)
    ).count()

    return render_template('admin_logs.html',
        logs=logs,
        pagination=logs_pagination,
        page=page,
        level_stats=level_stats,
        user_activity=user_activity,
        current_filters={
            'level': level_filter,
            'date_range': date_filter,
            'search': search_query
        }
    )

@app.route('/admin/kyc')
@admin_required
def admin_kyc():
    # Get all KYC requests with user information
    kyc_requests = db.session.query(KYCRequest, User).join(User, KYCRequest.user_id == User.id).filter(KYCRequest.status == 'pending').all()
    all_kyc_requests = KYCRequest.query.all()
    
    # Calculate statistics
    today = datetime.now().date()
    pending_count = KYCRequest.query.filter_by(status='pending').count()
    approved_today = KYCRequest.query.filter(
        KYCRequest.status == 'Verified',
        KYCRequest.approved_at >= today
    ).count()
    rejected_today = KYCRequest.query.filter(
        KYCRequest.status == 'Rejected',
        KYCRequest.rejected_at >= today
    ).count()
    total_verified = KYCRequest.query.filter_by(status='Verified').count()
    
    # Calculate approval rate
    yesterday = today - timedelta(days=1)
    approved_yesterday = KYCRequest.query.filter(
        KYCRequest.status == 'Verified',
        KYCRequest.approved_at >= yesterday,
        KYCRequest.approved_at < today
    ).count()
    
    if approved_yesterday > 0:
        approval_rate = round(((approved_today - approved_yesterday) / approved_yesterday) * 100, 1)
    else:
        approval_rate = 100 if approved_today > 0 else 0
    
    # Process KYC requests for display
    processed_requests = []
    for kyc_request, user in kyc_requests:
        # Handle document URLs - use existing id_document_path for both if separate paths don't exist
        id_doc_url = ''
        poa_doc_url = ''
        
        if kyc_request.id_document_path:
            id_doc_url = f"/static/kyc_docs/{kyc_request.id_document_path.split('/')[-1]}"
            
        if kyc_request.poa_document_path:
            poa_doc_url = f"/static/kyc_docs/{kyc_request.poa_document_path.split('/')[-1]}"
        
        # Calculate document count
        doc_count = 0
        if kyc_request.id_document_path:
            doc_count += 1
        if kyc_request.poa_document_path and kyc_request.poa_document_path != kyc_request.id_document_path:
            doc_count += 1
        
        # Calculate risk score if not set
        if not kyc_request.risk_score:
            risk_score = 0
            if not kyc_request.id_document_path:
                risk_score += 50
            if not kyc_request.id_number or len(kyc_request.id_number) < 10:
                risk_score += 30
            if not kyc_request.dob:
                risk_score += 20
            kyc_request.risk_score = min(risk_score, 100)
        
        # Add computed fields
        kyc_request.user_email = user.email
        kyc_request.user_username = user.username
        kyc_request.id_doc_url = id_doc_url
        kyc_request.poa_doc_url = poa_doc_url
        kyc_request.doc_count = doc_count
        
        processed_requests.append(kyc_request)
    
    # Calculate daily approved/rejected counts for chart
    dates = [(datetime.now() - timedelta(days=i)).strftime('%a') for i in range(6, -1, -1)]
    approval_data = {
        'labels': dates,
        'approved': [],
        'rejected': []
    }
    
    for i in range(6, -1, -1):
        day = datetime.now() - timedelta(days=i)
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        
        approved_count = KYCRequest.query.filter(
            KYCRequest.status == 'Verified',
            KYCRequest.approved_at >= day_start,
            KYCRequest.approved_at < day_end
        ).count()
        
        rejected_count = KYCRequest.query.filter(
            KYCRequest.status == 'Rejected',
            KYCRequest.rejected_at >= day_start,
            KYCRequest.rejected_at < day_end
        ).count()
        
        approval_data['approved'].append(approved_count)
        approval_data['rejected'].append(rejected_count)
    
    return render_template('admin_kyc.html',
                         kyc_requests=processed_requests,
                         pending_count=pending_count,
                         approved_today=approved_today,
                         rejected_today=rejected_today,
                         total_verified=total_verified,
                         approval_rate=approval_rate,
                         total_pending=pending_count,
                         approval_data=approval_data)

@app.route('/admin/kyc/review/<int:kyc_id>', methods=['GET', 'POST'])

def review_kyc(kyc_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    user = User.query.filter_by(username=session['user']).first()
    if not user or not user.is_admin:
        return "Access denied"
    kyc_entry = KYCRequest.query.get_or_404(kyc_id)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == "verify":
            kyc_entry.status = "Verified"
        elif action == "reject":
            kyc_entry.status = "Rejected"
        db.session.commit()
        log_activity(user.id, f"KYC {action} for user ID {kyc_entry.user_id}")
        return redirect(url_for('admin_kyc'))
    return render_template('review_kyc.html', entry=kyc_entry)

@app.route('/admin/kyc/<int:kyc_id>/approve', methods=['POST'])
@admin_required
def approve_kyc(kyc_id):
    try:
        kyc_request = KYCRequest.query.get_or_404(kyc_id)
        
        # Get admin notes from request
        data = request.get_json() or {}
        admin_notes = data.get('notes', '')
        reviewed_by = data.get('reviewed_by', 'admin')
        
        # Update KYC status
        kyc_request.status = 'Verified'
        kyc_request.approved_at = datetime.now()
        
        db.session.commit()
        
        # Enhanced activity logging with notes
        log_activity(
            session.get('user_id', 0),
            f"KYC approved for user ID {kyc_request.user_id}",
            level="info",
            details=f"KYC ID: {kyc_id}, Full Name: {kyc_request.full_name}, Admin Notes: {admin_notes}"
        )
        
        return jsonify({
            'success': True, 
            'message': 'KYC approved successfully',
            'kyc_id': kyc_id
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False, 
            'message': str(e)
        }), 500

@app.route('/admin/kyc/<int:kyc_id>/reject', methods=['POST'])
@admin_required
def reject_kyc(kyc_id):
    try:
        kyc_request = KYCRequest.query.get_or_404(kyc_id)
        
        # Get rejection reason from request
        data = request.get_json() or {}
        reason = data.get('reason', 'No reason provided')
        reviewed_by = data.get('reviewed_by', 'admin')
        
        # Update KYC status
        kyc_request.status = 'Rejected'
        kyc_request.rejected_at = datetime.now()
        
        db.session.commit()
        
        # Enhanced activity logging with rejection reason
        log_activity(
            session.get('user_id', 0),
            f"KYC rejected for user ID {kyc_request.user_id}",
            level="warning",
            details=f"KYC ID: {kyc_id}, Full Name: {kyc_request.full_name}, Reason: {reason}"
        )
        
        return jsonify({
            'success': True, 
            'message': 'KYC rejected successfully',
            'kyc_id': kyc_id
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False, 
            'message': str(e)
        }), 500


@app.route('/home')
def home():
    # Check if user is logged in
    if 'user' not in session:
        return redirect(url_for('login'))

    transactions = [{
        'value': '1000000000000000000',  # 1 ETH in wei
        'hash': '0x123...',
        'from': '0xabc...',
        'to': '0xdef...'
    }]
    
    # Get ETH price data
    try:
        # Current price
        price_res = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd')
        current_price = price_res.json().get('ethereum', {}).get('usd', 'N/A')
        
        # Historical data for graph (last 7 days)
        history_res = requests.get('https://api.coingecko.com/api/v3/coins/ethereum/market_chart?vs_currency=usd&days=7')
        price_history = history_res.json().get('prices', [])
        timestamps = [datetime.fromtimestamp(x[0]/1000).strftime('%Y-%m-%d') for x in price_history]
        prices = [x[1] for x in price_history]
    except Exception as e:
        current_price = 'N/A'
        timestamps = []
        prices = []

    # Get watchlist activity based on user role
    watchlist_activity = []
    user = User.query.filter_by(username=session['user']).first()
    if user:
        if session.get('is_admin'):
            # Admin sees activity from all watchlists
            watchlist = Watchlist.query.join(User).all()
            for item in watchlist:
                transactions_data = get_transactions(item.address)[:5]
                watchlist_activity.append({
                    'address': item.address,
                    'balance': get_account_balance(item.address),
                    'transactions': transactions_data,
                    'flagged': any(is_large_transaction(tx) for tx in transactions_data),
                    'username': item.user.username  # Add username for admin view
                })
        else:
            # Regular users see only their own watchlist activity
            watchlist = Watchlist.query.filter_by(user_id=user.id).all()
            for item in watchlist:
                transactions_data = get_transactions(item.address)[:5]
                watchlist_activity.append({
                    'address': item.address,
                    'balance': get_account_balance(item.address),
                    'transactions': transactions_data,
                    'flagged': any(is_large_transaction(tx) for tx in transactions_data)
                })
    
    return render_template('home.html',
        tx=transactions[0],
        user=user,
        price=current_price,
        timestamps=json.dumps(timestamps),  # Pre-convert to JSON string
        prices=json.dumps(prices),          # Pre-convert to JSON string
        watchlist_activity=watchlist_activity,
        transactions=transactions
    )


@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    data = generate_mock_data()
    if 'user' not in session:
        return redirect(url_for('login'))
    address = None
    balance = None
    transactions = []
    if request.method == 'POST':
        address = request.form.get('eth_address')
        if address and is_valid_eth_address(address):
            session['eth_address'] = address
            balance = get_account_balance(address)
            transactions = get_transactions(address) or []
        else:
            return "Please enter a valid Ethereum address."
    return render_template('dashboard.html', 
                         suspicious_count=data['suspicious_count'],
                         total_transactions=data['total_transactions'],
                         high_risk_wallets=data['high_risk_wallets'],
                         sars_filed=data['sars_filed'],
                         suspicious_transactions=data['suspicious_transactions'],
                         risk_trend=data['risk_trend'],
                         risk_distribution=data['risk_distribution'])

@app.route('/account')
def account():
    if 'user' not in session:
        return redirect(url_for('login'))
    username = session.get('user')
    user = User.query.filter_by(username=username).first()
    kyc = KYCRequest.query.filter_by(user_id=user.id).first() if user else None
    return render_template('account.html', user=user, kyc=kyc)
    
@app.route('/update-account', methods=['GET', 'POST'])
def update_account():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    username = session.get('user')
    user = User.query.filter_by(username=username).first()
    
    if not user:
        flash('User not found!', 'danger')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        new_email = request.form.get('email')
        
        # Verify current password
        if not check_password_hash(user.password, current_password):
            flash('Current password is incorrect!', 'danger')
            return render_template('update-accounts.html', user=user)
        
        # Update email if provided and different
        if new_email and new_email != user.email:
            # Check if email already exists
            existing_user = User.query.filter_by(email=new_email).first()
            if existing_user and existing_user.id != user.id:
                flash('Email already exists!', 'danger')
                return render_template('update-accounts.html', user=user)
            
            user.email = new_email
            
            # Log email update activity
            log_activity(
                user.id,
                "Email updated",
                level="info",
                details=f"Email changed from {user.email} to {new_email}",
                ip_address=request.remote_addr
            )
        
        # Update password if provided
        if new_password:
            if len(new_password) < 6:
                flash('New password must be at least 6 characters long!', 'danger')
                return render_template('update-accounts.html', user=user)
            
            if new_password != confirm_password:
                flash('New passwords do not match!', 'danger')
                return render_template('update-accounts.html', user=user)
            
            # Update password
            user.password = generate_password_hash(new_password)
            
            # Log password update activity
            log_activity(
                user.id,
                "Password updated",
                level="info",
                details="User successfully updated their password",
                ip_address=request.remote_addr
            )
            
            flash('Password updated successfully!', 'success')
        
        # Save changes to database
        try:
            db.session.commit()
            if new_email and not new_password:
                flash('Email updated successfully!', 'success')
            elif new_email and new_password:
                flash('Email and password updated successfully!', 'success')
        except Exception as e:
            db.session.rollback()
            flash('An error occurred while updating your account. Please try again.', 'danger')
            return render_template('update-accounts.html', user=user)
        
        return redirect(url_for('account'))
    
    return render_template('update-accounts.html', user=user)

@app.route('/track_wallet', methods=['GET', 'POST'])
def track_wallet():
    # Initialize all variables at the start
    wallet_data = None
    eth_price = None
    suspisious_txs = []
    suspisious_flags = []
    sent_txs = []
    received_txs = []
    
    if request.method == 'POST':
        address = request.form['track_address']
        
        # Log wallet tracking activity
        if 'user' in session:
            user = User.query.filter_by(username=session['user']).first()
            if user:
                log_activity(
                    user.id, 
                    f"Tracked wallet: {address}", 
                    level="info",
                    details=f"Wallet address: {address}"
                )
        
        balance = get_account_balance(address)
        transactions = get_transactions(address)
        
        if transactions is None:  # Handle case where no transactions are found
            transactions = []
            
        wallet_data = {
            'address': address,
            'balance': balance,
            'transactions': transactions
        }

        # categorize transactions
        user_address = wallet_data['address'].lower()
        
        for tx in wallet_data['transactions']:
            # Ensure transaction has 'from' field
            if 'from' in tx and tx['from'].lower() == user_address:
                sent_txs.append(tx)
            else:
                received_txs.append(tx)

        # flag suspicious transactions
        for tx in transactions:
            flag = []
            if is_large_transaction(tx):
                flag.append('Large Transaction')
            if is_to_from_suspicious_address(tx):
                flag.append("Known suspicious address")
            if flag:
                suspisious_txs.append(tx)
                suspisious_flags.append((tx, flag))
        
        # Log suspicious activity detection
        if suspisious_flags and 'user' in session:
            user = User.query.filter_by(username=session['user']).first()
            if user:
                log_activity(
                    user.id,
                    f"Suspicious activity detected on wallet: {address}",
                    level="warning",
                    details=f"Flagged transactions: {len(suspisious_flags)}, Flags: {[flag for _, flags in suspisious_flags for flag in flags]}"
                )
            
        # Frequency check
        wallet_data['high_frequency'] = is_high_frequency(transactions)
        
        # Check for alerts if user is logged in
        if 'user' in session:
            user = User.query.filter_by(username=session['user']).first()
            if user:
                # check_and_create_alerts(user.id, address, transactions, suspisious_flags)
                pass  # Temporarily disabled alert functionality

    return render_template(
        'track_wallet.html', 
        wallet_data=wallet_data, 
        sent_transactions=sent_txs,
        received_transactions=received_txs,
        suspisious_flags=suspisious_flags, 
        suspisious_txs=suspisious_txs
    )

@app.route('/watchlist', methods=['GET', 'POST'])
def watchlist_page():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user = User.query.filter_by(username=session['user']).first()
    if not user:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        address = request.form.get('address')
        if is_valid_eth_address(address):
            existing = Watchlist.query.filter_by(user_id=user.id, address=address).first()
            if not existing:
                new_watchlist = Watchlist(user_id=user.id, address=address)
                db.session.add(new_watchlist)
                db.session.commit()
                flash('Address added to watchlist', 'success')
            else:
                flash('Address already in watchlist', 'warning')
        else:
            flash('Invalid Ethereum address', 'danger')

    # Get watchlist entries based on user role
    watchlist_items = []
    if session.get('is_admin'):
        # Admin sees all watchlist entries with user information
        all_watchlist_items = Watchlist.query.join(User).all()
        for item in all_watchlist_items:
            transactions = get_transactions(item.address) or []
            balance = get_account_balance(item.address) or 0.0
            flagged = any(is_large_transaction(tx) for tx in transactions) if transactions else False
            
            watchlist_items.append({
                'address': item.address,
                'balance': balance,
                'transactions': transactions,
                'flagged': flagged,
                'user_id': item.user_id,
                'username': item.user.username  # Add username for admin view
            })
    else:
        # Regular users see only their own watchlist entries
        for item in Watchlist.query.filter_by(user_id=user.id).all():
            transactions = get_transactions(item.address) or []
            balance = get_account_balance(item.address) or 0.0
            flagged = any(is_large_transaction(tx) for tx in transactions) if transactions else False
            
            watchlist_items.append({
                'address': item.address,
                'balance': balance,
                'transactions': transactions,
                'flagged': flagged
            })
            
            # Check for alerts on this specific watchlist item
            if flagged:
                suspicious_flags = []
                for tx in transactions:
                    flags = []
                    if is_large_transaction(tx):
                        flags.append('Large Transaction')
                    if is_to_from_suspicious_address(tx):
                        flags.append('Known suspicious address')
                    if flags:
                        suspicious_flags.append((tx, flags))
                
                pass  # Temporarily disabled alert functionality

    return render_template('watchlist.html', watchlist_items=watchlist_items, is_large_transaction=is_large_transaction)

# PDF Export Routes
@app.route('/export-wallet-pdf', methods=['GET', 'POST'])
def export_wallet_pdf():
    if not is_authenticated():
        flash('Please login first', 'danger')
        return redirect(url_for('login'))
    
    try:
        user = User.query.filter_by(username=session['user']).first()
        if not user:
            flash('User session not found', 'danger')
            return redirect(url_for('login'))
            
        address = request.form.get('address')
        
        # Log PDF export attempt
        log_activity(
            user.id,
            f"Wallet PDF export requested: {address}",
            level="info",
            details=f"Export type: track_wallet, Address: {address}"
        )
            
        # Get wallet data from form
        address = request.form.get('address')
        if not address:
            flash('No wallet address provided', 'error')
            return redirect(url_for('track_wallet'))
        
        # Validate Ethereum address format
        if not is_valid_eth_address(address):
            flash('Invalid Ethereum address format', 'error')
            return redirect(url_for('track_wallet'))
        
        try:
            # Get wallet data
            balance = get_account_balance(address)
            if balance is None:
                flash('Error fetching wallet balance', 'error')
                return redirect(url_for('track_wallet'))
                
            transactions = get_transactions(address)
            if transactions is None:
                flash('Error fetching transactions', 'error')
                return redirect(url_for('track_wallet'))
                
            transactions = transactions or []
            
            wallet_data = {
                'address': address,
                'balance': balance,
                'transactions': transactions
            }
            
            # Get suspicious flags
            suspicious_flags = []
            for tx in transactions:
                flags = []
                try:
                    if is_large_transaction(tx):
                        flags.append('Large Transaction')
                    if is_to_from_suspicious_address(tx):
                        flags.append('Known suspicious address')
                    if flags:
                        suspicious_flags.append((tx, flags))
                except Exception as tx_error:
                    print(f"Error processing transaction {tx.get('hash', 'unknown')}: {str(tx_error)}")
                    continue
            
            # Generate PDF
            try:
                pdf_buffer = generate_wallet_pdf(wallet_data, suspicious_flags)
            except Exception as pdf_error:
                flash('Error generating PDF report', 'error')
                print(f"PDF generation error: {str(pdf_error)}")
                return redirect(url_for('track_wallet'))
            
            # Save export record
            try:
                export_record = PDFExport(
                    user_id=user.id,
                    export_type='track_wallet',
                    file_path=f'wallet_report_{address[:10]}.pdf'
                )
                db.session.add(export_record)
                db.session.commit()
            except Exception as db_error:
                print(f"Database error: {str(db_error)}")
                # Continue even if record saving fails
            
            # Log successful PDF generation
            log_activity(
                user.id,
                f"Wallet PDF export completed: {address}",
                level="info",
                details=f"File: wallet_report_{address[:10]}.pdf, Transactions: {len(transactions)}, Suspicious flags: {len(suspicious_flags)}"
            )
            
            return send_file(
                pdf_buffer,
                as_attachment=True,
                download_name=f'wallet_report_{address[:10]}.pdf',
                mimetype='application/pdf'
            )
            
        except Exception as data_error:
            flash('Error fetching wallet data', 'error')
            print(f"Data fetching error: {str(data_error)}")
            return redirect(url_for('track_wallet'))
            
    except Exception as e:
        # Log PDF export error
        if 'user' in session:
            user = User.query.filter_by(username=session['user']).first()
            if user:
                log_activity(
                    user.id,
                    f"Wallet PDF export failed: {address if 'address' in locals() else 'unknown'}",
                    level="error",
                    details=f"Error: {str(e)}"
                )
        print(f"PDF generation error: {str(e)}")  # Add logging
        flash(f'Error generating PDF: {str(e)}', 'danger')
        return redirect(url_for('track_wallet'))

@app.route('/export-watchlist-pdf', methods=['GET', 'POST'])
def export_watchlist_pdf():
    if not is_authenticated():
        flash('Please login first', 'danger')
        return redirect(url_for('login'))
    
    try:
        user = User.query.filter_by(username=session['user']).first()
        if not user:
            flash('User session not found', 'danger')
            return redirect(url_for('login'))
        
        # Log watchlist PDF export attempt
        log_activity(
            user.id,
            "Watchlist PDF export requested",
            level="info",
            details=f"Export type: watchlist"
        )
        
        watchlist_items = Watchlist.query.filter_by(user_id=user.id).all()
        
        # Generate PDF
        pdf_buffer = generate_watchlist_pdf(watchlist_items)
        
        # Create export record
        export_record = PDFExport(
            user_id=user.id,
            export_type='watchlist',
            file_path='watchlist_report.pdf'
        )
        db.session.add(export_record)
        db.session.commit()
        
        # Log successful PDF generation
        log_activity(
            user.id,
            "Watchlist PDF export completed",
            level="info",
            details=f"File: watchlist_report.pdf, Addresses: {len(watchlist_items)}"
        )
        
        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name='watchlist_report.pdf',
            mimetype='application/pdf'
        )
        
    except Exception as e:
        # Log PDF export error
        if 'user' in session:
            user = User.query.filter_by(username=session['user']).first()
            if user:
                log_activity(
                    user.id,
                    "Watchlist PDF export failed",
                    level="error",
                    details=f"Error: {str(e)}"
                )
        flash(f'Error generating PDF: {str(e)}', 'danger')
        return redirect(url_for('watchlist_page'))

@app.route('/export-flagged-transactions-pdf', methods=['GET', 'POST'])
def export_flagged_transactions_pdf():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user = User.query.filter_by(username=session['user']).first()
    if not user:
        return redirect(url_for('login'))
    
    # Log flagged transactions PDF export attempt
    log_activity(
        user.id,
        "Flagged transactions PDF export requested",
        level="info",
        details="Export type: flagged_transactions"
    )
    
    # Collect all flagged transactions from watchlist
    flagged_transactions = []
    for item in Watchlist.query.filter_by(user_id=user.id).all():
        transactions = get_transactions(item.address)
        for tx in transactions:
            flags = []
            if is_large_transaction(tx):
                flags.append('Large Transaction')
            if is_to_from_suspicious_address(tx):
                flags.append('Known suspicious address')
            if flags:
                flagged_transactions.append((tx, flags))
    
    if not flagged_transactions:
        log_activity(
            user.id,
            "Flagged transactions PDF export - no data found",
            level="warning",
            details="No flagged transactions available for export"
        )
        flash('No flagged transactions found', 'info')
        return redirect(url_for('watchlist_page'))
    
    # Generate PDF
    pdf_buffer = generate_flagged_transactions_pdf(flagged_transactions)
    
    # Save export record
    export_record = PDFExport(
        user_id=user.id,
        export_type='flagged_transactions',
        file_path='flagged_transactions_report.pdf'
    )
    db.session.add(export_record)
    db.session.commit()
    
    # Log successful PDF generation
    log_activity(
        user.id,
        "Flagged transactions PDF export completed",
        level="info",
        details=f"File: flagged_transactions_report.pdf, Flagged transactions: {len(flagged_transactions)}"
    )
    
    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name='flagged_transactions_report.pdf',
        mimetype='application/pdf'
    )

# Email Preferences Routes
@app.route('/email-preferences', methods=['GET', 'POST'])
def email_preferences():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user = User.query.filter_by(username=session['user']).first()
    if not user:
        return redirect(url_for('login'))
    
    # Get or create email preferences
    email_prefs = EmailPreference.query.filter_by(user_id=user.id).first()
    if not email_prefs:
        email_prefs = EmailPreference(user_id=user.id)
        db.session.add(email_prefs)
        db.session.commit()
    
    if request.method == 'POST':
        # Update user email settings
        user.email_alerts_enabled = 'email_alerts_enabled' in request.form
        user.alert_email = request.form.get('alert_email') or user.email
        
        # Update email preferences
        email_prefs.large_transaction_alerts = 'large_transaction_alerts' in request.form
        email_prefs.suspicious_address_alerts = 'suspicious_address_alerts' in request.form
        email_prefs.high_frequency_alerts = 'high_frequency_alerts' in request.form
        email_prefs.watchlist_alerts = 'watchlist_alerts' in request.form
        email_prefs.daily_summary = 'daily_summary' in request.form
        
        db.session.commit()
        flash('Email preferences updated successfully', 'success')
        return redirect(url_for('email_preferences'))
    
    return render_template('email_preferences.html', user=user, email_prefs=email_prefs)

@app.route('/alerts')
def alerts():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user = User.query.filter_by(username=session['user']).first()
    if not user:
        return redirect(url_for('login'))
    
    # Get user alerts
    alerts = Alert.query.filter_by(user_id=user.id).order_by(Alert.created_at.desc()).all()
    
    return render_template('alerts.html', alerts=alerts)

@app.route('/test-pdf')
def test_pdf():
    try:
        # Test if the function exists
        if 'generate_watchlist_pdf' in globals():
            return "Function exists"
        else:
            return "Function not found"
    except Exception as e:
        return f"Error: {str(e)}"

@app.route('/mark-alert-read/<int:alert_id>')
def mark_alert_read(alert_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user = User.query.filter_by(username=session['user']).first()
    if not user:
        return redirect(url_for('login'))
    
    alert = Alert.query.filter_by(id=alert_id, user_id=user.id).first()
    if alert:
        alert.is_read = True
        db.session.commit()
        flash('Alert marked as read', 'success')
    
    return redirect(url_for('alerts'))

@app.route('/logout')
def logout():
    if 'user' in session:
        user = User.query.filter_by(username=session['user']).first()
        if user:
            log_activity(
                user.id,
                "User logged out",
                level="info",
                details="Logout successful"
            )
    
    session.clear()
    return redirect(url_for('home'))



@app.route('/remove-from-watchlist', methods=['POST'])
def remove_from_watchlist():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user = User.query.filter_by(username=session['user']).first()
    if not user:
        return redirect(url_for('login'))
    
    address = request.form.get('address')
    if address:
        entry = Watchlist.query.filter_by(user_id=user.id, address=address).first()
        if entry:
            db.session.delete(entry)
            db.session.commit()
            log_activity(user.id, f"Removed address {address} from watchlist")
            flash('Address removed from watchlist', 'success')
    
    return redirect(url_for('watchlist_page'))

@app.template_filter('tojson')
def tojson_filter(obj):
    """Safe JSON serialization filter with undefined handling"""
    if obj is None or isinstance(obj, Undefined):
        return 'null'
    try:
        return json.dumps(obj)
    except TypeError:
        return '{}'

@app.route('/user/<int:user_id>')
def user_info(user_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get_or_404(user_id)
    kyc = KYCRequest.query.filter_by(user_id=user_id).first()
    activity_logs = ActivityLog.query.filter_by(user_id=user_id).order_by(ActivityLog.timestamp.desc()).limit(10).all()
    
    return render_template('user_info.html',
                         user=user,
                         kyc=kyc,
                         activity_logs=activity_logs)

@app.route('/api/risk-trend/<timeframe>')
def risk_trend(timeframe):
    data = generate_mock_data()
    return jsonify(data['risk_trend'][timeframe])

def get_extended_transactions(address, count=50):
    """Get extended transaction list for an address"""
    url = f"{BASE_URL}?module=account&action=txlist&address={address}&sort=desc&page=1&offset={count}&apikey={ETHERSCAN_API_KEY}"
    try:
        response = requests.get(url)
        data = response.json()
        return data.get('result', [])
    except Exception as e:
        print(f"Error fetching transactions: {e}")
        return []
    try:
        response = requests.get(url)
        data = response.json()
        return data.get('result', [])
    except Exception as e:
        print(f"Error fetching transactions: {e}")
        return []

def get_balance_history_data(address, days=30):
    """Get balance history for the past specified days"""
    try:
        current_balance = get_account_balance(address) or 0.0
        transactions = get_extended_transactions(address, 100)  # Get more transactions for history
        
        # Create balance history from transaction data
        from datetime import datetime, timedelta
        import random
        
        history = []
        base_date = datetime.now() - timedelta(days=days)
        
        for i in range(days):
            date = base_date + timedelta(days=i)
            # Create realistic balance variations based on transaction patterns
            if transactions:
                # Use transaction data to create more realistic history
                daily_txs = [tx for tx in transactions if 
                           abs(int(tx['timeStamp']) - int(date.timestamp())) < 86400]
                if daily_txs:
                    # Calculate balance change based on transactions
                    balance_change = sum([int(tx['value'])/1e18 for tx in daily_txs 
                                        if tx['to'].lower() == address.lower()]) - \
                                   sum([int(tx['value'])/1e18 for tx in daily_txs 
                                        if tx['from'].lower() == address.lower()])
                    balance = max(0, current_balance + balance_change)
                else:
                    variation = random.uniform(-0.05, 0.05) * current_balance
                    balance = max(0, current_balance + variation)
            else:
                variation = random.uniform(-0.05, 0.05) * current_balance
                balance = max(0, current_balance + variation)
            
            history.append({
                'date': date.strftime('%Y-%m-%d'),
                'balance': round(balance, 6)
            })
        
        return history
    except Exception as e:
        print(f"Error generating balance history: {e}")
        return []

@app.route('/api/wallet-balance-history/<address>')
def get_wallet_balance_history(address):
    """Get balance history for a wallet address"""
    if not is_valid_eth_address(address):
        return jsonify({'error': 'Invalid address'}), 400
    
    try:
        # Get current balance
        current_balance = get_account_balance(address)
        history = get_balance_history_data(address)
        
        return jsonify({
            'address': address,
            'current_balance': current_balance,
            'history': history
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/watchlist-balances')
def get_watchlist_balances():
    """Get current balances for all watchlist addresses"""
    if 'user' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    user = User.query.filter_by(username=session['user']).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    try:
        watchlist_items = Watchlist.query.filter_by(user_id=user.id).all()
        balances = []
        
        for item in watchlist_items:
            current_balance = get_account_balance(item.address)
            balances.append({
                'address': item.address,
                'balance': current_balance,
                'timestamp': int(datetime.now().timestamp())
            })
        
        return jsonify({'balances': balances})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_labels_for_timeframe(timeframe):
    if timeframe == 'weekly':
        return ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    elif timeframe == 'monthly':
        return [f'Week {i+1}' for i in range(12)]
    return [f'202{9+i}' for i in range(5)]

if __name__ == '__main__':
    with app.app_context():
        db.create_all()

    app.run(debug=True)



    