"""
Script to check and fix question_options for existing questions
"""

import sys
import os
import json

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.config import DatabaseURL
from app.database.dbCRUD import get_question_by_id

# Create database connection
engine = create_engine(DatabaseURL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def check_and_fix_question_options():
    """Check and fix question_options for questions"""
    db = SessionLocal()

    try:
        # Check the specific question from the logs
        question_id = "Q001"
        question = get_question_by_id(question_id, db)

        if not question:
            print(f"Question {question_id} not found")
            return

        print(f"Question: {question.question}")
        print(f"Answer: {question.answer}")
        print(f"Current question_options: {question.question_options}")

        # Check if question_options is None or invalid JSON
        if question.question_options is None:
            print("question_options is None - needs to be populated")
        else:
            try:
                options = json.loads(question.question_options)
                print(f"Parsed options: {options}")
                if not isinstance(options, list) or len(options) == 0:
                    print("question_options is empty or not a list")
                else:
                    print(f"Found {len(options)} incorrect options")
            except json.JSONDecodeError:
                print("question_options contains invalid JSON")

        # Let's add some sample incorrect options for this question
        if question.question == "What is the capital of France?":
            incorrect_options = ["London", "Berlin", "Madrid"]
            question.question_options = json.dumps(incorrect_options)
            db.commit()
            print(f"Updated question_options to: {question.question_options}")

            # Test the randomization
            all_options = incorrect_options + [question.answer]
            print(f"All options (before shuffle): {all_options}")

            import random

            random.shuffle(all_options)
            correct_index = all_options.index(question.answer)
            print(f"All options (after shuffle): {all_options}")
            print(f"Correct answer index: {correct_index}")

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    check_and_fix_question_options()
