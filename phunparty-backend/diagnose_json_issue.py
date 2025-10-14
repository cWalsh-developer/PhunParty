"""
Production diagnostic script to check question_options JSON issues
Run this on your production server to diagnose the JSON parsing problem
"""

import sys
import os
import json
import binascii

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.orm import sessionmaker
from app.config import engine
from app.database.dbCRUD import get_question_by_id

# Use the existing engine from config
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def diagnose_question_options():
    """Diagnose Q001 and Q002 JSON parsing issues"""
    db = SessionLocal()

    try:
        for question_id in ["Q001", "Q002"]:
            print(f"\n=== Diagnosing {question_id} ===")

            question = get_question_by_id(question_id, db)
            if not question:
                print(f"{question_id} not found")
                continue

            options = question.question_options
            print(f"Raw value type: {type(options)}")
            print(f"Raw value: {repr(options)}")
            print(f"Raw value length: {len(options) if options else 0}")

            if options:
                # Check for hidden characters
                print(
                    f"Hex representation: {binascii.hexlify(options.encode('utf-8') if isinstance(options, str) else str(options).encode('utf-8'))}"
                )

                # Try different parsing approaches
                try:
                    parsed = json.loads(options)
                    print(f"✅ JSON parsing SUCCESS: {parsed}")
                except json.JSONDecodeError as e:
                    print(f"❌ JSON parsing FAILED: {e}")
                    print(
                        f"Error position: {e.pos if hasattr(e, 'pos') else 'unknown'}"
                    )

                    # Try to identify the problematic character
                    if hasattr(e, "pos") and e.pos < len(options):
                        problem_char = options[e.pos] if e.pos < len(options) else "EOF"
                        print(f"Character at error position: {repr(problem_char)}")

                        # Show context around the error
                        start = max(0, e.pos - 10)
                        end = min(len(options), e.pos + 10)
                        context = options[start:end]
                        print(f"Context around error: {repr(context)}")

                # Try cleaning the string
                cleaned = options.strip()
                if cleaned != options:
                    print(f"After strip(): {repr(cleaned)}")
                    try:
                        parsed_cleaned = json.loads(cleaned)
                        print(f"✅ Cleaned JSON parsing SUCCESS: {parsed_cleaned}")
                    except json.JSONDecodeError as e2:
                        print(f"❌ Cleaned JSON parsing still FAILED: {e2}")

                # Check for BOM or other encoding issues
                if options.startswith("\ufeff"):
                    print("⚠️ BOM detected at start of string")
                    no_bom = options[1:]
                    try:
                        parsed_no_bom = json.loads(no_bom)
                        print(f"✅ No-BOM JSON parsing SUCCESS: {parsed_no_bom}")
                    except json.JSONDecodeError:
                        print(f"❌ No-BOM JSON parsing still FAILED")
            else:
                print("question_options is None/empty")

    except Exception as e:
        print(f"Script error: {e}")
        import traceback

        traceback.print_exc()
    finally:
        db.close()


def suggest_fixes():
    """Suggest potential fixes based on common issues"""
    print("\n=== Potential Fixes ===")
    print(
        "1. If BOM detected: UPDATE questions SET question_options = LTRIM(question_options, '\ufeff') WHERE question_id IN ('Q001', 'Q002');"
    )
    print(
        "2. If whitespace issues: UPDATE questions SET question_options = TRIM(question_options) WHERE question_id IN ('Q001', 'Q002');"
    )
    print("3. If encoding issues: Check database encoding and client encoding")
    print(
        "4. If the JSON is actually valid, the issue might be in the Python JSON library or data retrieval"
    )


if __name__ == "__main__":
    diagnose_question_options()
    suggest_fixes()
