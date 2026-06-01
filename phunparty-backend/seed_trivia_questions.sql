BEGIN;

-- Trivia seed data for the questions table.
-- question_options stores the incorrect multiple-choice options as JSON.
-- The application appends the correct answer at runtime and randomizes the final list.

INSERT INTO questions (
    question_id,
    question,
    answer,
    genre,
    difficulty,
    question_options
) VALUES
    ('Q001', 'What is the capital of France?', 'Paris', 'geography', 'easy', '["London", "Rome", "Berlin"]'),
    ('Q002', 'What is the largest planet in our solar system?', 'Jupiter', 'science', 'easy', '["Saturn", "Earth", "Mars"]'),
    ('Q003', 'What color do you get when you mix blue and yellow paint?', 'Green', 'art', 'easy', '["Orange", "Purple", "Red"]'),
    ('Q004', 'How many days are in a week?', '7', 'general knowledge', 'easy', '["5", "6", "8"]'),
    ('Q005', 'What is the freezing point of water in Celsius?', '0', 'science', 'easy', '["10", "32", "-5"]'),
    ('Q006', 'What is the main gas in the atmosphere of Earth?', 'Nitrogen', 'science', 'easy', '["Oxygen", "Carbon dioxide", "Helium"]'),
    ('Q007', 'Which planet is known as the Red Planet?', 'Mars', 'science', 'easy', '["Venus", "Mercury", "Neptune"]'),
    ('Q008', 'Which animal is known for barking?', 'Dog', 'animals', 'easy', '["Cat", "Horse", "Cow"]'),
    ('Q009', 'What day comes after Monday?', 'Tuesday', 'general knowledge', 'easy', '["Sunday", "Wednesday", "Friday"]'),
    ('Q010', 'What is the largest ocean on Earth?', 'Pacific Ocean', 'geography', 'easy', '["Atlantic Ocean", "Indian Ocean", "Arctic Ocean"]'),
    ('Q011', 'What currency is used in the United States?', 'Dollar', 'economics', 'easy', '["Euro", "Yen", "Peso"]'),
    ('Q012', 'Which sea mammal is known for intelligence and jumps through waves?', 'Dolphin', 'animals', 'easy', '["Seal", "Whale", "Shark"]'),
    ('Q013', 'How many sides does a triangle have?', '3', 'math', 'easy', '["2", "4", "5"]'),
    ('Q014', 'What is the first month of the year?', 'January', 'general knowledge', 'easy', '["March", "June", "December"]'),
    ('Q015', 'Which instrument has black and white keys?', 'Piano', 'music', 'easy', '["Guitar", "Drum", "Flute"]'),
    ('Q016', 'What is a hot drink made from tea leaves called?', 'Tea', 'food and drink', 'easy', '["Coffee", "Juice", "Milk"]'),
    ('Q017', 'How many continents are there?', '7', 'geography', 'easy', '["5", "6", "8"]'),
    ('Q018', 'What is the opposite of cold?', 'Hot', 'general knowledge', 'easy', '["Wet", "Dry", "Soft"]'),
    ('Q019', 'How many rings are on the Olympic symbol?', '5', 'sports', 'easy', '["3", "4", "6"]'),
    ('Q020', 'What is the star at the center of our solar system?', 'Sun', 'science', 'easy', '["Moon", "Earth", "Mars"]'),
    ('Q021', 'What shape has four equal sides?', 'Square', 'math', 'easy', '["Triangle", "Circle", "Rectangle"]'),
    ('Q022', 'What do bees make?', 'Honey', 'animals', 'easy', '["Milk", "Wax", "Bread"]'),
    ('Q023', 'What is the fastest land animal?', 'Cheetah', 'animals', 'easy', '["Lion", "Horse", "Elephant"]'),
    ('Q024', 'What color is the sky on a clear day?', 'Blue', 'general knowledge', 'easy', '["Green", "Orange", "Brown"]'),
    ('Q025', 'Which month usually has 28 days in a common year?', 'February', 'general knowledge', 'easy', '["April", "June", "September"]'),
    ('Q026', 'What is the direction opposite north?', 'South', 'geography', 'easy', '["East", "West", "Up"]'),
    ('Q027', 'What season is December in the Northern Hemisphere?', 'Winter', 'geography', 'easy', '["Summer", "Spring", "Autumn"]'),
    ('Q028', 'What is the hardest natural gemstone?', 'Diamond', 'science', 'easy', '["Ruby", "Emerald", "Opal"]'),
    ('Q029', 'What is the longest bone in the human body?', 'Femur', 'science', 'easy', '["Tibia", "Humerus", "Ulna"]'),
    ('Q030', 'What device is commonly used to call other people?', 'Telephone', 'technology', 'easy', '["Camera", "Speaker", "Microscope"]'),
    ('Q031', 'Which animal is often called the king of the jungle?', 'Lion', 'animals', 'easy', '["Tiger", "Bear", "Wolf"]'),
    ('Q032', 'Which plant part absorbs water from the soil?', 'Roots', 'science', 'easy', '["Leaves", "Flowers", "Fruit"]'),
    ('Q033', 'What gas do humans breathe in to stay alive?', 'Oxygen', 'science', 'easy', '["Carbon dioxide", "Hydrogen", "Nitrogen"]'),
    ('Q034', 'What is 2 plus 2?', '4', 'math', 'easy', '["3", "5", "6"]')
