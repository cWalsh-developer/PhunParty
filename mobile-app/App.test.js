import React from 'react';
import { render, screen } from '@testing-library/react-native';
import App from './App';

describe('App', () => {
  test('renders correctly', () => {
    render(<App />);
    
    // Check if main elements are present
    expect(screen.getByText('🎉 PhunParty')).toBeTruthy();
    expect(screen.getByText('Player App')).toBeTruthy();
  });

  test('shows coming soon status', () => {
    render(<App />);
    
    const statusElement = screen.getByTestId('app-status');
    expect(statusElement).toBeTruthy();
    expect(statusElement.props.children).toBe('Coming Soon...');
  });

  test('has correct title structure', () => {
    render(<App />);
    
    const title = screen.getByText('🎉 PhunParty');
    const subtitle = screen.getByText('Player App');
    
    expect(title).toBeTruthy();
    expect(subtitle).toBeTruthy();
  });

  test('renders without errors', () => {
    // If the render function doesn't throw, the test passes
    expect(() => render(<App />)).not.toThrow();
  });
});
