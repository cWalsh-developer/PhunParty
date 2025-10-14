"""
Script to check and fix Q001 question data
"""

import sys
import os
import json
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.orm import sessionmaker
from app.config import engine
from app.models.questions_model import Questions

# Use the existing engine from config
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def check_question_data():
    """Check Q001 data specifically"""
    db = SessionLocal()
    
    try:
        # Get Q001 specifically
        question = db.query(Questions).filter(Questions.question_id == "Q001").first()
        
        if not question:
            print("Q001 not found!")
            return
            
        print(f"Question ID: {question.question_id}")
        print(f"Question text: '{question.question}'")
        print(f"Answer: '{question.answer}'")
        print(f"Question options (raw): {repr(question.question_options)}")
        
        # Try to parse question_options
        if question.question_options:
            try:
                options = json.loads(question.question_options)
                print(f"Parsed options: {options}")
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}")
        else:
            print("Question options is None/empty")
            
        # Check if this matches what we expect
        expected_question = "What is the capital of France?"
        expected_answer = "Paris"
        expected_options = ["London", "Berlin", "Madrid"]
        
        print(f"\nExpected question: '{expected_question}'")
        print(f"Actual matches expected: {question.question == expected_question}")
        
        if question.question != expected_question or not question.question_options:
            print("\nFixing Q001 data...")
            question.question = expected_question
            question.answer = expected_answer
            question.question_options = json.dumps(expected_options)
            
            db.commit()
            print("Q001 fixed!")
        else:
            print("Q001 data looks correct")
            
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    check_question_data()