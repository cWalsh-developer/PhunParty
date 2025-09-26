# Production Environment Configuration for PhunParty API

## Environment Variables

Add these to your production environment (Linode server):

```bash
# API Configuration
API_URL=https://api.phun.party
WEB_URL=https://phun.party

# Database Configuration (already configured)
DATABASE_URL=postgresql://username:password@host:port/database
# OR individual DB variables:
# DB_User=postgres
# DB_Password=your_password
# DB_Host=localhost
# DB_Name=PhunParty
# DB_Port=5432

# Security
SECRET_KEY=your_secret_key_here
API_KEY=your_api_key_here

# Twilio (for SMS features)
TWILIO_SID=your_twilio_sid
TWILIO_AUTH_TOKEN=your_twilio_token
TWILIO_PHONE_NUMBER=your_twilio_number
```

## Local Development vs Production

### Local Development (.env or credentials.env):

```bash
API_URL=http://localhost:8000
WEB_URL=http://localhost:3000
```

### Production Environment:

```bash
API_URL=https://api.phun.party
WEB_URL=https://phun.party
```

## WebSocket URLs

The system will automatically generate the correct WebSocket URLs:

- **Production**: `wss://api.phun.party/ws/session/{session_code}`
- **Local**: `ws://localhost:8000/ws/session/{session_code}`

## Deployment Checklist

### 1. Server Configuration

- ✅ Ensure WebSocket support in your web server (nginx/Apache)
- ✅ SSL/TLS configured for HTTPS (required for WSS)
- ✅ Environment variables set

### 2. Dependencies

```bash
# Install required packages on production
pip install -r requirements.txt
```

### 3. Nginx Configuration Example

```nginx
# Add to your nginx config for WebSocket support
location /ws/ {
    proxy_pass http://localhost:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

### 4. Testing Production WebSockets

```javascript
// Test WebSocket connection in browser console
const ws = new WebSocket("wss://api.phun.party/ws/session/TEST123");
ws.onopen = () => console.log("Connected!");
ws.onmessage = (event) => console.log("Message:", event.data);
```

## API Endpoints Ready for Production

### Session Management

- `POST /game/create/session` - Create new game session
- `GET /game/session/{session_code}/join-info` - Get session join information

### WebSocket Connection

- `ws://api.phun.party/ws/session/{session_code}` - Real-time game communication

### Frontend Integration

Your existing frontend QR codes pointing to:

```
https://phun.party/#/join/{session_code}
```

Will work perfectly with the WebSocket system!
