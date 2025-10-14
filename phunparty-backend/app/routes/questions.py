from typing import List
import json
import random

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.dbCRUD import get_question_by_id, submit_questions
from app.dependencies import get_api_key, get_db
from app.models.enums import DifficultyLevel
from app.models.questions_model import Questions
from app.models.response_models import QuestionRequest, QuestionsAddedResponseModel
from app.models.session_question_assignment import SessionQuestionAssignment

router = APIRouter(dependencies=[Depends(get_api_key)])


@router.get("/{question_id}", tags=["Questions"])
def get_question_by_id_route(question_id: str, db: Session = Depends(get_db)):
    """
    Retrieve a question by its ID with randomized answer options.
    """
    try:
        question = get_question_by_id(question_id, db)
        if not question:
            raise HTTPException(status_code=404, detail="Question not found")

        # Always randomize the options
        incorrect_options = json.loads(question.question_options)
        all_options = incorrect_options + [question.answer]
        random.shuffle(all_options)
        correct_index = all_options.index(question.answer)

        return {
            "question_id": question.question_id,
            "question": question.question,
            "answer": question.answer,
            "genre": question.genre,
            "difficulty": question.difficulty,
            "question_options": question.question_options,
            "display_options": all_options,
            "correct_index": correct_index,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Unable to retrieve question")


@router.post("/add", tags=["Questions"], response_model=QuestionsAddedResponseModel)
def add_question_route(
    question_request: QuestionRequest, db: Session = Depends(get_db)
):
    """
    Add a new question.
    """
    try:
        # Create SQLAlchemy model from Pydantic request
        question = Questions(
            question=question_request.question,
            answer=question_request.answer,
            genre=question_request.genre,
            difficulty=question_request.difficulty,
        )

        submitted_question = submit_questions(db, question)
        return QuestionsAddedResponseModel(
            message="Question added successfully",
            question=submitted_question.question,
            answer=submitted_question.answer,
            genre=submitted_question.genre,
            difficulty=(
                submitted_question.difficulty
                if submitted_question.difficulty
                else DifficultyLevel.easy
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to add question")
