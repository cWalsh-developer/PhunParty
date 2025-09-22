"""
Test script to verify API key protection is working
"""

import requests
import json

# Test configuration
BASE_URL = "http://localhost:8000"
VALID_API_KEY = "your-secure-api-key-here-change-this"  # Should match credentials.env
INVALID_API_KEY = "invalid-key"


def test_api_key_protection():
    """Test that API endpoints require valid API key"""

    print("Testing API Key Protection...")
    print("=" * 60)

    # Test protected endpoints
    protected_endpoints = [
        ("/password-reset/request", {"phone_number": "07123456789"}),
        ("/game", {"rules": "test", "genre": "trivia"}),
        (
            "/players/create",
            {
                "player_name": "Test",
                "player_email": "test@test.com",
                "password": "test123",
            },
        ),
    ]

    # Test public endpoints (should work without API key)
    public_endpoints = [
        ("/auth/login", {"player_email": "test@test.com", "password": "test123"}),
        ("/", {}),  # Root endpoint
    ]

    # Test protected endpoints
    for endpoint, payload in protected_endpoints:
        url = f"{BASE_URL}{endpoint}"
        print(f"\n=== Testing Protected Endpoint: {endpoint} ===")

        # Test without API key (should fail)
        print("1. Without API key:")
        if payload:
            response = requests.post(url, json=payload)
        else:
            response = requests.get(url)
        print(f"   Status Code: {response.status_code}")
        print(f"   Response: {response.text[:100]}...")

        # Test with invalid API key (should fail)
        print("2. With invalid API key:")
        headers = {"X-API-Key": INVALID_API_KEY}
        if payload:
            response = requests.post(url, json=payload, headers=headers)
        else:
            response = requests.get(url, headers=headers)
        print(f"   Status Code: {response.status_code}")
        print(f"   Response: {response.text[:100]}...")

        # Test with valid API key (should succeed or fail for other reasons)
        print("3. With valid API key:")
        headers = {"X-API-Key": VALID_API_KEY}
        if payload:
            response = requests.post(url, json=payload, headers=headers)
        else:
            response = requests.get(url, headers=headers)
        print(f"   Status Code: {response.status_code}")
        print(f"   Response: {response.text[:100]}...")

    # Test public endpoints
    print(f"\n=== Testing Public Endpoints (No API Key Required) ===")
    for endpoint, payload in public_endpoints:
        url = f"{BASE_URL}{endpoint}"
        print(f"\nTesting: {endpoint}")
        if payload:
            response = requests.post(url, json=payload)
        else:
            response = requests.get(url)
        print(f"   Status Code: {response.status_code}")
        print(f"   Response: {response.text[:100]}...")

    print("\n" + "=" * 60)
    print("API Key Protection Test Complete!")
    print("\nExpected results:")
    print("- Protected endpoints: 403 without API key, 403 with invalid key")
    print("- Public endpoints: Should not return 403")


def test_specific_endpoint():
    """Quick test for a specific endpoint"""
    url = f"{BASE_URL}/password-reset/request"
    payload = {"phone_number": "07123456789"}

    print("\nQuick test of password reset endpoint:")

    # Test without API key
    print("1. Without API key:")
    response = requests.post(url, json=payload)
    print(f"   Status Code: {response.status_code}")

    # Test with valid API key
    print("2. With valid API key:")
    headers = {"X-API-Key": VALID_API_KEY}
    response = requests.post(url, json=payload, headers=headers)
    print(f"   Status Code: {response.status_code}")


if __name__ == "__main__":
    test_api_key_protection()
    # Uncomment to run quick test:
    # test_specific_endpoint()
