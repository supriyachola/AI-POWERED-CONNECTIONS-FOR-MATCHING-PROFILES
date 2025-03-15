import sqlite3
from gensim.models import Word2Vec
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

# Train Word2Vec model based on user interests
def train_model():
    conn = sqlite3.connect('database/users.db')
    cursor = conn.cursor()
    cursor.execute("SELECT interests FROM users WHERE interests IS NOT NULL")
    data = cursor.fetchall()
    conn.close()

    if not data:
        raise ValueError("No interests found in the database to train the model.")

    # Prepare training data
    sentences = [row[0].split(",") for row in data]
    model = Word2Vec(sentences, vector_size=100, window=5, min_count=1, workers=4)
    model.save("models/trained_model.pkl")
    print("Model trained and saved successfully.")

# Match users based on interests
def match_users(username):
    conn = sqlite3.connect('database/users.db')
    cursor = conn.cursor()
    
    # Get the interests of the logged-in user
    cursor.execute("SELECT interests FROM users WHERE username = ?", (username,))
    result = cursor.fetchone()
    
    if result is None:
        conn.close()
        raise ValueError(f"User '{username}' not found in the database.")
    
    if result[0] is None or result[0].strip() == "":
        conn.close()
        raise ValueError(f"User '{username}' has no interests defined.")
    
    user_interests = result[0].split(",")
    
    # Load the Word2Vec model
    model = Word2Vec.load("models/trained_model.pkl")
    
    # Compute the similarity scores
    cursor.execute("SELECT username, interests FROM users WHERE username != ?", (username,))
    all_users = cursor.fetchall()
    
    if not all_users:
        conn.close()
        raise ValueError("No other users found in the database.")

    user_vector = np.mean([model.wv[word] for word in user_interests if word in model.wv], axis=0).reshape(1, -1)
    matches = []
    for other_username, other_interests in all_users:
        if other_interests:  # Check if other_interests is not empty or NULL
            other_vector = np.mean([model.wv[word] for word in other_interests.split(",") if word in model.wv], axis=0).reshape(1, -1)
            similarity = cosine_similarity(user_vector, other_vector)[0][0]
            matches.append((other_username, similarity))
    
    conn.close()

    # Sort by similarity score
    matches = sorted(matches, key=lambda x: x[1], reverse=True)
    return matches[:5]  # Return top 5 matches

# Example usage
if __name__ == "__main__":
    try:
        print("Training model...")
        train_model()  # Train the model
        username = input("Enter your username: ").strip()
        print(f"Finding matches for user: {username}...")
        matches = match_users(username)
        if matches:
            print("Top matches:")
            for match in matches:
                print(f"Username: {match[0]}, Similarity: {match[1]*100:.2f}%")
        else:
            print("No matches found.")
    except Exception as e:
        print(f"Error: {e}")
