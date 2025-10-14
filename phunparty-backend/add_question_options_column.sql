-- Add question_options column to the questions table
-- This column will store JSON array of multiple choice options

ALTER TABLE questions 
ADD COLUMN question_options TEXT;

-- Add a comment to document the column
COMMENT ON COLUMN questions.question_options IS 'JSON array of multiple choice options for the question';

-- Optional: Update existing questions that don't have options
-- (You can run the INSERT statements from trivia_questions.sql after adding the column)