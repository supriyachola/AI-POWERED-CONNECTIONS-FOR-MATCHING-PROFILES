from gensim.models import Word2Vec

# Load the Word2Vec model from the file
model_path = "models/trained_model.pkl"  # Adjust the path if necessary
model = Word2Vec.load(model_path)

# Example: Check the vocabulary
print("Vocabulary:", list(model.wv.index_to_key))

# Example: Get vector for a word
word = "technology"  # Replace with a word in your vocabulary
if word in model.wv:
    print(f"Vector for '{word}':\n{model.wv[word]}\n")
else:
    print(f"'{word}' is not in the model vocabulary.\n")

# Find and print similar words
try:
    similar_words = model.wv.most_similar("technology", topn=5)
    print("Most similar words to 'technology':")
    for similar_word, similarity in similar_words:
        print(f"  {similar_word}: {similarity:.4f}")
except KeyError as e:
    print(f"Error: {e} is not in the model vocabulary.")
