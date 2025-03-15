from gensim.models import Word2Vec
import pickle

def train_interest_model():
    # Define some sample data for training
    sample_sentences = [
        ["technology", "sports", "travel"],
        ["music", "gaming", "cooking"],
        ["movies", "art", "science"]
    ]

    # Initialize the Word2Vec model
    model = Word2Vec(vector_size=50, window=3, min_count=1, workers=4)

    # Build vocabulary
    model.build_vocab(sample_sentences)

    # Train the model
    model.train(sample_sentences, total_examples=len(sample_sentences), epochs=10)

    # Save the model as .pkl
    with open("models/trained_model.pkl", "wb") as f:
        pickle.dump(model, f)
    print("Interest model trained and saved as 'trained_model.pkl'.")

if __name__ == "__main__":
    train_interest_model()

