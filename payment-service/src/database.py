from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import datetime

db = SQLAlchemy()
bcrypt = Bcrypt()

class User(db.Model):
    user_id = db.Column(db.Integer, primary_key=True)
    credits = db.Column(db.Float, nullable=False, default=100.0)
