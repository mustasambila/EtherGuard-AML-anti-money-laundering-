from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    # New fields for email preferences
    email_alerts_enabled = db.Column(db.Boolean, default=True)
    alert_email = db.Column(db.String(150))  # Optional separate alert email
    
    # Relationships
    kyc_request = db.relationship('KYCRequest', back_populates='user', uselist=False, cascade='all, delete-orphan')
    watchlist_items = db.relationship('Watchlist', backref='owner', lazy=True)
    activity_logs = db.relationship('ActivityLog', back_populates='user')
    email_preferences = db.relationship('EmailPreference', back_populates='user', uselist=False)
    alerts = db.relationship('Alert', back_populates='user')

    def __repr__(self):
        return f'<User {self.id} {self.username}>'

class ActivityLog(db.Model):
    __tablename__ = 'activity_log'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    activity = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text, nullable=True)  # Additional details
    ip_address = db.Column(db.String(45), nullable=True)  # User IP address
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    level = db.Column(db.String(20), default='info')
    
    user = db.relationship('User', back_populates='activity_logs')

    def __repr__(self):
        return f'<ActivityLog {self.id} {self.activity}>'  # Fixed: using 'activity' instead of 'activity_type'

class Watchlist(db.Model):
    __tablename__ = 'watchlist'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    
    user = db.relationship('User', back_populates='watchlist_items')  # Added back_populates

class KYCRequest(db.Model):
    __tablename__ = 'kyc_requests'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    full_name = db.Column(db.String(255), nullable=False)
    dob = db.Column(db.String(20))
    address = db.Column(db.String(255))
    id_number = db.Column(db.String(50))
    id_document_path = db.Column(db.String(255))  # Separate field for ID document
    poa_document_path = db.Column(db.String(255))  # Separate field for proof of address
    status = db.Column(db.String(50))
    submitted_at = db.Column(db.DateTime)
    approved_at = db.Column(db.DateTime)
    rejected_at = db.Column(db.DateTime)
    risk_score = db.Column(db.Float)
    
    user = db.relationship('User', back_populates='kyc_request')
    def __repr__(self):
        return f'<KYCRequest {self.id} {self.status}>'

class Log(db.Model):
    __tablename__ = 'logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # ✅ fixed
    action = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.DateTime, default=db.func.now())

    user = db.relationship('User', backref='logs', lazy=True)
  # Added default
    
    def __repr__(self):
        return f'<Log {self.id} {self.level}>'  # Fixed: using proper attributes

# New models for email alerts and preferences
class EmailPreference(db.Model):
    __tablename__ = 'email_preferences'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    large_transaction_alerts = db.Column(db.Boolean, default=True)
    suspicious_address_alerts = db.Column(db.Boolean, default=True)
    high_frequency_alerts = db.Column(db.Boolean, default=True)
    watchlist_alerts = db.Column(db.Boolean, default=True)
    daily_summary = db.Column(db.Boolean, default=False)
    
    user = db.relationship('User', back_populates='email_preferences')

class Alert(db.Model):
    __tablename__ = 'alerts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    alert_type = db.Column(db.String(50), nullable=False)  # 'large_transaction', 'suspicious_address', etc.
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    wallet_address = db.Column(db.String(255))
    transaction_hash = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False)
    email_sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', back_populates='alerts')

class PDFExport(db.Model):
    __tablename__ = 'pdf_exports'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    export_type = db.Column(db.String(50), nullable=False)  # 'track_wallet', 'watchlist', 'flagged_transactions'
    file_path = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='pdf_exports')