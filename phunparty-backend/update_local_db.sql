-- Run this on your local database to add the missing column
ALTER TABLE questions ADD COLUMN question_options JSON;

-- Then insert your question data or update existing records
-- You can use the trivia_questions.sql file you provided