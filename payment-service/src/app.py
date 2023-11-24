from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from redis import Redis
import json
from database import db, Payment, User, bcrypt
import os
import threading

app = Flask(__name__)

basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] =\
        'sqlite:///' + os.path.join(basedir, 'database.db')
  
db.init_app(app)
bcrypt.init_app(app)

REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
r = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


@app.route('/process_payment', methods=['POST'])
def process_payment():
    data = request.json
    user_id = data['user_id']
    order_id = data['order_id']
    purchase_amount = data['amount']

    user = User.get_or_create(user_id)

    if user.credits < purchase_amount:
        return jsonify({'status': 'INSUFFICIENT_FUND', 'message': 'Not enough credits'}), 400

    try:
        # time.sleep(5)  
        user.credits -= purchase_amount
        db.session.commit()
        r.publish('payment_status', json.dumps({'order_id': order_id, 'status': 'SUCCESS'}))
        return jsonify({'status': 'SUCCESS', 'message': 'Payment processed', 'remaining_credits': user.credits})
    
    except TimeoutError:
        return jsonify({'status': 'TIMEOUT', 'message': 'Payment processing timeout'}), 500
    except:
        db.session.rollback()
        r.publish('payment_status', json.dumps({'order_id': order_id, 'status': 'UNKNOWN'}))
        return jsonify({'status': 'UNKNOWN', 'message': 'An error occurred'}), 500


def handle_order_created_event():
    pubsub = r.pubsub()
    pubsub.subscribe('order_created')

    for message in pubsub.listen():
        if message['type'] == 'message':
            order_data = json.loads(message['data'])
            print(f"Order created event received: {order_data}")

def rollback_payment(order_id):
    payment = Payment.query.filter_by(order_id=order_id, status='SUCCESS').first()
    if payment:
        user = User.query.get(payment.user_id)
        user.credits += payment.amount
        payment.status = 'REFUNDED'
        db.session.commit()
        r.publish('payment_rollback', json.dumps({'order_id': order_id, 'status': 'REFUNDED'}))

def handle_inventory_failure_event(message):
    data = json.loads(message['data'])
    order_id = data['order_id']
    rollback_payment(order_id)

r.pubsub().subscribe(**{'inventory_failure': handle_inventory_failure_event})

if __name__ == '__main__':
    with app.app_context():
        db.create_all()

    thread = threading.Thread(target=handle_order_created_event)
    thread.start()
    
    app.run(debug=True, port=5001)
