from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import asyncio
import redis
import pika
import json
import asyncpg
from typing import Optional
import uuid

app = FastAPI()
redis_client = redis.StrictRedis(host='redis', port=6379, db=0, decode_responses=True)
db_pool = None

# Simulate LLM response (replace with real LLM inference)
async def simulate_llm(prompt: str) -> str:
    await asyncio.sleep(0.5)  # Simulate latency
    return f"Response to: {prompt}"

# Database setup
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(
        user='user', password='password', database='chat_db', host='db'
    )

# Store chat history in PostgreSQL
async def store_chat_history(user_id: str, prompt: str, response: str):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO chat_history (user_id, message, response) VALUES ($1, $2, $3)",
            user_id, prompt, response
        )

# Queue message to RabbitMQ
def send_to_queue(user_id: str, prompt: str):
    connection = pika.BlockingConnection(pika.ConnectionParameters('rabbitmq'))
    channel = connection.channel()
    channel.queue_declare(queue='chat_tasks', durable=True)
    message = json.dumps({'user_id': user_id, 'prompt': prompt})
    channel.basic_publish(exchange='', routing_key='chat_tasks', body=message)
    connection.close()

# Check Redis cache
async def get_cached_response(user_id: str, prompt: str) -> Optional[str]:
    cache_key = f"chat:{user_id}:{hash(prompt)}"
    return redis_client.get(cache_key)

# Set Redis cache
async def set_cached_response(user_id: str, prompt: str, response: str):
    cache_key = f"chat:{user_id}:{hash(prompt)}"
    redis_client.setex(cache_key, 3600, response)

# WebSocket endpoint
@app.websocket("/chat")
async def chat_endpoint(websocket: WebSocket):
    await websocket.accept()
    user_id = str(uuid.uuid4())  # Simple user ID for demo
    try:
        while True:
            prompt = await websocket.receive_text()
            # Check cache first
            cached = await get_cached_response(user_id, prompt)
            if cached:
                await websocket.send_text(f"[Cached] {cached}")
                continue

            # Queue for processing
            send_to_queue(user_id, prompt)
            # Simulate streaming (replace with real LLM stream)
            response = await simulate_llm(prompt)
            for i in range(3):  # Stream in chunks
                chunk = f"{response} [Chunk {i+1}/3]"
                await websocket.send_text(chunk)
                await asyncio.sleep(0.2)

            # Cache and store history
            await set_cached_response(user_id, prompt, response)
            await store_chat_history(user_id, prompt, response)
    except Exception as e:
        await websocket.close()
        print(f"WebSocket error: {e}")

# Simple HTML client for testing
@app.get("/")
async def get():
    html = """
    <!DOCTYPE html>
    <html>
        <body>
            <h1>Chat App Prototype</h1>
            <input id="message" type="text" placeholder="Type a message">
            <button onclick="sendMessage()">Send</button>
            <div id="chat"></div>
            <script>
                const ws = new WebSocket("ws://localhost:8000/chat");
                ws.onmessage = (event) => {
                    const chat = document.getElementById("chat");
                    chat.innerHTML += "<p>" + event.data + "</p>";
                };
                function sendMessage() {
                    const input = document.getElementById("message");
                    ws.send(input.value);
                    input.value = "";
                }
            </script>
        </body>
    </html>
    """
    return HTMLResponse(html)

# Background worker for RabbitMQ (run separately or in another process)
def start_worker():
    connection = pika.BlockingConnection(pika.ConnectionParameters('rabbitmq'))
    channel = connection.channel()
    channel.queue_declare(queue='chat_tasks', durable=True)

    def callback(ch, method, properties, body):
        task = json.loads(body)
        print(f"Processed queued task: {task}")
        # Here you would call LLM and send response (e.g., via WebSocket)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(queue='chat_tasks', on_message_callback=callback)
    channel.start_consuming()

@app.on_event("startup")
async def startup_event():
    await init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)