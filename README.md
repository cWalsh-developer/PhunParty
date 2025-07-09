# 🎉 PhunParty (Working Title)

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

- `backend/` – Python FastAPI server
- `mobile-app/` – React Native frontend (players)
- `web-host-ui/` – Host screen for displaying questions and scores (React)
- `docs/` – Wireframes, system design (coming soon)

---

## 📍 Status

🚧 This project is in active development. Initial backend is being built with FastAPI.

---

## 📌 Goals

- [x] Define MVP game loop
- [ ] Build core REST API
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
