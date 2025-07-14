import { render, screen } from "@testing-library/react";
import App from "./App";

test("renders PhunParty title", () => {
    render(<App />);
    const titleElement = screen.getByText(/PhunParty Host UI/i);
    expect(titleElement).toBeInTheDocument();
});

test("shows development status", () => {
    render(<App />);
    const statusElement = screen.getByTestId("game-status");
    expect(statusElement).toBeInTheDocument();
    expect(statusElement).toHaveTextContent("Waiting for development");
});

test("renders without crashing", () => {
    render(<App />);
    // If we get here, the component rendered successfully
    expect(true).toBe(true);
});

test("has correct structure", () => {
    render(<App />);
    // Check for the main title element
    const titleElement = screen.getByText(/PhunParty Host UI/i);
    expect(titleElement).toBeInTheDocument();
    
    // Check for the status element
    const statusElement = screen.getByTestId("game-status");
    expect(statusElement).toBeInTheDocument();
});