ON CONFLICT (question_id) DO NOTHING;

INSERT INTO questions (
    question_id,
    question,
    answer,
    genre,
    difficulty,
    question_options
) VALUES
    ('Q035', 'Which author wrote 1984?', 'George Orwell', 'literature', 'medium', '["Aldous Huxley", "Mark Twain", "Jules Verne"]'),
    ('Q036', 'What element has the chemical symbol Fe?', 'Iron', 'science', 'medium', '["Fluorine", "Lead", "Silver"]'),
    ('Q037', 'What is the capital of Australia?', 'Canberra', 'geography', 'medium', '["Sydney", "Melbourne", "Perth"]'),
    ('Q038', 'Who is credited with inventing the telephone?', 'Alexander Graham Bell', 'history', 'medium', '["Nikola Tesla", "Thomas Edison", "Guglielmo Marconi"]'),
    ('Q039', 'What is the tallest mountain in Africa?', 'Mount Kilimanjaro', 'geography', 'medium', '["Mount Kenya", "Mount Elgon", "Mount Meru"]'),
    ('Q040', 'What is the chemical formula for table salt?', 'Sodium chloride', 'science', 'medium', '["Potassium chloride", "Calcium carbonate", "Sodium bicarbonate"]'),
    ('Q041', 'Which river flows through Egypt?', 'Nile', 'geography', 'medium', '["Amazon", "Danube", "Yangtze"]'),
    ('Q042', 'What is the primary language spoken in Brazil?', 'Portuguese', 'geography', 'medium', '["Spanish", "French", "Italian"]'),
    ('Q043', 'In what year did the first Moon landing happen?', '1969', 'history', 'medium', '["1965", "1971", "1980"]'),
    ('Q044', 'Which organ pumps blood through the human body?', 'Heart', 'science', 'medium', '["Liver", "Lung", "Kidney"]'),
    ('Q045', 'Who was the first president of the United States?', 'George Washington', 'history', 'medium', '["Thomas Jefferson", "Abraham Lincoln", "John Adams"]'),
    ('Q046', 'Which famous painting was created by Leonardo da Vinci?', 'Mona Lisa', 'art', 'medium', '["The Scream", "Starry Night", "The Last Supper"]'),
    ('Q047', 'Which metal is liquid at room temperature?', 'Mercury', 'science', 'medium', '["Copper", "Iron", "Aluminum"]'),
    ('Q048', 'Who painted The Starry Night?', 'Vincent van Gogh', 'art', 'medium', '["Pablo Picasso", "Claude Monet", "Salvador Dali"]'),
    ('Q049', 'What is the capital of Canada?', 'Ottawa', 'geography', 'medium', '["Toronto", "Montreal", "Vancouver"]'),
    ('Q050', 'What is the largest hot desert on Earth?', 'Sahara Desert', 'geography', 'medium', '["Gobi Desert", "Kalahari Desert", "Atacama Desert"]'),
    ('Q051', 'What does DNA stand for?', 'Deoxyribonucleic acid', 'science', 'medium', '["Dynamic nitrogen atom", "Dual nucleic acid", "Digital network array"]'),
    ('Q052', 'Which country gifted the Statue of Liberty to the United States?', 'France', 'history', 'medium', '["Italy", "Spain", "Germany"]'),
    ('Q053', 'Which gas do plants absorb during photosynthesis?', 'Carbon dioxide', 'science', 'medium', '["Oxygen", "Nitrogen", "Helium"]'),
    ('Q054', 'Which organelle is often called the powerhouse of the cell?', 'Mitochondria', 'science', 'medium', '["Ribosome", "Nucleus", "Chloroplast"]'),
    ('Q055', 'Which war ended in 1945?', 'World War II', 'history', 'medium', '["World War I", "The Cold War", "The Vietnam War"]'),
    ('Q056', 'Which sport uses a shuttlecock?', 'Badminton', 'sports', 'medium', '["Tennis", "Squash", "Polo"]'),
    ('Q057', 'What do you call an animal that eats both plants and meat?', 'Omnivore', 'science', 'medium', '["Herbivore", "Carnivore", "Insectivore"]'),
    ('Q058', 'Which U.S. state is known as the Sunshine State?', 'Florida', 'geography', 'medium', '["California", "Texas", "Arizona"]'),
    ('Q059', 'Which ocean lies between Africa and Australia?', 'Indian Ocean', 'geography', 'medium', '["Atlantic Ocean", "Pacific Ocean", "Arctic Ocean"]'),
    ('Q060', 'Who wrote Hamlet?', 'William Shakespeare', 'literature', 'medium', '["Charles Dickens", "Jane Austen", "Homer"]'),
    ('Q061', 'What is the largest internal organ in the human body?', 'Liver', 'science', 'medium', '["Heart", "Brain", "Spleen"]'),
    ('Q062', 'What is the capital of New Zealand?', 'Wellington', 'geography', 'medium', '["Auckland", "Christchurch", "Hamilton"]'),
    ('Q063', 'What was the first artificial satellite launched into orbit?', 'Sputnik 1', 'history', 'medium', '["Apollo 11", "Vostok 1", "Explorer 1"]'),
    ('Q064', 'What is the chemical symbol for potassium?', 'K', 'science', 'medium', '["P", "Pt", "Ka"]'),
    ('Q065', 'Which planet is famous for its rings?', 'Saturn', 'science', 'medium', '["Jupiter", "Uranus", "Neptune"]'),
    ('Q066', 'What currency is used in Japan?', 'Yen', 'economics', 'medium', '["Won", "Rupee", "Dollar"]'),
    ('Q067', 'Which city is known as the Big Apple?', 'New York City', 'geography', 'medium', '["Chicago", "Boston", "Los Angeles"]')
