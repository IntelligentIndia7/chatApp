from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import asyncio
import redis
import pika
import json
import asyncpg
from typing import Optional
import uuid
import logging
from groq import AsyncGroq  # Groq's official async Python client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
redis_client = redis.StrictRedis(host='redis', port=6379, db=0, decode_responses=True)
db_pool = None

# Initialize Groq client
groq_client = AsyncGroq(api_key="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")  # Replace with your Groq API key

# Get LLM response from Groq API
async def get_groq_response(prompt: str) -> str:
    try:
        response = await groq_client.chat.completions.create(
            model="llama3-8b-8192",  # Fast and efficient model; swap for others like "mixtral-8x7b-32768"
            messages=[{"role": "user", "content": prompt}],
            stream=True  # Enable streaming
        )
        full_response = ""
        async for chunk in response:
            if chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                full_response += content
                yield content  # Yield chunks for streaming
        yield full_response  # Return full response for caching/history
    except Exception as e:
        logger.error(f"Groq API error: {e}")
        raise

# Database setup with schema initialization
async def init_db():
    global db_pool
    for _ in range(5):
        try:
            db_pool = await asyncpg.create_pool(
                user='user', password='password', database='chat_db', host='db'
            )
            logger.info("Database connection established")
            # Create chat_history table if it doesn't exist
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS chat_history (
                        id SERIAL PRIMARY KEY,
                        user_id VARCHAR(50) NOT NULL,
                        message TEXT NOT NULL,
                        response TEXT NOT NULL,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                logger.info("Chat history table initialized")
            return
        except Exception as e:
            logger.warning(f"Database connection failed: {e}, retrying...")
            await asyncio.sleep(2)
    raise Exception("Failed to connect to database after retries")

# Store chat history
async def store_chat_history(user_id: str, prompt: str, response: str):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO chat_history (user_id, message, response) VALUES ($1, $2, $3)",
                user_id, prompt, response
            )
    except Exception as e:
        logger.error(f"Failed to store chat history: {e}")

# Queue message to RabbitMQ
def send_to_queue(user_id: str, prompt: str):
    try:
        connection = pika.BlockingConnection(pika.ConnectionParameters('rabbitmq'))
        channel = connection.channel()
        channel.queue_declare(queue='chat_tasks', durable=True)
        message = json.dumps({'user_id': user_id, 'prompt': prompt})
        channel.basic_publish(exchange='', routing_key='chat_tasks', body=message)
        connection.close()
    except Exception as e:
        logger.error(f"Failed to send to RabbitMQ: {e}")

# Cache operations
async def get_cached_response(user_id: str, prompt: str) -> Optional[str]:
    try:
        cache_key = f"chat:{user_id}:{hash(prompt)}"
        return redis_client.get(cache_key)
    except Exception as e:
        logger.error(f"Redis get error: {e}")
        return None

async def set_cached_response(user_id: str, prompt: str, response: str):
    try:
        cache_key = f"chat:{user_id}:{hash(prompt)}"
        redis_client.setex(cache_key, 3600, response)
    except Exception as e:
        logger.error(f"Redis set error: {e}")

# WebSocket endpoint
@app.websocket("/chat")
async def chat_endpoint(websocket: WebSocket):
    await websocket.accept()
    user_id = str(uuid.uuid4())
    logger.info(f"WebSocket connected for user: {user_id}")
    try:
        while True:
            try:
                prompt = await websocket.receive_text()
                logger.info(f"Received prompt from {user_id}: {prompt}")

                # Check cache
                cached = await get_cached_response(user_id, prompt)
                if cached:
                    await websocket.send_text(f"[Cached] {cached}")
                    continue

                # Process message with Groq
                send_to_queue(user_id, prompt)
                response_generator = get_groq_response(prompt)
                full_response = ""
                async for chunk in response_generator:
                    await websocket.send_text(chunk)
                    full_response += chunk

                # Cache and store
                await set_cached_response(user_id, prompt, full_response)
                await store_chat_history(user_id, prompt, full_response)

            except WebSocketDisconnect:
                logger.info(f"User {user_id} disconnected")
                break
            except Exception as e:
                logger.error(f"Error processing message for {user_id}: {e}")
                await websocket.send_text(f"Error: {str(e)}")
    except Exception as e:
        logger.error(f"WebSocket fatal error for {user_id}: {e}")
    finally:
        logger.info(f"Closing WebSocket for {user_id}")

@app.get("/")
async def get():
    html = """
    <!DOCTYPE html>
    <html>
        <body>
            <h1>Chat App Powered by Groq Cloud</h1>
            <p>Connect using a WebSocket client to ws://localhost:8000/chat</p>
        </body>
    </html>
    """
    return HTMLResponse(html)

@app.on_event("startup")
async def startup_event():
    await init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)