from flask import Flask, request, jsonify, Blueprint,current_app
from database import User, db
from redis import Redis
import requests
import json
import os
import logging
from opentelemetry import trace
from opentelemetry import metrics

payment_service = Blueprint("payment_service", __name__)

SECRET_KEY = 'your_secret_key'
REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
ORDER_SERVICE_URL = "http://127.0.0.1:8080" 
r = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
pubsub = r.pubsub()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

tracer = trace.get_tracer("...tracer")
meter = metrics.get_meter("...meter")

@payment_service.route('/')
def home():
    return "Payment Microservice is running!"

def handle_order_created_event(message):
    with current_app.app_context():
        data = json.loads(message['data'])
    # @payment_service.route('/process_payment', methods=['POST'])
    # def process_payment():
    #     data = request.json
        user_id = data['customer_id']
        new= data['new']
        purchase_amount = data['amount']
        
        if new:
                user = User(user_id=user_id)  
                db.session.add(user)
                db.session.commit()
        else:
            user = User.query.get(user_id)
        if user.credits < purchase_amount:
            return jsonify({'status': 'INSUFFICIENT_FUND', 'message': 'Not enough credits'}), 400

        try:
            user.credits -= purchase_amount
            db.session.commit()

            # notify_inventory_service(order_id, items)
            # listen_for_inventory_updates()

            return jsonify({'status': 'SUCCESS', 'message': 'Payment processed', 'remaining_credits': user.credits})
        except:
            db.session.rollback()
            message = json.dumps({'order_id': order_id})
            r.publish('payment_failure', json.dumps({'order_id': order_data['order_id']}))

            return jsonify({'status': 'UNKNOWN', 'message': 'An error occurred'}), 500

pubsub.subscribe(**{'order_created': handle_order_created_event})
pubsub.run_in_thread(sleep_time=0.001)


def update_order_status(order_id, status):
    try:
        order_response = requests.get(f"{ORDER_SERVICE_URL}/orders/{order_id}")
        if order_response.status_code == 200:
            r.set(f"order_{order_id}", order_response.text)

        response = requests.put(f"{ORDER_SERVICE_URL}/orders/{order_id}", json={"status": status})
        if response.status_code != 200:
            raise Exception("Failed to update order status")

    except Exception as e:
        old_order_data = r.get(f"order_{order_id}")
        if old_order_data:
            requests.put(f"{ORDER_SERVICE_URL}/orders/{order_id}", json=json.loads(old_order_data))
        else:
            print("Failed to revert order status: Previous state not found in Redis")

def listen_for_inventory_updates():
    pubsub.subscribe(**{'inventory_update': handle_inventory_update})
    thread = pubsub.run_in_thread(sleep_time=0.001)
    return thread

def handle_inventory_update(message):
    data = json.loads(message['data'])
    order_id = data['order_id']
    success = data['success']

    if not success:
        rollback_payment(order_id)

