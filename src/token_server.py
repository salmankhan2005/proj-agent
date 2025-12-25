import os
import time
import jwt
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables if .env.local exists locally
if os.path.exists(".env.local"):
    load_dotenv(".env.local")

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend

LIVEKIT_API_KEY = os.getenv('LIVEKIT_API_KEY')
LIVEKIT_API_SECRET = os.getenv('LIVEKIT_API_SECRET')

def create_access_token(api_key, api_secret, participant_name, room_name):
    """
    Manually generate a LiveKit Access Token using PyJWT.
    Payload structure:
    {
        "exp": <expiration_time>,
        "iss": <api_key>,
        "sub": <participant_name>,
        "nbf": <not_before_time>,
        "video": {
            "room": <room_name>,
            "roomJoin": true
        },
        "name": <participant_name>
    }
    """
    now = int(time.time())
    exp = now + (6 * 60 * 60) # Token valid for 6 hours
    
    payload = {
        "iss": api_key,
        "sub": participant_name,
        "nbf": now,
        "exp": exp,
        "name": participant_name,
        "video": {
            "room": room_name,
            "roomJoin": True
        }
    }
    
    # Sign the token with HS256
    token = jwt.encode(payload, api_secret, algorithm="HS256")
    return token

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'name': 'ProjectGen Token Server (Lightweight)',
        'status': 'Environment configured' if LIVEKIT_API_KEY and LIVEKIT_API_SECRET else 'Environment missing credentials'
    })

@app.route('/getToken', methods=['GET'])
def get_token():
    name = request.args.get('name', 'user')
    room = request.args.get('room', 'my-room')
    
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        return jsonify({'error': 'LiveKit credentials not configured on server'}), 500
    
    try:
        jwt_token = create_access_token(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, name, room)
        return jsonify({'token': jwt_token})
    except Exception as e:
        return jsonify({'error': f'Failed to generate token: {str(e)}'}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    # Use PORT environment variable if available (for Render/Railway) -- still useful for Vercel/local
    port = int(os.environ.get("PORT", 5001))
    print(f"Starting Token Server on port {port}")
    app.run(host='0.0.0.0', port=port)
