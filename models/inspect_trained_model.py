import pickle
import os

# Path to the .pkl file
file_path = "models/trained_model.pkl"

# Check if the file exists
if not os.path.exists(file_path):
    print(f"File not found at {file_path}")
    exit()

# Try to load the file
try:
    with open(file_path, "rb") as file:
        data = pickle.load(file)
    print(f"Type of data: {type(data)}")
    print("Contents of the file:")
    print(data)
except Exception as e:
    print(f"An error occurred while loading the file: {e}")
