from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from redis import Redis
from flask import Response
import json
from database import db, Pay, User, bcrypt
import os
import logging
import threading
import requests
from prometheus_client import Counter, generate_latest
from flask import Response
from opentelemetry import trace
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes


service_name = "my-payment-service" 
resource = Resource(attributes={
    ResourceAttributes.SERVICE_NAME: service_name
})
app = Flask(__name__)

trace.set_tracer_provider(TracerProvider(resource=resource))

otlp_exporter = OTLPSpanExporter(
    endpoint="http://localhost:4317",  
    insecure=True
)

trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(otlp_exporter))

FlaskInstrumentor().instrument_app(app)

tracer = trace.get_tracer(__name__) 

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(name)s: %(message)s')
logger = logging.getLogger(__name__)

basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] =\
        'sqlite:///' + os.path.join(basedir, 'database.db')
  
db.init_app(app)
bcrypt.init_app(app)

REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
r = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

payment_processed_counter = Counter('payment_processed_total', 'Total number of processed payments')
payment_timeout_counter = Counter('payment_timeout_total', 'Total number of timeout payments')
payment_insufficient_counter = Counter('payment_insufficient_total', 'Total number of insufficient payments')
payment_failure_counter = Counter('payment_failure_total', 'Total number of failed payments')

@app.route('/metrics')
def serve_metrics():
    return Response(generate_latest(), mimetype="text/plain")

@app.route('/users/<int:user_id>', methods=['GET'])
def get_user_credits(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({'credits': user.credits}), 200

@app.route('/process_payment', methods=['POST'])
def process_payment():
    with tracer.start_as_current_span("process_payment") as span:
        try:
            data = request.json
            logger.info(f"Processing payment for order {data['order_id']} by user {data['user_id']}")

            span.set_attribute("order_id", data['order_id'])
            span.set_attribute("user_id", data['user_id'])
            span.set_attribute("amount", data['amount'])

            user_id = data['user_id']
            order_id = data['order_id']
            purchase_amount = data['amount']
            product_id= data['product_id']
            user = User.get_or_create(user_id)
            
            try:
                if order_id==999:
                    raise TimeoutError("Simulated payment processing timeout.")
                
                user.credits -= purchase_amount

                if user.credits < purchase_amount:
                    rollback_payment(order_id)
                    r.publish('payment_status', json.dumps({'order_id': order_id, 'status': 'INSUFFICIENT_FUND'})), 500
                    payment_insufficient_counter.inc()
                    return jsonify({'status': 'INSUFFICIENT_FUND', 'message': 'Not enough credits'}), 400
        
                else:
                    payment = Pay.get_or_create(order_id,order_id,purchase_amount)
                    db.session.commit()
                    r.publish('payment_status', json.dumps({'order_id': order_id, 'status': 'SUCCESS', 'amount':purchase_amount,'product_id': product_id}))
                    payment_processed_counter.inc()

                    return jsonify({'status': 'SUCCESS', 'message': 'Payment processed', 'remaining_credits': user.credits}), 200
            
            except TimeoutError:
                logger.warning(f"Timeout occurred while processing payment for order {order_id}")
                r.publish('payment_status', json.dumps({'order_id': order_id, 'status': 'TIMEOUT'}))
                # span.record_exception(e)
                # span.set_status(trace.Status(trace.StatusCode.ERROR, "Timeout processing payment"))   
                payment_timeout_counter.inc()
                return jsonify({'status': 'TIMEOUT', 'message': 'Payment processing timeout'}), 500
        except Exception as e:
                db.session.rollback()
                logger.error(f"Unexpected error while processing payment for order {order_id}: {e}", exc_info=True)
                r.publish('payment_status', json.dumps({'order_id': order_id, 'status': 'UNKNOWN', 'error': str(e)}))
                payment_failure_counter.inc()
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Error processing payment"))
                return jsonify({'status': 'UNKNOWN', 'message': str(e)}), 500

def handle_order_created_event(message):
    with tracer.start_as_current_span("handle_order_created_event"):
        pubsub = r.pubsub()
        pubsub.subscribe('order_created')
        logger.info("Received order created event")
        for message in pubsub.listen():
            if message['type'] == 'message':
                with app.app_context():
                    order_data = json.loads(message['data'])
                    payment = Pay(order_id=order_data['order_id'],status='unpaid',amount=order_data['amount'])
                    db.session.add(payment)
                    db.session.commit()

def rollback_payment(order_id):
    with app.app_context():
        with tracer.start_as_current_span("rollback_payment") as span:
            span.set_attribute("order_id", order_id)
            payment = Pay.query.filter_by(order_id=order_id).first()

            if payment:
                user = User.query.get(payment.user_id)
                if user:
                    user.credits += payment.amount
                    db.session.commit()
                    logger.info("Rolled back payment")
                    r.publish('payment_status', json.dumps({'order_id': order_id, 'status': 'REFUND'}))
                    return jsonify({'status': 'REFUND', 'message': 'Payment refunded due to inventory failure'}), 500

                else:
                    print(f"User not found for payment id {payment.id}")
            else:
                print(f"No payment found for order id {order_id}")


def handle_inventory_failure_event(message):
    data = json.loads(message['data'])
    order_id = data['order_id']
    
    rollback_payment(order_id)

def start_listeners():
    pubsub = r.pubsub()
    pubsub.subscribe(**{
        'order_created': handle_order_created_event,
        'inventory_failure': handle_inventory_failure_event
    })
    logger.info("Started listeners in payment service")
    for message in pubsub.listen():
        if message['type'] == 'message':
            if message['channel'] == 'order_created':              
                logger.info(f"Received prder created message: {message['data']}")
                handle_order_created_event(message)
            elif message['channel'] == 'inventory_failure':  
                logger.info(f"Received inventory failure message: {message['data']}")
                handle_inventory_failure_event(message)

if __name__ == '__main__':
    logger.info("Starting Flask application and initializing database")
    with app.app_context():
        db.create_all()
    logger.info("Database initialized")

    thread = threading.Thread(target=start_listeners)
    thread.start()
    logger.info("Background thread for listeners started")

    app.run(debug=True, port=5001)
