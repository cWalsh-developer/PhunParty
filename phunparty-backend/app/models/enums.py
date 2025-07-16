import enum


class DifficultyLevel(str, enum.Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class ResultType(str, enum.Enum):
    default = None
    win = "win"
    lose = "lose"
    draw = "draw"
