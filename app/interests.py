import tkinter as tk
from tkinter import messagebox
import sqlite3
import os
from conferencing import start_video_conferencing
from matching import train_model

def open_interests_selection(username):
    interests_window = tk.Tk()
    interests_window.title("Interests Page")
    interests_window.geometry("400x400")
    interests_window.configure(bg="#7A397B")

    tk.Label(interests_window, text="Select your interests:", font=("Arial", 16, "bold"), fg="#ffffff", bg="#7A397B").pack(pady=10)

    interests = ["Technology", "Sports", "Travel", "Music", "Gaming", "Cooking", "Movies", "Art", "Science","fitness","yoga","meditation","reading","writing","blogging"]
    interest_vars = {interest: tk.BooleanVar() for interest in interests}

    for interest, var in interest_vars.items():
        tk.Checkbutton(interests_window, text=interest, variable=var, fg="#ffffff", bg="#7A397B", selectcolor="#4B0082").pack(anchor='w', padx=20, pady=2)

    def save_interests():
        selected_interests = [interest for interest, var in interest_vars.items() if var.get()]
        if not selected_interests:
            messagebox.showerror("Error", "Please select at least one interest!")
            return

        os.makedirs('database', exist_ok=True)
        conn = sqlite3.connect('database/users.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET interests = ? WHERE username = ?", (",".join(selected_interests), username))
        conn.commit()
        conn.close()

        messagebox.showinfo("Saved", "Your interests have been saved!")

        # Train or retrain model after saving interests
        train_model()

        interests_window.destroy()
        start_video_conferencing(username)

    tk.Button(interests_window, text="Save and Continue", command=save_interests, fg="#000000").pack(pady=10)
    interests_window.mainloop()

