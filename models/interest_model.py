from gensim.models import Word2Vec
import pickle
import os

def train_interest_model():
    # Sample data for training
    sample_sentences = [
        ["technology", "sports", "travel"],
        ["music", "gaming", "cooking"],
        ["movies", "art", "science"],
        ["fitness", "yoga", "meditation"],
        ["reading", "writing", "blogging"]
    ]

    # Create Word2Vec model
    model = Word2Vec(vector_size=50, window=3, min_count=1, workers=4)
    model.build_vocab(sample_sentences)
    model.train(sample_sentences, total_examples=len(sample_sentences), epochs=10)

    # Ensure models directory exists
    os.makedirs("models", exist_ok=True)

    # Save model using pickle
    with open("models/trained_model.pkl", "wb") as f:
        pickle.dump(model, f)

    print("Interest model trained and saved as 'models/trained_model.pkl'.")

if __name__ == "__main__":
    train_interest_model()
    # Load the model to verify
    
