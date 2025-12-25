import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from livekit.api import AccessToken, VideoGrants
from dotenv import load_dotenv

# Load environment variables if .env.local exists locally
if os.path.exists(".env.local"):
    load_dotenv(".env.local")

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend

LIVEKIT_API_KEY = os.getenv('LIVEKIT_API_KEY')
LIVEKIT_API_SECRET = os.getenv('LIVEKIT_API_SECRET')

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'name': 'ProjectGen Token Server',
        'status': 'Environment configured' if LIVEKIT_API_KEY and LIVEKIT_API_SECRET else 'Environment missing credentials'
    })

@app.route('/getToken', methods=['GET'])
def get_token():
    name = request.args.get('name', 'user')
    room = request.args.get('room', 'my-room')
    
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        return jsonify({'error': 'LiveKit credentials not configured on server'}), 500
    
    token = AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
        .with_identity(name) \
        .with_name(name) \
        .with_grants(VideoGrants(
            room_join=True,
            room=room,
        ))
    
    jwt_token = token.to_jwt()
    return jsonify({'token': jwt_token})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    # Use PORT environment variable if available (for Render/Railway)
    port = int(os.environ.get("PORT", 5001))
    print(f"Starting Token Server on port {port}")
    app.run(host='0.0.0.0', port=port)
