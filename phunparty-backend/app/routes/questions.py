from app.database.dbCRUD import get_question_by_id, update_scores, submit_questions
from app.dependencies import get_db
from app.models.questions_model import Questions
from typing import List
from fastapi import APIRouter, HTTPException, Depends
from app.models.response_models import (
    AnswerVerificationResponseModel,
    QuestionsAddedResponseModel,
    QuestionRequest,
)
from app.models.questions import AnswerVerification
from app.models.session_question_assignment import SessionQuestionAssignment
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/{question_id}", tags=["Questions"])
def get_question_by_id_route(question_id: str, db: Session = Depends(get_db)):
    """
    Retrieve a question by its ID.
    """
    question = get_question_by_id(question_id, db)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    return question


@router.post(
    "/verify_answer", tags=["Questions"], response_model=AnswerVerificationResponseModel
)
def verify_answer_route(
    answer_verification: AnswerVerification,
    db: Session = Depends(get_db),
):
    """
    Verify the player's answer to a question.
    """
    question = get_question_by_id(answer_verification.question_id, db)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    is_correct = (
        str(question.answer).lower() == answer_verification.player_answer.lower()
    )
    if not is_correct:
        raise HTTPException(status_code=400, detail="Incorrect answer")
    else:
        update_scores(
            db, answer_verification.session_code, answer_verification.player_id
        )
    return AnswerVerificationResponseModel(
        player_answer=answer_verification.player_answer,
        is_correct=is_correct,
    )


@router.post("/add", tags=["Questions"], response_model=QuestionsAddedResponseModel)
def add_question_route(
    question_request: QuestionRequest, db: Session = Depends(get_db)
):
    """
    Add a new question.
    """
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
        difficulty=submitted_question.difficulty,
    )
