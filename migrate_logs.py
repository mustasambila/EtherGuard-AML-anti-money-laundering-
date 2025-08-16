from app import app
from models import db
from sqlalchemy import text

with app.app_context():
    try:
        # Add new columns to ActivityLog table
        db.session.execute(text('ALTER TABLE activity_log ADD COLUMN details TEXT'))
        db.session.execute(text('ALTER TABLE activity_log ADD COLUMN ip_address VARCHAR(45)'))
        db.session.commit()
        print("Database migration completed successfully!")
    except Exception as e:
        print(f"Migration error: {e}")
        db.session.rollback()