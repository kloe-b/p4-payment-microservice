FROM python:3.9

WORKDIR /app

COPY ./payment-service/requirements.txt .

COPY ./payment-service/src ./src

RUN pip install -r requirements.txt

EXPOSE 8080

CMD ["python", "./src/app.py"]

