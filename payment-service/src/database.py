from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import datetime

db = SQLAlchemy()
bcrypt = Bcrypt()

class Pay(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(50), nullable=False, default='pending')
    amount = db.Column(db.Float, nullable=False)
    @staticmethod
    def get_or_create(payment_id,order_id,amount):
        payment = Pay.query.get(payment_id)
        if not payment:
            payment = Pay(id=payment_id,order_id=order_id,status='pending',amount=amount)
            db.session.add(payment)
            db.session.commit()
        return payment
    def to_dict(self):
        return {
            'id': self.id,
            'order_id': self.order_id,
            'status': self.status,
            'amount': self.amount
        }
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    credits = db.Column(db.Float, default=100.0)  

    @staticmethod
    def get_or_create(user_id):
        user = User.query.get(user_id)
        if not user:
            user = User(id=user_id)
            db.session.add(user)
            db.session.commit()
        return user
