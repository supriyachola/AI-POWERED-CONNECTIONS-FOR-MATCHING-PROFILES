import pickle
import os

file_path = "models/trained_model.pkl"

if not os.path.exists(file_path):
    print(f"File not found at {file_path}")
    exit()

try:
    with open(file_path, "rb") as f:
        model = pickle.load(f)
    print(f"Type of loaded object: {type(model)}")
    print("Vocabulary:", list(model.wv.index_to_key))
except Exception as e:
    print(f"Failed to load model: {e}")
