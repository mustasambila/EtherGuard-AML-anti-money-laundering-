from models import db, User
from app import app

with app.app_context():
    username = "Admin"  # Change to your desired username
    user = User.query.filter_by(username=username).first()
    if user:
        user.is_admin = True
        db.session.commit()
        print(f"{username} has been granted admin rights.")
    else:
        print("User not found.")
