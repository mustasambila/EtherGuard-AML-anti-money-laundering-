from app import app
from models import db
from sqlalchemy import text

with app.app_context():
    try:
        # Add new columns to KYCRequest table
        db.session.execute(text('ALTER TABLE kyc_requests ADD COLUMN id_document_path VARCHAR(255)'))
        db.session.execute(text('ALTER TABLE kyc_requests ADD COLUMN poa_document_path VARCHAR(255)'))
        db.session.commit()
        print("KYC database migration completed successfully!")
    except Exception as e:
        print(f"Migration error: {e}")
        db.session.rollback()