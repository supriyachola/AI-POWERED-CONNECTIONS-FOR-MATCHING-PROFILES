import pickle
import os

# Path to the .pkl file
model_path = "models/trained_model.pkl"

# Load model
if os.path.exists(model_path):
    with open(model_path, "rb") as f:
        model = pickle.load(f)
else:
    raise FileNotFoundError(f"Model not found at {model_path}")

# Example usage
print("Vocabulary:", list(model.wv.index_to_key))

word = "technology"
if word in model.wv:
    print(f"Vector for '{word}':\n{model.wv[word]}")
else:
    print(f"'{word}' is not in the model vocabulary.")

# Similar words
try:
    similar_words = model.wv.most_similar(word, topn=5)
    print("\nMost similar words to 'technology':")
    for w, sim in similar_words:
        print(f"  {w}: {sim:.4f}")
except KeyError:
    print(f"'{word}' not found in vocabulary.")

