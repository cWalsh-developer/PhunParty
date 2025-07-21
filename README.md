<div align=center>

# 🎉 PhunParty (Working Title)

</div>



An in-progress multiplayer party quiz game designed for living rooms.

- **TV or laptop acts as the game host**
- **Players join using their phones (scanning QR codes or entering game pins)**
- Play trivia, music-based guessing rounds, categorical guessing games etc. Players can generate their own games
- Game scores update in real time
- Inspired by real family game nights 🎧📱🧠

---

## 🧠 Motivation

Most quiz apps are either:
- Built for solo play
- Limited to single-device use
- Or lack the personal touch of setting your own questions and games to play

This project combines:
- Custom trivia rounds
- Music-guessing mode
- Sync between mobile players and a shared "host" screen

---

## 💻 Tech Stack

| Layer | Tech |
|-------|------|
| **Backend** | FastAPI (Python), REST + WebSocket, Pydantic |
| **Mobile App** | React Native |
| **Web Host UI** | React (learning) |
| **Database** | PostgreSQL (learning) |
| **Deployment** | TBD (Considerations = Render, Netlify, etc.) |

---

## 📦 Project Structure

- `phunparty-backend/` – Python FastAPI server
- `mobile-app/` – React Native frontend (players) [Coming Soon]
- `web-host-ui/` – Host screen for displaying questions and scores (React) [Coming Soon]
- `docs/` – Wireframes, system design (coming soon)

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.8+** installed on your system
- **PostgreSQL** database server running locally
- **Git** for cloning the repository

### Database Setup

#### Installing PostgreSQL

**Windows:**
1. Download PostgreSQL from [postgresql.org](https://www.postgresql.org/download/windows/)
2. Run the installer and follow the setup wizard
3. Remember the password you set for the `postgres` user
4. Default port is `5432` (keep this unless you have conflicts)

**macOS:**
```bash
# Using Homebrew
brew install postgresql
brew services start postgresql

# Or download from postgresql.org
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Or download from postgresql.org
```

#### Creating the Database

1. **Access PostgreSQL command line:**
   ```bash
   # Windows (from Command Prompt)
   psql -U postgres -h localhost
   
   # macOS/Linux
   sudo -u postgres psql
   ```

2. **Create the database and user:**
   ```sql
   -- Create the database
   CREATE DATABASE phunparty_db;
   
   -- Create a user (optional, you can use postgres user)
   CREATE USER phunparty_user WITH PASSWORD 'your_secure_password';
   
   -- Grant privileges
   GRANT ALL PRIVILEGES ON DATABASE phunparty_db TO phunparty_user;
   
   -- Exit psql
   \q
   ```

3. **Test the connection:**
   ```bash
   psql -U phunparty_user -d phunparty_db -h localhost
   ```

### Setup Instructions

1. **Clone the repository**
   ```bash
   git clone https://github.com/JayNightmare/PhunParty.git
   cd PhunParty
   ```

2. **Set up the backend**
   ```bash
   cd phunparty-backend
   ```

3. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure your database**
   
   Create a `credentials.env` file in the `phunparty-backend` directory:
   ```env
   # If you created a custom user
   DB_User=phunparty_user
   DB_Password=your_secure_password
   DB_Host=localhost
   DB_Port=5432
   DB_Name=phunparty_db
   
   # Or if using the default postgres user
   # DB_User=postgres
   # DB_Password=your_postgres_password
   # DB_Host=localhost
   # DB_Port=5432
   # DB_Name=phunparty_db
   ```

   or use the example credentials in `.env.credentials.example`

5. **Set up PostgreSQL database**
   
   This step was completed in the Database Setup section above. Your database should already be created and ready to use.

6. **Run the application**
   
   From the `phunparty-backend` directory:
   ```bash
   python -m uvicorn App.main:app --reload --host 0.0.0.0 --port 8000
   ```
   
   Alternatively, you can run it as a module (after adding a main block):
   ```bash
   python -m App.main
   ```

7. **Verify the setup**
   
   Open your browser and navigate to:
   - **API Documentation**: http://localhost:8000/docs
   - **Health Check**: http://localhost:8000/
   
   You should see the FastAPI interactive documentation (Swagger UI).

### Development Notes

- The server runs on **port 8000** by default
- The `--reload` flag enables auto-restart when code changes
- Database tables are automatically created when the app starts
- Check the console output for any database connection errors

### Troubleshooting

- **Module not found errors**: Ensure you're running commands from the `phunparty-backend` directory
- **Database connection issues**: Verify your PostgreSQL server is running and credentials are correct
- **Port conflicts**: If port 8000 is in use, change it with `--port 8001`

---

## 📍 Status

🚧 This project is in active development. The API with fastAPI is complete and the React web app and React-Native mobile app development are underway.

---

## 📌 Goals

- [x] Define MVP game loop
- [x] Build core REST API
- [ ] Build React Native player app
- [ ] Build React host UI
- [ ] Add audio quiz mode
- [ ] Deploy working demo

---

## 💬 Learning Outcomes
### By the end of the project, I hope to have increased my knowledge of and learned to work with the following:
- React
- FastAPI
- PyDantic
- React-Native
- WebSockets

## 🔮 Future Plans
### TBA
