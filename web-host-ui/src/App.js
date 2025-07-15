import React from "react";
import "./App.css";

function App() {
    return (
        <div className="App">
            <header className="App-header">
                <h1>🎉 PhunParty Host UI</h1>
                <p>Game host interface coming soon...</p>
                <div className="game-status">
                    <p>
                        Status:{" "}
                        <span data-testid="game-status">
                            Waiting for development
                        </span>
                    </p>
                </div>
                <div className="info">
                    <p>📱 Players will join using their phones</p>
                    <p>🧠 Custom trivia and music games</p>
                    <p>📺 Perfect for living room gaming</p>
                </div>
            </header>
        </div>
    );
}

export default App;
