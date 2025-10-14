"""
Direct SQL test to check question_options column
"""

import sys
import os
import psycopg2
import json

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.config import DatabaseURL


def test_direct_sql():
    """Test with direct SQL connection"""
    try:
        # Connect directly to PostgreSQL
        conn = psycopg2.connect(DatabaseURL)
        cursor = conn.cursor()

        print("Connected to database successfully")

        # Check if question_options column exists
        cursor.execute(
            """
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'questions' 
            ORDER BY ordinal_position
        """
        )

        columns = cursor.fetchall()
        print("Columns in questions table:")
        for col_name, col_type in columns:
            print(f"  - {col_name}: {col_type}")

        # Try to get Q001 with question_options
        cursor.execute(
            """
            SELECT question_id, question, answer, question_options
            FROM questions 
            WHERE question_id = 'Q001'
        """
        )

        result = cursor.fetchone()
        if result:
            question_id, question, answer, options = result
            print(f"\nQ001 data:")
            print(f"  - ID: {question_id}")
            print(f"  - Question: {question}")
            print(f"  - Answer: {answer}")
            print(f"  - Options (raw): {repr(options)}")

            if options:
                try:
                    parsed_options = (
                        json.loads(options) if isinstance(options, str) else options
                    )
                    print(f"  - Options (parsed): {parsed_options}")
                except json.JSONDecodeError as e:
                    print(f"  - JSON decode error: {e}")
        else:
            print("Q001 not found")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    test_direct_sql()