ON CONFLICT (question_id) DO NOTHING;

INSERT INTO questions (
    question_id,
    question,
    answer,
    genre,
    difficulty,
    question_options
) VALUES
    ('Q068', 'What is the capital of Bhutan?', 'Thimphu', 'geography', 'hard', '["Kathmandu", "Dhaka", "Lhasa"]'),
    ('Q069', 'What is the smallest country in the world by area?', 'Vatican City', 'geography', 'hard', '["Monaco", "Nauru", "San Marino"]'),
    ('Q070', 'What element has atomic number 79?', 'Gold', 'science', 'hard', '["Silver", "Platinum", "Mercury"]'),
    ('Q071', 'Which scientist formulated the uncertainty principle?', 'Werner Heisenberg', 'science', 'hard', '["Niels Bohr", "Max Planck", "Erwin Schroedinger"]'),
    ('Q072', 'Which battle is considered a turning point in the Pacific during World War II?', 'Battle of Midway', 'history', 'hard', '["Battle of Stalingrad", "Battle of the Bulge", "Battle of Britain"]'),
    ('Q073', 'In what year did the Western Roman Empire fall?', '476', 'history', 'hard', '["395", "1066", "1453"]'),
    ('Q074', 'Who was the first human to orbit Earth?', 'Yuri Gagarin', 'history', 'hard', '["Neil Armstrong", "Alan Shepard", "John Glenn"]'),
    ('Q075', 'Who wrote The Brothers Karamazov?', 'Fyodor Dostoevsky', 'literature', 'hard', '["Leo Tolstoy", "Anton Chekhov", "Ivan Turgenev"]'),
    ('Q076', 'What is the deepest ocean trench on Earth?', 'Mariana Trench', 'geography', 'hard', '["Tonga Trench", "Java Trench", "Puerto Rico Trench"]'),
    ('Q077', 'What is the capital of Kazakhstan?', 'Astana', 'geography', 'hard', '["Almaty", "Tashkent", "Bishkek"]'),
    ('Q078', 'Which organelle contains chlorophyll?', 'Chloroplast', 'science', 'hard', '["Mitochondria", "Ribosome", "Nucleus"]'),
    ('Q079', 'Who composed The Four Seasons?', 'Antonio Vivaldi', 'music', 'hard', '["Johann Sebastian Bach", "Wolfgang Amadeus Mozart", "Ludwig van Beethoven"]'),
    ('Q080', 'What was the codename for the D-Day invasion?', 'Operation Overlord', 'history', 'hard', '["Operation Barbarossa", "Operation Market Garden", "Operation Torch"]'),
    ('Q081', 'Which country has the most natural lakes?', 'Canada', 'geography', 'hard', '["Russia", "Finland", "Sweden"]'),
    ('Q082', 'Who wrote One Hundred Years of Solitude?', 'Gabriel Garcia Marquez', 'literature', 'hard', '["Jorge Luis Borges", "Mario Vargas Llosa", "Pablo Neruda"]'),
    ('Q083', 'Which human organ produces insulin?', 'Pancreas', 'science', 'hard', '["Liver", "Kidney", "Spleen"]'),
    ('Q084', 'What is the capital of Tajikistan?', 'Dushanbe', 'geography', 'hard', '["Tashkent", "Baku", "Ashgabat"]'),
    ('Q085', 'In what year did the Chernobyl disaster occur?', '1986', 'history', 'hard', '["1979", "1991", "2001"]'),
    ('Q086', 'What is the deepest lake in the world?', 'Lake Baikal', 'geography', 'hard', '["Lake Superior", "Lake Tanganyika", "Lake Victoria"]'),
    ('Q087', 'Ancient Mesopotamia was primarily located in modern-day which country?', 'Iraq', 'history', 'hard', '["Iran", "Syria", "Turkey"]'),
    ('Q088', 'Which spacecraft carried the first humans to the Moon?', 'Apollo 11', 'history', 'hard', '["Apollo 8", "Gemini 4", "Soyuz 1"]'),
    ('Q089', 'What is the largest moon of Saturn?', 'Titan', 'science', 'hard', '["Europa", "Ganymede", "Io"]'),
    ('Q090', 'Which country has the most time zones?', 'France', 'geography', 'hard', '["United States", "Russia", "Australia"]'),
    ('Q091', 'What was Istanbul called during the Byzantine Empire?', 'Constantinople', 'history', 'hard', '["Byzantium", "Ankara", "Alexandria"]'),
    ('Q092', 'In what year did the Berlin Wall fall?', '1989', 'history', 'hard', '["1979", "1991", "1961"]'),
    ('Q093', 'What element has the chemical symbol Sb?', 'Antimony', 'science', 'hard', '["Tin", "Arsenic", "Bismuth"]'),
    ('Q094', 'What is the highest peak in South America?', 'Aconcagua', 'geography', 'hard', '["Huascaran", "Chimborazo", "Cotopaxi"]'),
    ('Q095', 'Who was the first prime minister of independent India?', 'Jawaharlal Nehru', 'history', 'hard', '["Mahatma Gandhi", "Sardar Patel", "Indira Gandhi"]'),
    ('Q096', 'What is the largest volcano in the solar system?', 'Olympus Mons', 'science', 'hard', '["Mauna Loa", "Mount Etna", "Mount Vesuvius"]'),
    ('Q097', 'Which mathematician proved Fermat''s Last Theorem?', 'Andrew Wiles', 'math', 'hard', '["Pierre de Fermat", "Leonhard Euler", "Carl Gauss"]'),
    ('Q098', 'What is the largest island in the Mediterranean Sea?', 'Sicily', 'geography', 'hard', '["Crete", "Sardinia", "Cyprus"]'),
    ('Q099', 'Which scientist developed the quantum theory of black-body radiation?', 'Max Planck', 'science', 'hard', '["Albert Einstein", "Niels Bohr", "Erwin Schroedinger"]'),
    ('Q100', 'What is the capital of the Canadian province of Quebec?', 'Quebec City', 'geography', 'hard', '["Montreal", "Ottawa", "Toronto"]')
ON CONFLICT (question_id) DO NOTHING;

COMMIT;

UPDATE questions
SET genre = 'Trivia'
WHERE question_id BETWEEN 'Q001' AND 'Q100';