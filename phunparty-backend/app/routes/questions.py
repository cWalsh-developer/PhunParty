import json
import random

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.dbCRUD import get_question_by_id, submit_questions
from app.dependencies import get_current_player, get_db, require_admin_api_key
from app.models.enums import DifficultyLevel
from app.schemas.questions_model import Questions
from app.models.response_models import QuestionRequest, QuestionsAddedResponseModel
from app.schemas.players_model import Players

router = APIRouter()


@router.get("/{question_id}", tags=["Questions"])
def get_question_by_id_route(
    question_id: str,
    db: Session = Depends(get_db),
    current_player: Players = Depends(get_current_player),
):
    """
    Retrieve a question by its ID with randomized answer options.
    """
    try:
        question = get_question_by_id(question_id, db)
        if not question:
            raise HTTPException(status_code=404, detail="Question not found")

        raw_options = getattr(question, "question_options", None)
        # Always randomize the options
        incorrect_options = []
        if raw_options:
            if isinstance(raw_options, str):
                incorrect_options = json.loads(raw_options)
            elif isinstance(raw_options, list):
                incorrect_options = raw_options
        all_options = []
        correct_index = None
        if incorrect_options:
            all_options = incorrect_options + [question.answer]
            random.shuffle(all_options)
            correct_index = all_options.index(question.answer)

        return {
            "question_id": question.question_id,
            "question": question.question,
            "genre": question.genre,
            "difficulty": question.difficulty,
            "question_options": raw_options if raw_options else [],
            "display_options": all_options,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Unable to retrieve question")


@router.post("/add", tags=["Questions"], response_model=QuestionsAddedResponseModel)
def add_question_route(
    question_request: QuestionRequest,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_api_key),
):
    """
    Add a new question.
    """
    try:
        # Create SQLAlchemy model from Pydantic request
        question_data = {
            "question": question_request.question,
            "answer": question_request.answer,
            "genre": question_request.genre,
            "difficulty": question_request.difficulty,
        }
        if hasattr(Questions, "question_options"):
            question_data["question_options"] = (
                json.dumps(question_request.question_options)
                if hasattr(question_request, "question_options")
                and question_request.question_options
                else "[]"
            )

        question = Questions(**question_data)

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
