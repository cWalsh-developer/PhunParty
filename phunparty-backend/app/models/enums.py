import enum


class DifficultyLevel(str, enum.Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class ResultType(str, enum.Enum):
    win = "Won"
    lose = "Lost"
    draw = "Draw"
