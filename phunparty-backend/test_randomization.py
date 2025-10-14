"""
Test the randomization logic with simulated data
"""

import json
import random


def test_randomization_logic():
    """Test what should happen with Q001 data"""

    # Simulate Q001 data from your production DB
    question_options = '["London", "Berlin", "Madrid"]'
    answer = "Paris"
    question_id = "Q001"

    print(f"Testing with Q001 data:")
    print(f"  question_options: {question_options}")
    print(f"  answer: {answer}")

    # Test the logic from get_question_with_randomized_options
    try:
        incorrect_options = json.loads(question_options)
        print(f"  parsed options: {incorrect_options}")

        # Combine incorrect options with correct answer
        all_options = incorrect_options + [answer]
        print(f"  all_options before shuffle: {all_options}")

        # Shuffle multiple times to see different results
        for i in range(5):
            shuffled_options = all_options.copy()
            random.shuffle(shuffled_options)
            correct_index = shuffled_options.index(answer)
            print(
                f"  test {i+1} - display_options: {shuffled_options}, correct_index: {correct_index}"
            )

    except Exception as e:
        print(f"Error: {e}")


def test_edge_cases():
    """Test edge cases that might cause fallback to answer-only"""

    print("\nTesting edge cases:")

    # Test case 1: null/None question_options
    print("1. None question_options:")
    question_options = None
    if not question_options:
        print("   -> Would return [answer] only")

    # Test case 2: empty string
    print("2. Empty string question_options:")
    question_options = ""
    if not question_options:
        print("   -> Would return [answer] only")

    # Test case 3: invalid JSON
    print("3. Invalid JSON:")
    question_options = '["London", "Berlin", "Madrid"'  # Missing closing bracket
    try:
        json.loads(question_options)
    except json.JSONDecodeError as e:
        print(f"   -> JSON error: {e}, would return [answer] only")

    # Test case 4: empty array
    print("4. Empty array:")
    question_options = "[]"
    try:
        incorrect_options = json.loads(question_options)
        all_options = incorrect_options + ["Paris"]
        print(f"   -> Would create display_options: {all_options}")
    except Exception as e:
        print(f"   -> Error: {e}")


if __name__ == "__main__":
    test_randomization_logic()
    test_edge_cases()
